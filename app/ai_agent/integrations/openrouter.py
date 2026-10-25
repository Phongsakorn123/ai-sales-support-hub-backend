"""OpenRouter access through LangChain's OpenAI-compatible chat model.

Prompts and model output are never logged, and neither is the API key.
"""

import asyncio
import logging
import random
import time
from typing import Any, Protocol, TypeVar

import openai
from langchain_core.exceptions import OutputParserException
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Only HTTP 429 is retried, a bounded number of times. Other errors fail fast.
MAX_RATE_LIMIT_RETRIES = 2
BACKOFF_BASE_SECONDS = 5.0  # waits ~5 s then ~10 s when the provider sends no Retry-After
MAX_RETRY_DELAY_SECONDS = 20.0
# Overall budget for one call including retries; keep it below the reverse proxy's read timeout
# (Nginx default 60 s) so the client gets a clean API error instead of a cut connection.
TOTAL_DEADLINE_SECONDS = 55.0
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMError(Exception):
    """Base class for failures the API turns into safe HTTP errors."""


class LLMNotConfiguredError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMUpstreamError(LLMError):
    pass


class LLMRateLimitedError(LLMUpstreamError):
    """The provider kept answering 429 after the allowed retries."""


class LLMOutputError(LLMError):
    """The model answered but not with usable structured output."""


class StructuredLLM(Protocol):
    model_name: str

    async def ainvoke_structured(
        self,
        prompt: ChatPromptTemplate,
        schema: type[SchemaT],
        variables: dict[str, Any],
        *,
        max_tokens: int,
    ) -> SchemaT: ...


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str,
        timeout_seconds: float,
        chat_model_factory=None,
    ) -> None:
        self._api_key = api_key
        self.model_name = model
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._factory = chat_model_factory or ChatOpenAI

    @classmethod
    def from_settings(cls) -> "OpenRouterClient":
        key = settings.openrouter_api_key
        model = (settings.openrouter_model or "").strip()
        if key is None or not key.get_secret_value().strip() or not model:
            raise LLMNotConfiguredError("OpenRouter API key or model is not configured")
        return cls(
            api_key=key,
            model=model,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.openrouter_timeout_seconds,
        )

    async def ainvoke_structured(
        self,
        prompt: ChatPromptTemplate,
        schema: type[SchemaT],
        variables: dict[str, Any],
        *,
        max_tokens: int,
    ) -> SchemaT:
        chat_model = self._factory(
            model=self.model_name,
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,  # retries are handled explicitly below
            max_tokens=max_tokens,
            temperature=0,
        )
        # Tool calling is the structured-output mode most OpenRouter models support.
        chain = prompt | chat_model.with_structured_output(schema, method="function_calling")
        attempt = 0
        deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeoutError("Total time budget exhausted")
            try:
                result = await asyncio.wait_for(chain.ainvoke(variables), timeout=min(self._timeout + 1, remaining))
                break
            except (OutputParserException, ValidationError) as error:
                logger.warning("OpenRouter returned unparsable output (%s)", type(error).__name__)
                raise LLMOutputError from error
            except (openai.RateLimitError, ValueError) as error:
                # OpenRouter can report provider failures as an HTTP 200 whose body holds an `error`
                # object; LangChain surfaces that as ValueError(<error dict>). Only code 429 is retried.
                if not isinstance(error, openai.RateLimitError):
                    code = _body_error_code(error)
                    if code != 429:
                        logger.error("OpenRouter returned an error body (code=%s)", code)
                        raise LLMUpstreamError from error
                if attempt >= MAX_RATE_LIMIT_RETRIES:
                    logger.warning("OpenRouter rate limit persisted after %d retries", attempt)
                    raise LLMRateLimitedError from error
                delay = _retry_delay(error, attempt)
                if delay >= deadline - time.monotonic():
                    logger.warning("OpenRouter rate limit; no time left to retry")
                    raise LLMRateLimitedError from error
                attempt += 1
                logger.warning("OpenRouter returned 429; retry %d in %.1fs", attempt, delay)
                await asyncio.sleep(delay)
            except (asyncio.TimeoutError, openai.APITimeoutError) as error:
                logger.warning("OpenRouter request timed out")
                raise LLMTimeoutError from error
            except Exception as error:
                # Log only the exception type: provider messages can echo request details.
                logger.error("OpenRouter request failed (%s)", type(error).__name__)
                raise LLMUpstreamError from error

        if isinstance(result, dict):
            try:
                result = schema.model_validate(result)
            except ValidationError as error:
                raise LLMOutputError from error
        if not isinstance(result, schema):
            raise LLMOutputError("Model returned no structured output")
        return result


def _body_error_code(error: ValueError) -> int | None:
    """HTTP-style code from an OpenRouter error body. Messages are never read or logged."""
    body = error.args[0] if error.args else None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _retry_delay(error: openai.RateLimitError, attempt: int) -> float:
    """Honor Retry-After (seconds) when present, otherwise exponential backoff with jitter."""
    header = getattr(getattr(error, "response", None), "headers", None)
    retry_after = header.get("retry-after") if header else None
    try:
        if retry_after is not None:
            return min(max(float(retry_after), 0.0), MAX_RETRY_DELAY_SECONDS)
    except ValueError:
        pass
    return min(BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 0.5), MAX_RETRY_DELAY_SECONDS)

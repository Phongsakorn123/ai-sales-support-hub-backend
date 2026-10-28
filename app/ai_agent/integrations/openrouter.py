"""OpenRouter access through LangChain's OpenAI-compatible chat model.

Prompts and model output are never logged, and neither is the API key.
"""

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, TypeVar

import openai
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from app.core.config import settings

logger = logging.getLogger(__name__)


MAX_RATE_LIMIT_RETRIES = 2
MAX_TIMEOUT_RETRIES = 2
BACKOFF_BASE_SECONDS = 5.0
MAX_RETRY_DELAY_SECONDS = 20.0
MAX_ATTEMPT_SECONDS = 18.0


TOTAL_DEADLINE_SECONDS = 55.0
MAX_AGENT_TOOL_ROUNDS = 8
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

    async def ainvoke_tool_agent(
        self,
        prompt: ChatPromptTemplate,
        schema: type[SchemaT],
        variables: dict[str, Any],
        *,
        tool_schemas: Sequence[dict[str, Any]],
        tool_handlers: Mapping[str, Callable[..., Any]],
        final_tool_name: str,
        required_tool_names: Sequence[str] = (),
        product_document_tool_name: str | None = None,
        exclusive_mode_tool_names: Sequence[str] = (),
        document_lookup_tool_name: str | None = None,
        recommendation_tool_name: str | None = None,
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
            max_retries=0,
            max_tokens=max_tokens,
            temperature=0,
        )

        chain = prompt | chat_model.with_structured_output(
            schema, method="function_calling"
        )
        deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS
        result = await self._ainvoke_with_retries(chain, variables, deadline)

        if isinstance(result, dict):
            try:
                result = schema.model_validate(result)
            except ValidationError as error:
                raise LLMOutputError from error
        if not isinstance(result, schema):
            raise LLMOutputError("Model returned no structured output")
        return result

    async def ainvoke_tool_agent(
        self,
        prompt: ChatPromptTemplate,
        schema: type[SchemaT],
        variables: dict[str, Any],
        *,
        tool_schemas: Sequence[dict[str, Any]],
        tool_handlers: Mapping[str, Callable[..., Any]],
        final_tool_name: str,
        required_tool_names: Sequence[str] = (),
        product_document_tool_name: str | None = None,
        exclusive_mode_tool_names: Sequence[str] = (),
        document_lookup_tool_name: str | None = None,
        recommendation_tool_name: str | None = None,
        max_tokens: int,
    ) -> SchemaT:
        """Run a bounded tool-call loop, then accept one schema-backed final tool call."""
        chat_model = self._factory(
            model=self.model_name,
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
            max_tokens=max_tokens,
            temperature=0,
        )
        final_schema = convert_to_openai_tool(schema)
        final_function_schema = final_schema.get("function", final_schema)
        final_function_schema["name"] = final_tool_name
        final_function_schema["description"] = (
            "Submit the validated final response after using the required search tools."
        )
        all_tools = [*tool_schemas, final_schema]
        messages = prompt.format_messages(**variables)
        deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS
        called_tools: set[str] = set()
        document_searches: set[int] = set()
        selected_mode_tool: str | None = None
        mode_product_ids: set[int] = set()

        for turn in range(MAX_AGENT_TOOL_ROUNDS):
            tool_choice: str | dict[str, Any] = "auto"
            if turn == MAX_AGENT_TOOL_ROUNDS - 1:
                tool_choice = {
                    "type": "function",
                    "function": {"name": final_tool_name},
                }
            bound_model = chat_model.bind_tools(all_tools, tool_choice=tool_choice)
            response = await self._ainvoke_with_retries(bound_model, messages, deadline)
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                raise LLMOutputError("Model did not call an available agent tool")
            messages.append(response)

            final_calls = [call for call in tool_calls if call.get("name") == final_tool_name]
            for call in tool_calls:
                name = call.get("name")
                if name == final_tool_name:
                    continue
                call_id = call.get("id")
                args = call.get("args")
                handler = tool_handlers.get(name)
                if not isinstance(call_id, str) or not isinstance(args, dict):
                    continue
                if (
                    name in exclusive_mode_tool_names
                    and selected_mode_tool is not None
                    and name != selected_mode_tool
                ):
                    messages.append(
                        ToolMessage(
                            content=(
                                "You already selected one request mode. Do not call both mode tools; "
                                f"continue using {selected_mode_tool}."
                            ),
                            tool_call_id=call_id,
                        )
                    )
                    continue
                if handler is None:
                    result: Any = {"error": "This tool is not available."}
                else:
                    try:
                        if name in exclusive_mode_tool_names:
                            selected_mode_tool = name
                        logger.info("AI agent called tool: %s", name)
                        result = handler(**args)
                        called_tools.add(name)
                        if name in exclusive_mode_tool_names:
                            mode_product_ids.update(_extract_product_ids(result))
                        if name == product_document_tool_name:
                            product_id = args.get("product_id")
                            if isinstance(product_id, int) and not isinstance(product_id, bool):
                                document_searches.add(product_id)
                    except Exception as error:
                        logger.warning("Agent tool failed (%s)", type(error).__name__)
                        result = {"error": "The tool could not complete this search."}
                messages.append(
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False, default=str),
                        tool_call_id=call_id,
                    )
                )

            if not final_calls:
                continue
            if len(final_calls) != 1:
                raise LLMOutputError("Model submitted the final result more than once")

            final_call = final_calls[0]
            final_call_id = final_call.get("id")
            args = final_call.get("args")
            if not isinstance(final_call_id, str) or not isinstance(args, dict):
                raise LLMOutputError("Model submitted an invalid final tool call")
            try:
                result = schema.model_validate(args)
            except ValidationError:
                messages.append(
                    ToolMessage(
                        content="The final result did not match the required schema. Submit it again with valid fields.",
                        tool_call_id=final_call_id,
                    )
                )
                continue

            missing_tools = set(required_tool_names) - called_tools
            recommended_ids = {
                item.product_id for item in getattr(result, "recommendations", [])
            }
            mode_errors: list[str] = []
            if exclusive_mode_tool_names and selected_mode_tool is None:
                mode_errors.append(
                    "Choose exactly one mode tool before submitting: one document lookup or product recommendation."
                )
            elif selected_mode_tool == document_lookup_tool_name:
                if mode_product_ids and len(recommended_ids) != 1:
                    mode_errors.append(
                        "Document lookup must return exactly one product."
                    )
                if recommended_ids - mode_product_ids:
                    mode_errors.append(
                        "Document lookup can return only the product selected by lookup_product_documents."
                    )
            elif selected_mode_tool == recommendation_tool_name:
                if recommended_ids - mode_product_ids:
                    mode_errors.append(
                        "Recommendations must use only products returned by recommend_products."
                    )
            missing_document_searches = (
                recommended_ids - document_searches
                if product_document_tool_name
                and selected_mode_tool != document_lookup_tool_name
                else set()
            )
            if missing_tools or missing_document_searches or mode_errors:
                missing = list(mode_errors)
                if missing_tools:
                    missing.append(f"Call these tools before finalizing: {sorted(missing_tools)}.")
                if missing_document_searches:
                    missing.append(
                        "Search documents for each recommended product_id before finalizing: "
                        f"{sorted(missing_document_searches)}."
                    )
                messages.append(
                    ToolMessage(
                        content=" ".join(missing),
                        tool_call_id=final_call_id,
                    )
                )
                continue
            return result

        raise LLMOutputError("Agent reached the maximum number of tool-call rounds")

    async def _ainvoke_with_retries(self, runnable, input_value, deadline):
        rate_limit_retries = 0
        timeout_retries = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeoutError("Total time budget exhausted")
            try:
                return await asyncio.wait_for(
                    runnable.ainvoke(input_value),
                    timeout=min(self._timeout + 1, MAX_ATTEMPT_SECONDS, remaining),
                )
            except (OutputParserException, ValidationError) as error:
                logger.warning(
                    "OpenRouter returned unparsable output (%s)", type(error).__name__
                )
                raise LLMOutputError from error
            except (openai.RateLimitError, ValueError) as error:
                if not isinstance(error, openai.RateLimitError):
                    code = _body_error_code(error)
                    if code != 429:
                        logger.error("OpenRouter returned an error body (code=%s)", code)
                        raise LLMUpstreamError from error
                if rate_limit_retries >= MAX_RATE_LIMIT_RETRIES:
                    raise LLMRateLimitedError from error
                delay = _retry_delay(error, rate_limit_retries)
                if delay >= deadline - time.monotonic():
                    raise LLMRateLimitedError from error
                rate_limit_retries += 1
                logger.warning(
                    "OpenRouter returned 429; retry %d in %.1fs",
                    rate_limit_retries,
                    delay,
                )
                await asyncio.sleep(delay)


            except (asyncio.TimeoutError, openai.APITimeoutError) as error:
                if timeout_retries >= MAX_TIMEOUT_RETRIES:
                    raise LLMTimeoutError from error
                delay = min(1.0 + random.uniform(0, 0.5), MAX_RETRY_DELAY_SECONDS)
                if delay >= deadline - time.monotonic():
                    raise LLMTimeoutError from error
                timeout_retries += 1
                logger.warning(
                    "OpenRouter request timed out; retry %d in %.1fs",
                    timeout_retries,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as error:
                logger.error("OpenRouter request failed (%s)", type(error).__name__)
                raise LLMUpstreamError from error


def _extract_product_ids(value: Any) -> set[int]:
    """Collect product IDs returned by a selected mode tool."""
    found: set[int] = set()
    if isinstance(value, Mapping):
        product_id = value.get("product_id")
        if isinstance(product_id, int) and not isinstance(product_id, bool):
            found.add(product_id)
        for nested in value.values():
            found.update(_extract_product_ids(nested))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            found.update(_extract_product_ids(nested))
    return found


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
    return min(
        BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 0.5),
        MAX_RETRY_DELAY_SECONDS,
    )

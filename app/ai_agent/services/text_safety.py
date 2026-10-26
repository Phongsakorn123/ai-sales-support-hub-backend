import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_DELIMITERS = re.compile(
    r"</?\s*(customer_requirements|candidate_products)\b[^>]*>", re.IGNORECASE
)


def neutralize(text: str) -> str:
    return _DELIMITERS.sub(" ", _CONTROL.sub("", text)).strip()

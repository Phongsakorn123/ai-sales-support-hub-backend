"""Deterministic requirement extraction.

Budget, currency, category, brand and a minimum RAM are pulled out of the request with plain Python so
the database filters never depend on a model call. The model only ranks and explains the candidates.
"""

import re
from dataclasses import dataclass, field

from app.ai_agent.schemas.llm_outputs import ExtractedRequirements
from app.backend.models.product import Product

_NUMBER = r"(\d[\d,]*(?:\.\d+)?)(?!\d)\s*(k\b)?"
_NOT_A_SPEC_UNIT = (
    r"(?!\s*(?:gb|tb|mb|hz|ghz|inch|in\b|\"|w\b|mah|mp\b|ms\b|nm\b|cores?))"
)
_CURRENCY = r"(thb|baht|usd|dollars?|บาท|฿|\$)"
_BUDGET_KEYWORD = re.compile(
    r"(?:under|below|less than|up to|within|at most|no more than|not more than|not exceeding|max(?:imum)?|"
    r"budget(?: is| of)?|ไม่เกิน|งบประมาณ|งบ|ต่ำกว่า)\s*:?\s*(?:฿|\$)?\s*"
    + _NUMBER
    + _NOT_A_SPEC_UNIT
    + r"\s*"
    + _CURRENCY
    + r"?",
    re.IGNORECASE,
)
_BUDGET_CURRENCY = re.compile(
    r"(?:฿|\$)\s*" + _NUMBER + r"|" + _NUMBER + r"\s*" + _CURRENCY, re.IGNORECASE
)
_RAM_AFTER = re.compile(
    r"(\d+(?:\.\d+)?)\s*(gb|g)\b\s*(?:of\s+)?(?:ram|memory|แรม|หน่วยความจำ)",
    re.IGNORECASE,
)
_RAM_BEFORE = re.compile(
    r"(?:ram|memory|แรม|หน่วยความจำ)\s*(?:of\s+)?(?:at least\s+)?(?:>=\s*)?(\d+(?:\.\d+)?)\s*(gb|g)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z][a-z0-9+\-]{2,}")

CURRENCY_CODES = {
    "thb": "THB",
    "baht": "THB",
    "บาท": "THB",
    "฿": "THB",
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "$": "USD",
}


SYNONYMS = {
    "notebook": "Laptop",
    "ultrabook": "Laptop",
    "macbook": "Laptop",
    "โน้ตบุ๊ก": "Laptop",
    "โน้ตบุ๊ค": "Laptop",
    "แล็ปท็อป": "Laptop",
    "แล็บท็อป": "Laptop",
    "แลปทอป": "Laptop",
    "headset": "Headphones",
    "earphones": "Headphones",
    "หูฟัง": "Headphones",
    "display": "Monitor",
    "screen": "Monitor",
    "จอ": "Monitor",
    "จอคอม": "Monitor",
    "เมาส์": "Mouse",
    "mice": "Mouse",
    "คีย์บอร์ด": "Keyboard",
    "เราเตอร์": "Router",
    "สวิตช์": "Switch",
    "ไฟร์วอลล์": "Firewall",
    "เซิร์ฟเวอร์": "Server",
}
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "any",
    "need",
    "needs",
    "want",
    "wants",
    "looking",
    "look",
    "please",
    "customer",
    "client",
    "some",
    "something",
    "good",
    "best",
    "about",
    "have",
    "has",
    "under",
    "over",
    "than",
    "least",
    "more",
    "less",
    "can",
    "will",
    "would",
    "should",
    "who",
    "from",
    "into",
    "also",
    "budget",
    "price",
    "cheap",
    "around",
    "approximately",
    "required",
    "require",
    "requires",
    "ram",
    "memory",
    "thb",
    "usd",
    "baht",
}


@dataclass
class RuleExtraction:
    categories: list[str] = field(default_factory=list)
    brand: str | None = None
    max_budget: float | None = None
    currency: str | None = None
    min_ram_gb: float | None = None
    keywords: list[str] = field(default_factory=list)


def _mentions(text: str, term: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(term.lower())}(?:s|es)?(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _amount(number: str, k_suffix: str | None) -> float | None:
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    return value * 1000 if k_suffix else value


def _currency(token: str | None) -> str | None:
    return CURRENCY_CODES.get(token.lower()) if token else None


def _find_budget(text: str) -> tuple[float | None, str | None]:
    match = _BUDGET_KEYWORD.search(text)
    if match:
        symbol = (
            "฿" if "฿" in match.group(0) else "$" if "$" in match.group(0) else None
        )
        return _amount(match.group(1), match.group(2)), _currency(
            match.group(3) or symbol
        )
    match = _BUDGET_CURRENCY.search(text)
    if match:
        symbol_first = match.group(1) is not None
        number, k_suffix = (
            (match.group(1), match.group(2))
            if symbol_first
            else (match.group(3), match.group(4))
        )
        token = match.group(0)[0] if symbol_first else match.group(5)
        return _amount(number, k_suffix), _currency(token)
    return None, None


def _find_min_ram(text: str) -> float | None:
    match = _RAM_AFTER.search(text) or _RAM_BEFORE.search(text)
    return float(match.group(1)) if match else None


def extract_by_rules(
    requirements: str, taxonomy: dict[str, list[str]]
) -> RuleExtraction:
    text = requirements.lower()
    result = RuleExtraction()

    types = {t.lower(): t for t in taxonomy["types"]}
    categories = {c.lower(): c for c in taxonomy["categories"]}
    matched = [t for key, t in types.items() if _mentions(text, key)]
    for word, target in SYNONYMS.items():
        if (
            word in text
            and target.lower() in types
            and types[target.lower()] not in matched
        ):
            matched.append(types[target.lower()])
    if not matched:
        matched = [c for key, c in categories.items() if _mentions(text, key)]
    result.categories = matched

    result.brand = next((b for b in taxonomy["brands"] if _mentions(text, b)), None)
    result.max_budget, result.currency = _find_budget(text)
    result.min_ram_gb = _find_min_ram(text)

    known = {*types, *categories, *(b.lower() for b in taxonomy["brands"])}
    words = [
        w
        for w in dict.fromkeys(_WORD.findall(text))
        if w not in STOPWORDS and w not in known
    ]
    result.keywords = [*(m.lower() for m in matched), *words][:8]
    return result


def to_extracted(rules: RuleExtraction) -> ExtractedRequirements:
    features = [f"At least {rules.min_ram_gb:g}GB RAM"] if rules.min_ram_gb else []
    return ExtractedRequirements(
        category=rules.categories[0] if rules.categories else None,
        max_budget=rules.max_budget,
        currency=rules.currency,
        required_features=features,
        keywords=rules.keywords,
        preferred_brand=rules.brand,
    )


_RAM_KEYS = ("ram", "memory")
_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb)", re.IGNORECASE)


def product_ram_gb(product: Product) -> float | None:
    """RAM in GB from the catalog specification rows, or None when it is absent or unparseable."""
    for spec in product.specifications:
        if any(key in spec["key"].lower() for key in _RAM_KEYS):
            match = _SIZE.search(spec["value"])
            if match:
                factor = {"tb": 1024, "gb": 1, "mb": 1 / 1024}[match.group(2).lower()]
                return float(match.group(1)) * factor
    return None

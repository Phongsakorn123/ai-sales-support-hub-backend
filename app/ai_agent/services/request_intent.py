"""Deterministic intent routing for product recommendations and document lookup."""

import re

from app.ai_agent.services.requirements import SearchCriteria

_DOCUMENT_TERMS = re.compile(
    r"เอกสาร|คู่มือ|สิ่งพิมพ์|รายงาน|\b(?:documents?|manuals?|publications?|datasheets?|"
    r"valuation|analysis|reports?)\b",
    re.IGNORECASE,
)
_RECOMMENDATION_TERMS = re.compile(
    r"แนะนำ|ช่วยเลือก|เลือกสินค้า|\b(?:recommend(?:ation)?s?|suggest(?:ion)?s?|"
    r"which product|products? to buy)\b",
    re.IGNORECASE,
)
_DOCUMENT_SEARCH_ACTIONS = re.compile(
    r"ค้นหา|ค้นเอกสาร|ค้นคู่มือ|ช่วยหาเอกสาร|หาเอกสาร|หาคู่มือ|เปิดดู|ดูเอกสาร|"
    r"\b(?:search|find|look\s+up|retrieve|open|view|get)\b",
    re.IGNORECASE,
)
_DOCUMENT_RELATIONS = re.compile(
    r"เอกสาร.{0,24}(?:ของ|สำหรับ|เกี่ยวกับ)|คู่มือ.{0,24}(?:ของ|สำหรับ)|"
    r"\b(?:documents?|manuals?|publications?|reports?)\b.{0,24}\b(?:for|of|about)\b",
    re.IGNORECASE,
)
_RECOMMENDATION_WITH_DOCUMENTS = re.compile(
    r"\b(?:with|include|including|along\s+with)\b.{0,24}\b(?:documents?|manuals?|"
    r"publications?|reports?)\b|(?:พร้อม|แนบ).{0,16}(?:เอกสาร|คู่มือ|สิ่งพิมพ์|รายงาน)",
    re.IGNORECASE,
)


def is_document_lookup_request(
    requirements: str, criteria: SearchCriteria
) -> bool:
    """Route explicit document retrieval to one product; recommendations stay multi-item."""
    if _RECOMMENDATION_TERMS.search(requirements):
        return False
    if not _DOCUMENT_TERMS.search(requirements):
        return False
    if (
        _RECOMMENDATION_WITH_DOCUMENTS.search(requirements)
        and not _DOCUMENT_SEARCH_ACTIONS.search(requirements)
    ):
        return False
    if _DOCUMENT_SEARCH_ACTIONS.search(requirements):
        return True
    if _DOCUMENT_RELATIONS.search(requirements):
        return True

    # A document noun paired with a named brand or model is a lookup request.
    named_product = bool(criteria.brand_filter) or any(
        keyword.casefold() not in {
            "laptop", "notebook", "desktop", "computer", "monitor", "server",
            "keyboard", "mouse", "router", "switch",
        }
        for keyword in criteria.keywords
    )
    return named_product

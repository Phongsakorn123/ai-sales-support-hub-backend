"""Small reproducible keyword-vs-hybrid ranking evaluation using the configured local model."""

import json
from pathlib import Path

from app.ai_agent.services.embeddings import embed_text
from app.ai_agent.services.requirements import build_criteria
from app.ai_agent.services.retrieval import _score, reciprocal_rank_fusion

CATALOG = [
    ("TRAVEL-LAPTOP", "Computer", "Laptop", "Lightweight portable notebook designed for business travel, client visits and mobile office work."),
    ("GAMING-LAPTOP", "Computer", "Laptop", "Laptop for gaming with a dedicated graphics card and a high refresh display."),
    ("OFFICE-LAPTOP", "Computer", "Laptop", "Laptop for office productivity, spreadsheets and email."),
    ("CREATIVE-LAPTOP", "Computer", "Laptop", "Laptop computer. 32GB memory. Suitable for video editing, digital artwork, photo editing and creative applications."),
    ("CALL-HEADSET", "Peripherals", "Headphones", "Wireless headset with microphone for video calls, online meetings and conference calls."),
    ("OFFICE-MONITOR", "Peripherals", "Monitor", "Large desktop display for office productivity and spreadsheets."),
]
TAXONOMY = {
    "categories": ["Computer", "Peripherals"],
    "types": ["Laptop", "Headphones", "Monitor"],
    "brands": [],
}
TOP_K = 3


def recall_at_k(ranked: list[str], relevant: set[str]) -> float:
    return len(set(ranked[:TOP_K]) & relevant) / len(relevant)


def reciprocal_rank_at_k(ranked: list[str], relevant: set[str]) -> float:
    return next((1 / rank for rank, sku in enumerate(ranked[:TOP_K], start=1) if sku in relevant), 0.0)


def main() -> None:
    dataset = json.loads((Path(__file__).with_name("hybrid_search.json")).read_text(encoding="utf-8"))
    totals = {"keyword_recall": 0.0, "keyword_mrr": 0.0, "hybrid_recall": 0.0, "hybrid_mrr": 0.0}
    examples = [
        {**example, "query": query}
        for example in dataset
        for query in (example["query"], example["query_th"])
    ]
    for example in examples:
        query = example["query"]
        relevant = set(example["relevant_skus"])
        criteria = build_criteria(query, TAXONOMY)
        old_terms = [*criteria.parsed.required_features, *criteria.keywords]
        if criteria.parsed.intended_use:
            old_terms.append(criteria.parsed.intended_use)
        query_vector = embed_text(query)
        semantic_scores = []
        keyword_scores = []
        for sku, category, product_type, text in CATALOG:
            searchable_text = f"{category} {product_type} {text}"
            category_match = not criteria.category_terms or any(
                value and value.casefold() in {term.casefold() for term in criteria.category_terms}
                for value in (category, product_type)
            )
            lexical = _score(searchable_text.lower(), old_terms)
            if category_match and (lexical > 0 or criteria.category_terms):
                keyword_scores.append((sku, lexical))
            vector = embed_text(searchable_text)
            cosine = sum(left * right for left, right in zip(query_vector, vector))
            semantic_scores.append((sku, cosine))
        keyword_scores.sort(key=lambda item: -item[1])
        semantic_scores.sort(key=lambda item: -item[1])
        fused = reciprocal_rank_fusion(
            [sku for sku, _ in keyword_scores], semantic_scores,
            k=60, keyword_weight=1.0, semantic_weight=1.0,
        )
        hybrid = sorted(fused, key=lambda sku: (-float(fused[sku]["rrf_score"]), sku))
        keyword = [sku for sku, _ in keyword_scores]
        totals["keyword_recall"] += recall_at_k(keyword, relevant)
        totals["keyword_mrr"] += reciprocal_rank_at_k(keyword, relevant)
        totals["hybrid_recall"] += recall_at_k(hybrid, relevant)
        totals["hybrid_mrr"] += reciprocal_rank_at_k(hybrid, relevant)
        print(f"{query} | keyword={keyword[:TOP_K]} | hybrid={hybrid[:TOP_K]} | expected={sorted(relevant)}")
    count = len(examples)
    print(f"queries={count}, k={TOP_K}")
    print(f"keyword Recall@{TOP_K}={totals['keyword_recall'] / count:.3f} MRR@{TOP_K}={totals['keyword_mrr'] / count:.3f}")
    print(f"hybrid  Recall@{TOP_K}={totals['hybrid_recall'] / count:.3f} MRR@{TOP_K}={totals['hybrid_mrr'] / count:.3f}")


if __name__ == "__main__":
    main()

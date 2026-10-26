"""Generate mock supporting documents (Markdown files + SQLite metadata) for existing products.

Usage (from the project root):
    python -m scripts.seed_documents              # create missing files and records
    python -m scripts.seed_documents --dry-run    # report only, write nothing

Safe to re-run: existing records are skipped, existing files are never overwritten, and nothing is
deleted. A record whose file has gone missing gets its file regenerated. Products added later are
picked up on the next run. All generated content is MOCK DEMO DATA derived from catalog fields.
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.backend.models.document import Document
from app.backend.models.product import Product
from app.core.config import settings
from app.db.session import SessionLocal

DOCUMENT_VERSION = "1.0"
VALIDATION_CATEGORIES = {"Laboratory", "Monitoring"}
NOTICE = (
    "> **MOCK / DEMO DATA.** This document was generated automatically from the product catalog "
    "for demonstration. It is not an official vendor document, has not been reviewed or approved, "
    "and must not be used for purchasing, safety, compliance or technical decisions."
)

DOCUMENT_TYPES = {
    "datasheet": "Datasheet",
    "product_manual": "Product Manual",
    "compatibility_guide": "Compatibility Guide",
    "brochure": "Brochure",
    "validation_report": "Validation Checklist",
}


@dataclass
class Report:
    files_created: int = 0
    files_skipped: int = 0
    records_created: int = 0
    records_skipped: int = 0
    failed: list[str] = field(default_factory=list)
    created_ids: list[str] = field(default_factory=list)


def _bullets(values: list[str]) -> str:
    return (
        "\n".join(f"- {value}" for value in values)
        if values
        else "- Not specified in the catalog."
    )


def _specs_table(product: Product) -> str:
    if not product.specifications:
        return "_No specifications are recorded in the catalog._"
    rows = "\n".join(f"| {s['key']} | {s['value']} |" for s in product.specifications)
    return f"| Specification | Value |\n|---|---|\n{rows}"


def _header(doc_id: str, title: str, doc_type: str, product: Product) -> str:
    return (
        f"# {title}\n\n{NOTICE}\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Document ID | {doc_id} |\n"
        f"| Document type | {doc_type} |\n"
        f"| Version | {DOCUMENT_VERSION} |\n"
        f"| Status | mock_demo (not approved) |\n"
        f"| Product | {product.name} |\n"
        f"| Product ID | {product.id} |\n"
        f"| SKU | {product.sku or 'n/a'} |\n\n"
    )


def _identity(product: Product) -> str:
    lines = [
        f"- **Brand:** {product.brand or 'n/a'}",
        f"- **Model:** {product.model or 'n/a'}",
        f"- **Category / type:** {product.category or 'n/a'} / {product.type or 'n/a'}"
        + (f" / {product.sub_type}" if product.sub_type else ""),
    ]
    if product.warranty_months is not None:
        lines.append(
            f"- **Warranty (catalog value):** {product.warranty_months} months"
        )
    return "\n".join(lines)


def build_datasheet(doc_id: str, product: Product) -> str:
    return (
        _header(doc_id, f"{product.name} — Datasheet", "datasheet", product)
        + f"## Overview\n\n{product.description or 'No description in the catalog.'}\n\n"
        + f"## Identification\n\n{_identity(product)}\n\n"
        + f"## Specifications\n\n{_specs_table(product)}\n\n"
        + f"## Key features\n\n{_bullets(product.features)}\n\n"
        + f"## Tags\n\n{', '.join(product.tags) or 'None'}\n\n"
        + "## Limitations\n\n"
        + f"{product.limitations or 'No limitations are recorded in the catalog.'}\n"
    )


def build_manual(doc_id: str, product: Product) -> str:
    return (
        _header(
            doc_id, f"{product.name} — Product Manual (Demo)", "product_manual", product
        )
        + "## About this manual\n\nThis demo manual summarizes how the catalog describes "
        f"the {product.name}. Step-by-step setup instructions are intentionally not included "
        "because they are not part of the catalog data; refer to the manufacturer's official manual.\n\n"
        + f"## Product summary\n\n{product.description or 'No description in the catalog.'}\n\n"
        + f"## Intended use cases\n\n{_bullets(product.use_cases)}\n\n"
        + f"## Features to be aware of\n\n{_bullets(product.features)}\n\n"
        + f"## Recorded specifications\n\n{_specs_table(product)}\n\n"
        + "## Known limitations\n\n"
        + f"{product.limitations or 'No limitations are recorded in the catalog.'}\n\n"
        + f"## Warranty\n\n"
        + (
            f"The catalog records a warranty of {product.warranty_months} months. "
            "Actual terms must be confirmed with the seller.\n"
            if product.warranty_months is not None
            else "No warranty period is recorded in the catalog.\n"
        )
    )


def build_compatibility(doc_id: str, product: Product) -> str:
    connectivity = [
        f"- **{s['key']}:** {s['value']}"
        for s in product.specifications
        if re.search(
            r"connect|port|interface|ethernet|wi-?fi|usb|hdmi|bluetooth|wireless",
            s["key"] + " " + s["value"],
            re.I,
        )
    ]
    return (
        _header(
            doc_id,
            f"{product.name} — Compatibility Guide (Demo)",
            "compatibility_guide",
            product,
        )
        + "## Scope\n\nCompatibility with specific operating systems, devices or software has "
        "**not been verified** for this demo. The notes below only restate connectivity-related "
        "specifications recorded in the catalog.\n\n"
        + "## Connectivity-related specifications\n\n"
        + (
            "\n".join(connectivity)
            if connectivity
            else "- No connectivity specifications are recorded in the catalog."
        )
        + f"\n\n## Suitable environments (from catalog use cases)\n\n{_bullets(product.use_cases)}\n\n"
        + f"## Related tags\n\n{', '.join(product.tags) or 'None'}\n\n"
        + "## Verification\n\nBefore deployment, confirm compatibility with the manufacturer or "
        "an authorized reseller.\n"
    )


def build_brochure(doc_id: str, product: Product) -> str:
    return (
        _header(doc_id, f"{product.name} — Brochure", "brochure", product)
        + f"## {product.name}\n\n{product.description or ''}\n\n"
        + f"### Why customers choose it\n\n{product.key_benefits or 'Benefits are not recorded in the catalog.'}\n\n"
        + f"### Highlights\n\n{_bullets(product.features)}\n\n"
        + f"### Designed for\n\n{_bullets(product.target_customers)}\n\n"
        + f"### Typical uses\n\n{_bullets(product.use_cases)}\n\n"
        + f"### Good to know\n\n{product.limitations or 'No limitations are recorded in the catalog.'}\n"
    )


def build_validation(doc_id: str, product: Product) -> str:
    return (
        _header(
            doc_id,
            f"{product.name} — Validation Checklist (Demo Template)",
            "validation_report",
            product,
        )
        + "## Purpose\n\nThis is a blank, illustrative validation checklist. **No tests have been "
        "performed and no results exist.** It only shows how a validation record could be organized.\n\n"
        + "## Items to verify (from recorded specifications)\n\n"
        + "| Specification | Recorded value | Result |\n|---|---|---|\n"
        + (
            "\n".join(
                f"| {s['key']} | {s['value']} | Not performed (demo) |"
                for s in product.specifications
            )
            or "| — | — | Not performed (demo) |"
        )
        + "\n\n## Sign-off\n\nNo reviewer or approver. Status: mock_demo.\n"
    )


BUILDERS = {
    "datasheet": build_datasheet,
    "product_manual": build_manual,
    "compatibility_guide": build_compatibility,
    "brochure": build_brochure,
    "validation_report": build_validation,
}


def document_types_for(product: Product) -> list[str]:
    types = ["datasheet", "product_manual", "compatibility_guide", "brochure"]
    if product.category in VALIDATION_CATEGORIES:
        types.append("validation_report")
    return types


def _doc_number(value: str) -> int:
    match = re.fullmatch(r"DOC-(\d+)", value)
    return int(match.group(1)) if match else 0


def _write_new(path: Path, content: str) -> bool:
    """Create the file only if it does not exist. Returns True if written."""
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(content)
        return True
    except FileExistsError:
        return False


def seed_documents(
    session: Session, storage_dir: Path, *, dry_run: bool = False
) -> Report:
    report = Report()
    storage_dir = storage_dir.resolve()
    if not dry_run:
        storage_dir.mkdir(parents=True, exist_ok=True)

    existing = {
        (d.product_id, d.document_type): d
        for d in session.scalars(select(Document).where(Document.is_mock.is_(True)))
    }
    numbers = [_doc_number(i) for i in session.scalars(select(Document.id))]
    if storage_dir.exists():
        numbers += [_doc_number(p.stem) for p in storage_dir.glob("DOC-*.md")]
    next_number = max(numbers, default=0) + 1

    products = session.scalars(
        select(Product)
        .options(
            selectinload(Product.specification_rows),
            selectinload(Product.feature_rows),
            selectinload(Product.tag_rows),
            selectinload(Product.target_customer_rows),
            selectinload(Product.use_case_rows),
        )
        .order_by(Product.id)
    ).all()

    for product in products:
        for doc_type in document_types_for(product):
            record = existing.get((product.id, doc_type))
            try:
                if record is not None:
                    report.records_skipped += 1
                    path = storage_dir / record.file_path
                    if path.exists():
                        report.files_skipped += 1
                    elif not dry_run:

                        if _write_new(path, BUILDERS[doc_type](record.id, product)):
                            report.files_created += 1
                    else:
                        report.files_created += 1
                    continue

                doc_id = f"DOC-{next_number:04d}"
                next_number += 1
                filename = f"{doc_id}.md"
                title = f"{product.name} — {DOCUMENT_TYPES[doc_type]}"
                if dry_run:
                    report.files_created += 1
                    report.records_created += 1
                    continue
                if not _write_new(
                    storage_dir / filename, BUILDERS[doc_type](doc_id, product)
                ):
                    report.files_skipped += 1
                    report.failed.append(
                        f"{doc_id}: file already exists, record not created"
                    )
                    continue
                try:
                    session.add(
                        Document(
                            id=doc_id,
                            product_id=product.id,
                            title=title,
                            document_type=doc_type,
                            description=(
                                f"Mock demo {DOCUMENT_TYPES[doc_type].lower()} for {product.name}, "
                                "generated from catalog data."
                            ),
                            file_path=filename,
                            file_format="md",
                            version=DOCUMENT_VERSION,
                            approval_status="mock_demo",
                            is_mock=True,
                        )
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                    (storage_dir / filename).unlink(missing_ok=True)
                    raise
                report.files_created += 1
                report.records_created += 1
                report.created_ids.append(doc_id)
            except Exception as error:
                report.failed.append(
                    f"product {product.id} {doc_type}: {type(error).__name__}"
                )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would happen"
    )
    args = parser.parse_args()
    print(f"Storage directory: {settings.document_storage_dir}")
    with SessionLocal() as session:
        report = seed_documents(
            session, settings.document_storage_dir, dry_run=args.dry_run
        )
    label = "would create" if args.dry_run else "created"
    print(
        f"Records {label}: {report.records_created}, skipped: {report.records_skipped}"
    )
    print(f"Files   {label}: {report.files_created}, skipped: {report.files_skipped}")
    print(f"Failed: {len(report.failed)}")
    for failure in report.failed:
        print(f"  - {failure}", file=sys.stderr)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())

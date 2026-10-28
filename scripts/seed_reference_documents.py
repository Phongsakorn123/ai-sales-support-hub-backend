"""Seed curated official publisher links for catalog products.

The local Markdown file is an original short summary and link, not a copy of the
publisher's copyrighted document. Safe to rerun; records are matched by product
and source URL. External material remains in_review pending internal validation.
"""
from pathlib import Path
import re
from urllib.parse import quote_plus

from sqlalchemy import select
from app.backend.models.document import Document
from app.backend.models.product import Product
from app.core.config import settings
from app.db.session import SessionLocal

REFERENCES = [
    ("MOCK-COM-001", "Lenovo", "ThinkPad T14 Gen 5 (Intel) Specifications", "datasheet", "https://psref.lenovo.com/syspool/Sys/PDF/ThinkPad/ThinkPad_T14_Gen_5_Intel/ThinkPad_T14_Gen_5_Intel_Spec.pdf", "Official Lenovo PSREF specification PDF for the ThinkPad T14 Gen 5 Intel family. Confirm the exact machine type and configured options against the customer quote."),
    ("MOCK-COM-002", "Dell", "OptiPlex 7020 Small Form Factor Owner's Manual", "product_manual", "https://downloads.dell.com/topicspdf/optiplex-7020-desktop_Owners-Manual2_en-us.pdf", "Dell owner documentation for the OptiPlex 7020 desktop family. Check the exact chassis and configuration before advising on ports, service, or upgrades."),
    ("MOCK-LPT-001", "Apple", "MacBook Air (M2, 2022) Technical Specifications", "datasheet", "https://support.apple.com/en-ie/111867", "Apple technical specifications for the 13-inch MacBook Air with M2. Verify memory, storage, region, and current availability for the quoted configuration."),
    ("MOCK-LPT-005", "Microsoft", "Surface Laptop (7th Edition) Features and Configuration", "publication", "https://support.microsoft.com/en-us/surface/models/surface-laptop-7th-edition-features", "Microsoft support reference for Surface Laptop (7th Edition) features. Confirm processor, display, ports, and configuration for the exact SKU."),
    ("MOCK-NET-001", "Cisco", "Catalyst 1000 Series Switches Datasheet", "datasheet", "https://www.cisco.com/c/en/us/products/collateral/switches/catalyst-1000-series-switches/nb-06-cat1k-ser-switch-ds-cte-en.pdf", "Cisco product-family datasheet. Validate exact Catalyst 1000 model, port count, PoE variant, licensing, and regional availability."),
    ("MOCK-NET-002", "MikroTik", "RB5009UG+S+IN Product and Suggested Price Reference", "valuation_report", "https://mikrotik.com/product/rb5009ug_s_in", "Official MikroTik product page and vendor suggested price reference only. This is not an independent valuation, appraisal, or local reseller quote; verify tax, region, and current pricing."),
    ("MOCK-NET-003", "Ubiquiti", "UniFi U6 Pro Technical Specifications", "datasheet", "https://techspecs.ui.com/unifi/wifi/u6-pro", "Official Ubiquiti technical specifications. Confirm regulatory region, supported features, and controller requirements for the proposed deployment."),
    ("MOCK-LAB-001", "RIGOL", "DP800 Series User Guide (includes DP832)", "product_manual", "https://download.rigol.com/en/Manual/DC%20Power%26Loads/DP800/DP800_UserGuide_EN.pdf", "RIGOL DP800 series user guide covering the DP832 family. Check the exact model suffix, voltage/current ratings, and safety requirements."),
    ("MOCK-MNT-001", "Aqara", "Temperature and Humidity Sensor User Manual Library", "product_manual", "https://www.aqara.com/eu/support/user-manual/?cat_id=Controller", "Official Aqara manual library. Select the exact sensor generation and region; protocol, hub compatibility, and package contents can differ."),
    ("MOCK-PER-001", "Logitech", "MX Keys S Getting Started", "product_manual", "https://support.logi.com/hc/en-001/articles/13750828691991-Getting-Started-MX-Keys-S", "Official Logitech setup reference for MX Keys S. Confirm keyboard layout, language, receiver, and regional variant with the customer."),
]


def _safe_name(sku: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", sku)


def main() -> None:
    created = skipped = 0
    root = settings.document_storage_dir
    root.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        products = {p.sku: p for p in db.scalars(select(Product)).all() if p.sku}
        existing_ids = db.scalars(select(Document.id)).all()
        next_id = max((int(doc_id[4:]) for doc_id in existing_ids if doc_id.startswith("DOC-") and doc_id[4:].isdigit()), default=0) + 1
        for sku, publisher, title, doc_type, url, summary in REFERENCES:
            product = products.get(sku)
            if product is None:
                print(f"SKIP {sku}: catalog product not found")
                skipped += 1
                continue
            existing = db.scalar(select(Document).where(Document.product_id == product.id, Document.source_url == url))
            if existing:
                path = root / existing.file_path
                if not path.exists():
                    path.write_text(_content(title, publisher, url, summary, product.name, sku), encoding="utf-8")
                skipped += 1
                continue
            doc_id = f"DOC-{next_id:04d}"
            next_id += 1
            file_path = f"references/{_safe_name(sku)}-{doc_id}.md"
            path = root / file_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_content(title, publisher, url, summary, product.name, sku), encoding="utf-8")
            db.add(Document(
                id=doc_id, product_id=product.id, title=title, document_type=doc_type,
                description=f"External publisher reference from {publisher}; validate exact configuration before use.",
                source_name=publisher, source_url=url, file_path=file_path, file_format="md",
                version="1.0", approval_status="in_review", is_mock=False,
            ))
            created += 1
        # Ensure every catalog product has both a publication/manual reference and a
        # pricing/market-analysis entry. Search links lead to live web results and
        # are explicitly labelled as leads, not as verified valuation reports.
        for product in products.values():
            linked_docs = db.scalars(
                select(Document).where(Document.product_id == product.id, Document.source_url.is_not(None))
            ).all()
            has_publication = any(d.document_type in {"datasheet", "publication", "product_manual"} for d in linked_docs)
            has_market = any(d.document_type == "valuation_report" for d in linked_docs)
            missing = []
            if not has_publication:
                query = f'\"{product.brand or ""} {product.name} {product.sku or ""}\" official manual user guide product publication'
                missing.append(("publication", f"Product manual and publications search — {product.name}", query, "Product manuals and official publications search"))
            if not has_market:
                query = f'\"{product.brand or ""} {product.name} {product.sku or ""}\" price market analysis price history review'
                missing.append(("valuation_report", f"Pricing and market analysis search — {product.name}", query, "Pricing and market analysis search"))
            for doc_type, title, query, summary in missing:
                url = "https://www.google.com/search?q=" + quote_plus(query)
                existing = db.scalar(select(Document).where(Document.product_id == product.id, Document.source_url == url))
                if existing:
                    continue
                doc_id = f"DOC-{next_id:04d}"
                next_id += 1
                file_path = f"references/{_safe_name(product.sku or str(product.id))}-{doc_id}.md"
                path = root / file_path
                path.parent.mkdir(parents=True, exist_ok=True)
                description = (
                    "Live search link for product-specific price and market-analysis sources. "
                    "Search results are leads, not an independent appraisal or verified valuation."
                    if doc_type == "valuation_report" else
                    "Live search link for product manuals and publisher publications; verify exact model and revision."
                )
                note = (
                    "Search results may include listings and historical prices; they are not an independent appraisal. Verify currency, region, condition, date, and configuration before quoting."
                    if doc_type == "valuation_report" else
                    "Use results to locate the manufacturer or publisher document for the exact model and revision. Confirm compatibility before sharing."
                )
                path.write_text(_content(title, "Google Search", url, note, product.name, product.sku or "n/a"), encoding="utf-8")
                db.add(Document(
                    id=doc_id, product_id=product.id, title=title, document_type=doc_type,
                    description=description, source_name="Google Search", source_url=url,
                    file_path=file_path, file_format="md", version="1.0",
                    approval_status="in_review", is_mock=False,
                ))
                created += 1
        db.commit()
    print(f"Reference documents created={created}, existing or missing products skipped={skipped}")


def _content(title: str, publisher: str, url: str, summary: str, product: str, sku: str) -> str:
    source_label = "Google search results" if publisher == "Google Search" else f"{publisher} official source"
    return f"# {title}\n\n> External reference. This local note is an original summary, not a copy of the linked source. Status: in_review; internal approval is pending.\n\n- Product: {product}\n- Catalog SKU: {sku}\n- Source: {publisher}\n- Link: [{source_label}]({url})\n\n## Summary\n\n{summary}\n\n## Sales use\n\nVerify exact SKU, configuration, region, document revision, and current terms before sharing or quoting. This note does not establish internal approval.\n"



if __name__ == "__main__":
    main()

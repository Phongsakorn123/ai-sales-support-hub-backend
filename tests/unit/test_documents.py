import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backend.dependencies.auth import get_current_user
from app.backend.models.document import Document
from app.backend.models.product import Product
from app.backend.services.documents import clear_search_cache
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from scripts.seed_documents import seed_documents

LAB = {
    "name": "Quantum Pipette",
    "category": "Laboratory",
    "sku": "T-LAB",
    "selling_price": 10,
    "description": "Zorblax calibrated pipette.",
}
KEYBOARD = {"name": "Typist Keyboard", "category": "Peripherals", "sku": "T-KBD", "selling_price": 10}


class DocumentTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name) / "documents"
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        from sqlalchemy import event

        event.listen(
            self.engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON")
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as session:
            session.add_all([Product(**LAB), Product(**KEYBOARD)])
            session.commit()

        self.old_storage = settings.document_storage_dir
        self.old_secret = settings.jwt_secret_key
        settings.document_storage_dir = self.storage
        settings.jwt_secret_key = "test-secret"
        clear_search_cache()

        def override_db():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: object()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        settings.document_storage_dir = self.old_storage
        settings.jwt_secret_key = self.old_secret
        self.engine.dispose()
        self.tmp.cleanup()

    def seed(self):
        with self.Session() as session:
            return seed_documents(session, self.storage)

    def docs(self) -> list[Document]:
        with self.Session() as session:
            return list(session.scalars(select(Document).order_by(Document.id)))


class SeedTests(DocumentTestCase):
    def test_creates_files_and_records_for_existing_products(self) -> None:
        report = self.seed()
        # 4 types for the keyboard, 5 (incl. validation) for the laboratory product.
        self.assertEqual(report.records_created, 9)
        self.assertEqual(report.files_created, 9)
        self.assertEqual(report.failed, [])
        docs = self.docs()
        with self.Session() as session:
            product_ids = set(session.scalars(select(Product.id)))
        for doc in docs:
            self.assertIn(doc.product_id, product_ids)
            self.assertEqual(doc.file_path, f"{doc.id}.md")
            self.assertFalse(Path(doc.file_path).is_absolute())
            text = (self.storage / doc.file_path).read_text(encoding="utf-8")
            self.assertIn(doc.id, text)
            self.assertIn("MOCK / DEMO DATA", text)
            self.assertTrue(doc.is_mock)
            self.assertEqual(doc.approval_status, "mock_demo")

    def test_validation_report_only_for_lab_products(self) -> None:
        self.seed()
        types = {(d.product_id, d.document_type) for d in self.docs()}
        self.assertEqual(sum(1 for _, t in types if t == "validation_report"), 1)

    def test_rerun_is_idempotent_and_preserves_modified_files(self) -> None:
        self.seed()
        first = self.docs()
        edited = self.storage / first[0].file_path
        edited.write_text("user edit", encoding="utf-8")
        extra = self.storage / "notes.txt"
        extra.write_text("keep me", encoding="utf-8")

        report = self.seed()
        self.assertEqual(report.records_created, 0)
        self.assertEqual(report.files_created, 0)
        self.assertEqual(report.records_skipped, 9)
        self.assertEqual([d.id for d in self.docs()], [d.id for d in first])
        self.assertEqual(edited.read_text(encoding="utf-8"), "user edit")
        self.assertEqual(extra.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(len(list(self.storage.glob("DOC-*.md"))), 9)

    def test_missing_file_is_restored_and_new_product_supported(self) -> None:
        self.seed()
        victim = self.docs()[0]
        (self.storage / victim.file_path).unlink()
        with self.Session() as session:
            session.add(Product(name="Late Mouse", category="Peripherals", sku="T-MSE", selling_price=5))
            session.commit()
        report = self.seed()
        self.assertEqual(report.records_created, 4)
        self.assertEqual(report.files_created, 5)
        self.assertTrue((self.storage / victim.file_path).is_file())
        self.assertEqual(len(self.docs()), 13)

    def test_dry_run_writes_nothing(self) -> None:
        with self.Session() as session:
            report = seed_documents(session, self.storage, dry_run=True)
        self.assertEqual(report.records_created, 9)
        self.assertFalse(self.storage.exists())
        self.assertEqual(self.docs(), [])

    def test_existing_files_are_never_overwritten_by_new_ids(self) -> None:
        self.storage.mkdir(parents=True)
        (self.storage / "DOC-0001.md").write_text("unrelated", encoding="utf-8")
        self.seed()
        self.assertEqual((self.storage / "DOC-0001.md").read_text(encoding="utf-8"), "unrelated")
        self.assertNotIn("DOC-0001", [d.id for d in self.docs()])

    def test_deleting_product_removes_records(self) -> None:
        self.seed()
        with self.Session() as session:
            session.delete(session.scalars(select(Product).where(Product.sku == "T-KBD")).one())
            session.commit()
            remaining = session.scalar(select(func.count()).select_from(Document))
        self.assertEqual(remaining, 5)


class ApiTests(DocumentTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed()
        docs = self.docs()
        self.lab_id = next(d.product_id for d in docs if d.document_type == "validation_report")
        self.first = docs[0]

    def test_search_finds_text_from_markdown_file(self) -> None:
        response = self.client.get("/api/v1/documents/search", params={"q": "zorblax"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(body["total"], 0)
        self.assertTrue(all(i["product_id"] == self.lab_id for i in body["items"]))
        item = body["items"][0]
        self.assertNotIn("file_path", item)
        self.assertTrue(item["file_url"].startswith("/api/v1/documents/DOC-"))
        self.assertIn("snippet", item)

    def test_search_matches_new_text_added_to_file(self) -> None:
        path = self.storage / self.first.file_path
        path.write_text(path.read_text(encoding="utf-8") + "\nuniquephrasexyz\n", encoding="utf-8")
        body = self.client.get("/api/v1/documents/search", params={"q": "uniquephrasexyz"}).json()
        self.assertEqual([i["id"] for i in body["items"]], [self.first.id])

    def test_filters_and_pagination(self) -> None:
        url = "/api/v1/documents/search"
        by_type = self.client.get(url, params={"document_type": "datasheet"}).json()
        self.assertEqual(by_type["total"], 2)
        by_product = self.client.get(url, params={"product_id": self.lab_id}).json()
        self.assertEqual(by_product["total"], 5)
        page1 = self.client.get(url, params={"limit": 4, "offset": 0}).json()
        page2 = self.client.get(url, params={"limit": 4, "offset": 4}).json()
        self.assertEqual(page1["total"], 9)
        self.assertEqual(len(page1["items"]), 4)
        self.assertEqual(len(page2["items"]), 4)
        self.assertFalse({i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]})
        self.assertEqual(self.client.get(url, params={"limit": 0}).status_code, 422)

    def test_detail_and_file(self) -> None:
        detail = self.client.get(f"/api/v1/documents/{self.first.id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["file_url"], f"/api/v1/documents/{self.first.id}/file")
        file_response = self.client.get(detail.json()["file_url"])
        self.assertEqual(file_response.status_code, 200)
        self.assertTrue(file_response.headers["content-type"].startswith("text/markdown"))
        self.assertIn("inline", file_response.headers["content-disposition"])
        expected = (self.storage / self.first.file_path).read_text(encoding="utf-8")
        self.assertEqual(file_response.text, expected)
        download = self.client.get(detail.json()["download_url"])
        self.assertIn(f'attachment; filename="{self.first.id}.md"', download.headers["content-disposition"])

    def test_unknown_document_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/v1/documents/DOC-9999").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/documents/DOC-9999/file").status_code, 404)

    def test_missing_file_returns_safe_404(self) -> None:
        (self.storage / self.first.file_path).unlink()
        response = self.client.get(f"/api/v1/documents/{self.first.id}/file")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Document file not found.")
        self.assertNotIn(str(self.storage), response.text)

    def test_path_traversal_is_rejected(self) -> None:
        for bad in ("..%2Fsecret", "DOC-0001%2F..%2F..", "../etc/passwd", "DOC-1"):
            with self.subTest(bad=bad):
                self.assertIn(
                    self.client.get(f"/api/v1/documents/{bad}/file").status_code, (404, 422)
                )
        secret = Path(self.tmp.name) / "secret.md"
        secret.write_text("TOP SECRET", encoding="utf-8")
        for stored in ("../secret.md", str(secret)):
            with self.subTest(stored=stored):
                with self.Session() as session:
                    session.get(Document, self.first.id).file_path = stored
                    session.commit()
                response = self.client.get(f"/api/v1/documents/{self.first.id}/file")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("TOP SECRET", response.text)

    def test_symlink_escape_is_rejected(self) -> None:
        secret = Path(self.tmp.name) / "outside.md"
        secret.write_text("TOP SECRET", encoding="utf-8")
        link = self.storage / "link.md"
        link.symlink_to(secret)
        with self.Session() as session:
            session.get(Document, self.first.id).file_path = "link.md"
            session.commit()
        response = self.client.get(f"/api/v1/documents/{self.first.id}/file")
        self.assertEqual(response.status_code, 404)

    def test_product_documents(self) -> None:
        response = self.client.get(f"/api/v1/products/{self.lab_id}/documents")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 5)
        self.assertEqual(self.client.get("/api/v1/products/9999/documents").status_code, 404)

    def test_authentication_enforced(self) -> None:
        app.dependency_overrides.pop(get_current_user, None)
        with TestClient(app) as anonymous:
            for path in (
                "/api/v1/documents/search",
                f"/api/v1/documents/{self.first.id}",
                f"/api/v1/documents/{self.first.id}/file",
                f"/api/v1/products/{self.lab_id}/documents",
            ):
                with self.subTest(path=path):
                    self.assertEqual(anonymous.get(path).status_code, 401)

    def test_product_api_still_works(self) -> None:
        response = self.client.get("/api/v1/products")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

from app.backend.dependencies.auth import get_current_user
from app.backend.models.user import User
from app.db.base import Base
from app.db.session import get_db
from app.core.config import settings
from app.main import app


class ProductAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory(prefix="product-api-tests-")
        database_path = Path(self.temp_directory.name) / "test.db"
        self.engine = create_engine(URL.create("sqlite", database=str(database_path)))
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )
        self.previous_secret = settings.jwt_secret_key
        settings.jwt_secret_key = SecretStr("product-test-signing-secret-32-bytes")

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        self.previous_dependency_override = app.dependency_overrides.get(get_db)
        self.previous_user_override = app.dependency_overrides.get(get_current_user)
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: User(
            id=1,
            email="product-tests@example.com",
            hashed_password="unused-test-hash",
        )
        self.client = TestClient(app, headers={"Authorization": "Bearer test-product-token"})

    def tearDown(self) -> None:
        self.client.close()
        if self.previous_dependency_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = self.previous_dependency_override
        if self.previous_user_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = self.previous_user_override
        settings.jwt_secret_key = self.previous_secret
        self.engine.dispose()
        self.temp_directory.cleanup()

    @staticmethod
    def product_payload(**overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": "Desk lamp",
            "price": "1250.50",
        }
        payload.update(overrides)
        return payload

    def create_product(self, **overrides: object) -> dict[str, object]:
        response = self.client.post(
            "/api/v1/products",
            json=self.product_payload(**overrides),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_product_serializes_decimal_and_utc_timestamps(self) -> None:
        product = self.create_product(name="  Desk lamp  ")

        self.assertEqual(product["name"], "Desk lamp")
        self.assertEqual(Decimal(product["price"]), Decimal("1250.50"))
        self.assertEqual(product["currency"], "THB")
        self.assertEqual(product["stock_quantity"], 0)
        self.assertTrue(product["is_active"])
        self.assertIsInstance(product["id"], int)
        created_at = datetime.fromisoformat(product["created_at"])
        updated_at = datetime.fromisoformat(product["updated_at"])
        self.assertIsNotNone(created_at.utcoffset())
        self.assertIsNotNone(updated_at.utcoffset())

    def test_create_requires_name_and_rejects_invalid_values(self) -> None:
        invalid_payloads = (
            {"price": "10.00"},
            self.product_payload(name="   "),
            self.product_payload(price="-0.01"),
            self.product_payload(price="1.234"),
            self.product_payload(price="10000000000.00"),
            self.product_payload(stock_quantity=-1),
            self.product_payload(currency="US"),
            self.product_payload(id=20),
            self.product_payload(created_at="2026-01-01T00:00:00Z"),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/products", json=payload)
                self.assertEqual(response.status_code, 422, response.text)

    def test_list_products_is_active_ordered_and_paginated(self) -> None:
        first = self.create_product(name="First")
        second = self.create_product(name="Second")
        third = self.create_product(name="Third")
        inactive = self.client.patch(
            f"/api/v1/products/{third['id']}",
            json={"is_active": False},
        )
        self.assertEqual(inactive.status_code, 200, inactive.text)

        response = self.client.get("/api/v1/products", params={"limit": 1, "offset": 1})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["id"] for item in response.json()], [second["id"]])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.client.get("/api/v1/products").json()[0]["id"], first["id"])

    def test_get_product_including_inactive_and_missing(self) -> None:
        product = self.create_product(is_active=False)

        response = self.client.get(f"/api/v1/products/{product['id']}")
        missing = self.client.get("/api/v1/products/999999")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])
        self.assertEqual(missing.status_code, 404)

    def test_patch_updates_only_supplied_fields_and_refreshes_timestamp(self) -> None:
        product = self.create_product(name="Original")

        response = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"description": "Updated description", "price": "9.99"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["name"], "Original")
        self.assertEqual(updated["description"], "Updated description")
        self.assertEqual(Decimal(updated["price"]), Decimal("9.99"))
        self.assertGreater(
            datetime.fromisoformat(updated["updated_at"]),
            datetime.fromisoformat(product["updated_at"]),
        )

    def test_invalid_patch_does_not_partially_modify_product(self) -> None:
        product = self.create_product(name="Original")

        response = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"name": "Should not apply", "price": "-1"},
        )
        null_update = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"price": None},
        )
        after = self.client.get(f"/api/v1/products/{product['id']}")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(null_update.status_code, 422)
        self.assertEqual(after.json()["name"], "Original")
        self.assertEqual(Decimal(after.json()["price"]), Decimal("1250.50"))

    def test_patch_rejects_database_managed_fields_and_empty_update(self) -> None:
        product = self.create_product()

        for payload in (
            {"id": 10},
            {"updated_at": "2026-01-01T00:00:00Z"},
            {},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(
                    f"/api/v1/products/{product['id']}",
                    json=payload,
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_patch_missing_product_returns_404(self) -> None:
        response = self.client.patch("/api/v1/products/999999", json={"name": "Updated"})
        self.assertEqual(response.status_code, 404)

    def test_delete_returns_empty_204_and_missing_product_returns_404(self) -> None:
        product = self.create_product()

        response = self.client.delete(f"/api/v1/products/{product['id']}")
        missing = self.client.delete("/api/v1/products/999999")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(missing.status_code, 404)

    def test_health_and_openapi_remain_available(self) -> None:
        health = self.client.get("/health")
        openapi = self.client.get("/openapi.json")

        self.assertEqual(
            (health.status_code, health.json()),
            (
                200,
                {
                    "status": "healthy",
                    "service": "ai-sales-support-hub-backend",
                },
            ),
        )
        self.assertEqual(openapi.status_code, 200)
        self.assertIn("/api/v1/products", openapi.json()["paths"])


if __name__ == "__main__":
    unittest.main()

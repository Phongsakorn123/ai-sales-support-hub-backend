import unittest
from unittest import mock
from datetime import datetime
from decimal import Decimal
from tempfile import TemporaryDirectory
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.backend.dependencies.auth import get_current_user
from app.backend.models.product_embedding import ProductEmbedding
from app.backend.models.user import User
from app.db.base import Base
from app.db.session import get_db
from app.core.config import settings
from app.main import app


CHILD_TABLES = (
    "product_specifications",
    "product_features",
    "product_tags",
    "product_target_customers",
    "product_use_cases",
)


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
        self.client = TestClient(
            app, headers={"Authorization": "Bearer test-product-token"}
        )
        self.embedding_sync = mock.patch(
            "app.ai_agent.services.embeddings.try_sync_product_embedding",
            return_value=True,
        )
        self.embedding_sync_mock = self.embedding_sync.start()

    def tearDown(self) -> None:
        self.client.close()
        self.embedding_sync.stop()
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
            "selling_price": "1250.50",
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
        self.assertEqual(Decimal(product["selling_price"]), Decimal("1250.50"))
        self.assertEqual(product["currency"], "THB")
        self.assertEqual(product["stock_quantity"], 0)
        self.assertFalse(product["discontinued"])
        for field in (
            "specifications",
            "features",
            "tags",
            "target_customers",
            "use_cases",
        ):
            self.assertEqual(product[field], [])
        self.assertIsInstance(product["id"], int)
        created_at = datetime.fromisoformat(product["created_at"])
        updated_at = datetime.fromisoformat(product["updated_at"])
        self.assertIsNotNone(created_at.utcoffset())
        self.assertIsNotNone(updated_at.utcoffset())

    def test_product_create_update_sync_embeddings_and_delete_cascades_index(
        self,
    ) -> None:
        created = self.create_product(
            name="Creative laptop", description="For digital artwork"
        )
        self.embedding_sync_mock.assert_called_once()
        self.assertEqual(self.embedding_sync_mock.call_args.args[1].id, created["id"])

        with self.session_factory() as db:
            db.add(
                ProductEmbedding(
                    product_id=created["id"],
                    model_name="test",
                    content_hash="test",
                    vector=[0.1, 0.2],
                )
            )
            db.commit()

        self.embedding_sync_mock.reset_mock()
        updated = self.client.patch(
            f"/api/v1/products/{created['id']}",
            json={"description": "For photo editing"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.embedding_sync_mock.assert_called_once()

        deleted = self.client.delete(f"/api/v1/products/{created['id']}")
        self.assertEqual(deleted.status_code, 204, deleted.text)
        with self.session_factory() as db:
            self.assertIsNone(db.get(ProductEmbedding, created["id"]))

    SAMPLE = {
        "name": "ThinkPad T14 Gen 5",
        "brand": "Lenovo",
        "model": "ThinkPad T14 Gen 5",
        "sku": "LEN-TP14-G5-001",
        "description": "Business laptop for office productivity, software development, and enterprise use.",
        "image_url": None,
        "category": "Computer",
        "type": "Laptop",
        "sub_type": "Business Laptop",
        "selling_price": 42900.00,
        "currency": "THB",
        "cost_price": None,
        "stock_quantity": 15,
        "discontinued": False,
        "specifications": [
            {"key": "Processor", "value": "Intel Core Ultra 5"},
            {"key": "Memory", "value": "16 GB RAM"},
            {"key": "Storage", "value": "512 GB SSD"},
            {"key": "Display", "value": "14-inch"},
        ],
        "features": ["Business productivity", "Portable design", "Enterprise use"],
        "warranty_months": 36,
        "tags": ["laptop", "business", "enterprise"],
        "target_customers": ["SME", "IT Department", "Sales Team"],
        "use_cases": ["Office work", "Online meetings", "Programming", "Mobile work"],
        "key_benefits": "Suitable for business productivity and mobile work.",
        "limitations": "Not primarily designed for high-end 3D rendering.",
    }

    def child_counts(self) -> list[int]:
        with self.engine.connect() as connection:
            return [
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                for table in CHILD_TABLES
            ]

    def test_exact_sample_payload_round_trips_in_order(self) -> None:
        response = self.client.post("/api/v1/products", json=self.SAMPLE)
        self.assertEqual(response.status_code, 201, response.text)
        fetched = self.client.get(f"/api/v1/products/{response.json()['id']}").json()
        for field, value in self.SAMPLE.items():
            if field in ("selling_price",):
                self.assertEqual(Decimal(fetched[field]), Decimal("42900.00"))
            else:
                self.assertEqual(fetched[field], value, field)
        self.assertEqual(fetched["availability"], "in_stock")
        listed = self.client.get("/api/v1/products").json()[0]
        self.assertEqual(listed["specifications"], self.SAMPLE["specifications"])
        self.assertEqual(self.child_counts(), [4, 3, 3, 3, 4])

    def test_update_replaces_collections_and_keeps_others(self) -> None:
        product = self.client.post("/api/v1/products", json=self.SAMPLE).json()
        response = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={
                "specifications": [{"key": "RAM", "value": "32 GB"}],
                "features": [],
                "tags": ["b", "a"],
                "use_cases": ["Only one"],
                "cost_price": "30000.00",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["specifications"], [{"key": "RAM", "value": "32 GB"}])
        self.assertEqual(updated["features"], [])
        self.assertEqual(updated["tags"], ["b", "a"])
        self.assertEqual(updated["use_cases"], ["Only one"])
        self.assertEqual(updated["target_customers"], self.SAMPLE["target_customers"])
        self.assertEqual(Decimal(updated["cost_price"]), Decimal("30000.00"))
        self.assertEqual(self.child_counts(), [1, 0, 2, 3, 1])
        cleared = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"cost_price": None, "image_url": None},
        )
        self.assertIsNone(cleared.json()["cost_price"])

    def test_invalid_collections_rejected(self) -> None:
        for payload in (
            self.product_payload(tags="laptop"),
            self.product_payload(tags=None),
            self.product_payload(specifications=[{"key": "", "value": "x"}]),
            self.product_payload(specifications=[{"key": "k"}]),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.post("/api/v1/products", json=payload).status_code, 422
                )

    def test_sku_unique_on_create_and_update(self) -> None:
        first = self.create_product(sku="A-1", tags=["x"])
        second = self.create_product(sku="B-1")
        self.assertEqual(
            self.client.post(
                "/api/v1/products", json=self.product_payload(sku="A-1")
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/v1/products/{second['id']}", json={"sku": "A-1"}
            ).status_code,
            409,
        )
        self.create_product(sku=None)
        self.create_product(sku=None)
        self.assertEqual(self.child_counts()[2], 1)
        self.assertEqual(first["sku"], "A-1")

    def test_delete_removes_child_rows(self) -> None:
        keep = self.client.post(
            "/api/v1/products", json={**self.SAMPLE, "sku": "KEEP"}
        ).json()
        gone = self.client.post("/api/v1/products", json=self.SAMPLE).json()
        self.assertEqual(
            self.client.delete(f"/api/v1/products/{gone['id']}").status_code, 204
        )
        self.assertEqual(self.child_counts(), [4, 3, 3, 3, 4])
        with self.engine.connect() as connection:
            for table in CHILD_TABLES:
                orphans = connection.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE product_id = :id"),
                    {"id": gone["id"]},
                ).scalar_one()
                self.assertEqual(orphans, 0, table)
        self.assertEqual(
            self.client.get(f"/api/v1/products/{keep['id']}").json()["tags"],
            self.SAMPLE["tags"],
        )

    def test_database_rejects_orphan_child_rows(self) -> None:
        with self.engine.begin() as connection:
            with self.assertRaises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO product_tags (product_id, value, position) VALUES (999, 'x', 0)"
                    )
                )

    COLLECTIONS = (
        "specifications",
        "features",
        "tags",
        "target_customers",
        "use_cases",
    )

    def create_sample(self, **overrides: object) -> dict[str, object]:
        response = self.client.post(
            "/api/v1/products", json={**self.SAMPLE, **overrides}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_list_returns_saved_product_with_collections_and_empty_list(self) -> None:
        self.assertEqual(self.client.get("/api/v1/products").json(), [])
        created = self.create_sample()
        listed = self.client.get("/api/v1/products").json()
        self.assertEqual(listed, [created])
        self.assertEqual(
            self.client.get("/api/v1/products", params={"offset": 5}).json(), []
        )

    def test_list_loads_collections_without_n_plus_one_queries(self) -> None:
        for index in range(5):
            self.create_sample(sku=f"SKU-{index}")
        statements: list[str] = []

        def record(conn, cursor, statement, *args):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", record)
        try:
            response = self.client.get("/api/v1/products")
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(len(response.json()), 5)
        selects = [
            item for item in statements if item.lstrip().upper().startswith("SELECT")
        ]
        self.assertLessEqual(len(selects), 6, selects)

    def test_list_includes_discontinued_and_out_of_stock(self) -> None:
        self.create_sample(sku="IN")
        self.create_sample(sku="OUT", stock_quantity=0)
        self.create_sample(sku="DISC", discontinued=True)
        availability = {
            item["sku"]: item["availability"]
            for item in self.client.get("/api/v1/products").json()
        }
        self.assertEqual(
            availability,
            {"IN": "in_stock", "OUT": "out_of_stock", "DISC": "discontinued"},
        )

    def test_patch_core_fields_leaves_collections_unchanged(self) -> None:
        product = self.create_sample()
        response = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"name": "Renamed", "selling_price": "100.00", "stock_quantity": 0},
        )
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(updated["availability"], "out_of_stock")
        for field in self.COLLECTIONS:
            self.assertEqual(updated[field], self.SAMPLE[field], field)

    def test_patch_each_collection_leaves_the_others_unchanged(self) -> None:
        replacements = {
            "specifications": [{"key": "Z", "value": "1"}, {"key": "A", "value": "2"}],
            "features": ["f2", "f1"],
            "tags": ["t2", "t1"],
            "target_customers": ["c2", "c1"],
            "use_cases": ["u2", "u1"],
        }
        for field, replacement in replacements.items():
            with self.subTest(field=field):
                product = self.create_sample(sku=f"P-{field}")
                updated = self.client.patch(
                    f"/api/v1/products/{product['id']}", json={field: replacement}
                ).json()
                self.assertEqual(updated[field], replacement)
                for other in self.COLLECTIONS:
                    if other != field:
                        self.assertEqual(updated[other], self.SAMPLE[other], other)
                fetched = self.client.get(f"/api/v1/products/{product['id']}").json()
                self.assertEqual(fetched[field], replacement)

    def test_patch_empty_collection_clears_only_that_collection(self) -> None:
        product = self.create_sample()
        updated = self.client.patch(
            f"/api/v1/products/{product['id']}", json={"tags": []}
        ).json()
        self.assertEqual(updated["tags"], [])
        for other in self.COLLECTIONS:
            if other != "tags":
                self.assertEqual(updated[other], self.SAMPLE[other], other)
        self.assertEqual(self.child_counts(), [4, 3, 0, 3, 4])

    def test_nullable_fields_can_be_set_to_null(self) -> None:
        product = self.create_sample(
            image_url="https://example.com/a.png",
            cost_price="10.00",
            warranty_months=12,
        )
        nullable = (
            "sku",
            "description",
            "image_url",
            "cost_price",
            "warranty_months",
            "key_benefits",
            "limitations",
            "brand",
            "model",
            "category",
            "type",
            "sub_type",
        )
        updated = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={field: None for field in nullable},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        for field in nullable:
            self.assertIsNone(updated.json()[field], field)

    def test_patch_cannot_set_generated_or_computed_fields(self) -> None:
        product = self.create_sample()
        for payload in (
            {"id": 5},
            {"availability": "in_stock"},
            {"created_at": "2026-01-01T00:00:00Z"},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(
                    f"/api/v1/products/{product['id']}", json=payload
                )
                self.assertEqual(response.status_code, 422)
        for field in (
            "name",
            "discontinued",
            "currency",
            "stock_quantity",
            "tags",
            "specifications",
        ):
            with self.subTest(null_field=field):
                response = self.client.patch(
                    f"/api/v1/products/{product['id']}", json={field: None}
                )
                self.assertEqual(response.status_code, 422)

    def test_collection_items_are_trimmed_and_validated(self) -> None:
        created = self.create_sample(
            tags=["  spaced  "], specifications=[{"key": " K ", "value": " V "}]
        )
        self.assertEqual(created["tags"], ["spaced"])
        self.assertEqual(created["specifications"], [{"key": "K", "value": "V"}])
        for payload in (
            {"tags": ["   "]},
            {"tags": ["x" * 101]},
            {"features": [""]},
            {"features": [1]},
            {"target_customers": ["y" * 256]},
            {"use_cases": [None]},
            {"specifications": [{"key": "k", "value": ""}]},
            {"specifications": ["k=v"]},
            {"sku": "   "},
            {"selling_price": "abc"},
            {"currency": "thb1"},
            {"stock_quantity": 1.5},
            {"warranty_months": -1},
            {"cost_price": "-1"},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/v1/products", json={**self.SAMPLE, "sku": "V-1", **payload}
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_missing_product_returns_404_for_every_method(self) -> None:
        self.assertEqual(self.client.get("/api/v1/products/999").status_code, 404)
        self.assertEqual(
            self.client.patch("/api/v1/products/999", json={"name": "x"}).status_code,
            404,
        )
        self.assertEqual(self.client.delete("/api/v1/products/999").status_code, 404)

    def test_decimal_prices_serialize_exactly(self) -> None:
        product = self.create_sample(selling_price="42900.10", cost_price="0.05")
        fetched = self.client.get(f"/api/v1/products/{product['id']}").json()
        self.assertEqual(fetched["selling_price"], "42900.10")
        self.assertEqual(fetched["cost_price"], "0.05")

    def test_create_rolls_back_product_and_children_when_a_child_insert_fails(
        self,
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TRIGGER fail_use_case BEFORE INSERT ON product_use_cases "
                    "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
                )
            )
        response = self.client.post("/api/v1/products", json=self.SAMPLE)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.child_counts(), [0, 0, 0, 0, 0])
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("SELECT COUNT(*) FROM products")).scalar_one(),
                0,
            )

    def test_update_rolls_back_all_changes_when_a_child_insert_fails(self) -> None:
        product = self.create_sample()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TRIGGER fail_use_case BEFORE INSERT ON product_use_cases "
                    "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
                )
            )
        response = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"name": "Changed", "tags": ["new"], "use_cases": ["new"]},
        )
        self.assertEqual(response.status_code, 500)
        with self.engine.begin() as connection:
            connection.execute(text("DROP TRIGGER fail_use_case"))
        after = self.client.get(f"/api/v1/products/{product['id']}").json()
        self.assertEqual(after["name"], product["name"])
        self.assertEqual(after["tags"], self.SAMPLE["tags"])
        self.assertEqual(after["use_cases"], self.SAMPLE["use_cases"])

    def test_failed_duplicate_create_leaves_no_partial_rows(self) -> None:
        self.create_sample()
        response = self.client.post("/api/v1/products", json=self.SAMPLE)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.child_counts(), [4, 3, 3, 3, 4])

    def test_failed_duplicate_update_does_not_change_collections(self) -> None:
        self.create_sample(sku="ONE")
        other = self.create_sample(sku="TWO")
        response = self.client.patch(
            f"/api/v1/products/{other['id']}", json={"sku": "ONE", "tags": ["changed"]}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.client.get(f"/api/v1/products/{other['id']}").json()["tags"],
            self.SAMPLE["tags"],
        )

    def test_application_engine_enforces_foreign_keys(self) -> None:
        from app.db.session import engine as application_engine

        with application_engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("PRAGMA foreign_keys")).scalar_one(), 1
            )
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("PRAGMA foreign_keys")).scalar_one(), 1
            )

    def test_every_product_route_requires_authentication(self) -> None:
        app.dependency_overrides.pop(get_current_user, None)
        with TestClient(app) as anonymous:
            for method, path in (
                ("get", "/api/v1/products"),
                ("post", "/api/v1/products"),
                ("get", "/api/v1/products/1"),
                ("patch", "/api/v1/products/1"),
                ("delete", "/api/v1/products/1"),
            ):
                with self.subTest(method=method, path=path):
                    response = getattr(anonymous, method)(path)
                    self.assertEqual(response.status_code, 401)

    def test_expanded_fields_round_trip_and_availability(self) -> None:
        product = self.create_product(
            brand="Acme",
            sku="ACME-1",
            cost_price="10.00",
            warranty_months=12,
            stock_quantity=3,
            specifications=[{"key": "RAM", "value": "16 GB"}],
            tags=["a", "b"],
        )
        fetched = self.client.get(f"/api/v1/products/{product['id']}").json()
        self.assertEqual(fetched["specifications"], [{"key": "RAM", "value": "16 GB"}])
        self.assertEqual(fetched["tags"], ["a", "b"])
        self.assertEqual(fetched["features"], [])
        self.assertIsNone(fetched["image_url"])
        self.assertEqual(fetched["availability"], "in_stock")
        patched = self.client.patch(
            f"/api/v1/products/{product['id']}", json={"discontinued": True}
        ).json()
        self.assertEqual(patched["availability"], "discontinued")
        self.assertEqual(
            self.client.post(
                "/api/v1/products", json=self.product_payload(sku="ACME-1")
            ).status_code,
            409,
        )

    def test_create_requires_name_and_rejects_invalid_values(self) -> None:
        invalid_payloads = (
            {"selling_price": "10.00"},
            self.product_payload(name="   "),
            self.product_payload(selling_price="-0.01"),
            self.product_payload(selling_price="1.234"),
            self.product_payload(selling_price="10000000000.00"),
            self.product_payload(stock_quantity=-1),
            self.product_payload(currency="US"),
            self.product_payload(id=20),
            self.product_payload(created_at="2026-01-01T00:00:00Z"),
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/api/v1/products", json=payload)
                self.assertEqual(response.status_code, 422, response.text)

    def test_list_products_ordered_and_paginated(self) -> None:
        first = self.create_product(name="First")
        second = self.create_product(name="Second")
        third = self.create_product(name="Third")
        inactive = self.client.patch(
            f"/api/v1/products/{third['id']}",
            json={"discontinued": True},
        )
        self.assertEqual(inactive.status_code, 200, inactive.text)

        response = self.client.get("/api/v1/products", params={"limit": 1, "offset": 1})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["id"] for item in response.json()], [second["id"]])
        self.assertNotEqual(first["id"], second["id"])
        listing = self.client.get("/api/v1/products").json()
        self.assertEqual(
            [item["id"] for item in listing],
            [first["id"], second["id"], inactive.json()["id"]],
        )

    def test_get_discontinued_product_and_missing(self) -> None:
        product = self.create_product(discontinued=True)

        response = self.client.get(f"/api/v1/products/{product['id']}")
        missing = self.client.get("/api/v1/products/999999")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["availability"], "discontinued")
        self.assertEqual(missing.status_code, 404)

    def test_patch_updates_only_supplied_fields_and_refreshes_timestamp(self) -> None:
        product = self.create_product(name="Original")

        response = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"description": "Updated description", "selling_price": "9.99"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["name"], "Original")
        self.assertEqual(updated["description"], "Updated description")
        self.assertEqual(Decimal(updated["selling_price"]), Decimal("9.99"))
        self.assertGreater(
            datetime.fromisoformat(updated["updated_at"]),
            datetime.fromisoformat(product["updated_at"]),
        )

    def test_invalid_patch_does_not_partially_modify_product(self) -> None:
        product = self.create_product(name="Original")

        response = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"name": "Should not apply", "selling_price": "-1"},
        )
        null_update = self.client.patch(
            f"/api/v1/products/{product['id']}",
            json={"selling_price": None},
        )
        after = self.client.get(f"/api/v1/products/{product['id']}")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(null_update.status_code, 422)
        self.assertEqual(after.json()["name"], "Original")
        self.assertEqual(Decimal(after.json()["selling_price"]), Decimal("1250.50"))

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
        response = self.client.patch(
            "/api/v1/products/999999", json={"name": "Updated"}
        )
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

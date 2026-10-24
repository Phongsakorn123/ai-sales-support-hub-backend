import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import sessionmaker

from app.backend.models.user import User
from app.backend.services.auth import create_access_token, verify_password
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app


PASSWORD = "correct horse battery staple"
TEST_SECRET = SecretStr("auth-unit-test-signing-secret-at-least-32-bytes")


class AuthAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory(prefix="auth-api-tests-")
        database_path = Path(self.temp_directory.name) / "auth-test.db"
        self.engine = create_engine(URL.create("sqlite", database=str(database_path)))
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.previous_secret = settings.jwt_secret_key
        settings.jwt_secret_key = TEST_SECRET

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        self.previous_dependency_override = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        if self.previous_dependency_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = self.previous_dependency_override
        settings.jwt_secret_key = self.previous_secret
        self.engine.dispose()
        self.temp_directory.cleanup()

    def register(self, email: str = "Sales.User@example.com", password: str = PASSWORD):
        return self.client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )

    def login(self, email: str = "sales.user@example.com", password: str = PASSWORD):
        return self.client.post(
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )

    def test_register_normalizes_email_hashes_password_and_returns_safe_profile(self) -> None:
        response = self.register()

        self.assertEqual(response.status_code, 201, response.text)
        user_data = response.json()
        self.assertEqual(user_data["email"], "sales.user@example.com")
        self.assertNotIn("password", user_data)
        self.assertNotIn("hashed_password", user_data)
        with self.session_factory() as db:
            user = db.scalar(select(User).where(User.email == "sales.user@example.com"))
            self.assertIsNotNone(user)
            self.assertNotEqual(user.hashed_password, PASSWORD)
            self.assertTrue(verify_password(PASSWORD, user.hashed_password))
            self.assertTrue(user.hashed_password.startswith("$argon2id$"))

    def test_duplicate_email_registration_returns_conflict(self) -> None:
        self.assertEqual(self.register().status_code, 201)
        duplicate = self.register(email=" SALES.USER@EXAMPLE.COM ")
        self.assertEqual(duplicate.status_code, 409)
        self.assertNotIn("hashed_password", duplicate.json())

    def test_invalid_email_and_weak_password_are_rejected(self) -> None:
        invalid_email = self.client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": PASSWORD},
        )
        weak_password = self.client.post(
            "/api/v1/auth/register",
            json={"email": "valid@example.com", "password": "short"},
        )
        self.assertEqual(invalid_email.status_code, 422)
        self.assertEqual(weak_password.status_code, 422)

    def test_login_returns_usable_bearer_token(self) -> None:
        self.assertEqual(self.register().status_code, 201)

        response = self.login()

        self.assertEqual(response.status_code, 200, response.text)
        token_data = response.json()
        self.assertEqual(token_data["token_type"], "bearer")
        current = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["email"], "sales.user@example.com")
        self.assertNotIn("hashed_password", current.json())

    def test_wrong_password_and_unknown_email_share_generic_unauthorized(self) -> None:
        self.assertEqual(self.register().status_code, 201)

        wrong_password = self.login(password="incorrect password")
        unknown_email = self.login(email="nobody@example.com")

        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_email.status_code, 401)
        self.assertEqual(wrong_password.json(), unknown_email.json())

    def test_missing_or_placeholder_jwt_secret_fails_closed(self) -> None:
        self.assertEqual(self.register().status_code, 201)
        for secret in (None, SecretStr("replace-with-a-placeholder-secret-long-enough")):
            with self.subTest(secret_is_missing=secret is None):
                settings.jwt_secret_key = secret
                response = self.login()
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("replace-with", response.text)
        settings.jwt_secret_key = TEST_SECRET

    def test_inactive_user_cannot_login_or_access_me(self) -> None:
        registered = self.register()
        user_id = registered.json()["id"]
        with self.session_factory() as db:
            user = db.get(User, user_id)
            user.is_active = False
            db.commit()

        login = self.login()
        token = create_access_token(user_id)
        current = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(login.status_code, 401)
        self.assertEqual(current.status_code, 401)

    def test_me_rejects_missing_invalid_malformed_and_expired_tokens(self) -> None:
        self.assertEqual(self.register().status_code, 201)
        expired_token = create_access_token(1, expires_delta=timedelta(minutes=-1))
        headers = (
            {},
            {"Authorization": "Bearer invalid.token.value"},
            {"Authorization": "Bearer not-a-jwt"},
            {"Authorization": f"Bearer {expired_token}"},
        )
        for authorization in headers:
            with self.subTest(authorization=authorization):
                response = self.client.get("/api/v1/auth/me", headers=authorization)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.headers.get("www-authenticate"), "Bearer")

    def test_me_rejects_tokens_for_missing_users(self) -> None:
        token = create_access_token(987654321)

        response = self.client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 401)

    def test_product_routes_require_a_bearer_token(self) -> None:
        unauthenticated_client = TestClient(app)
        try:
            requests = (
                ("GET", "/api/v1/products", None),
                ("POST", "/api/v1/products", {"name": "Lamp", "price": "1.00"}),
                ("GET", "/api/v1/products/1", None),
                ("PATCH", "/api/v1/products/1", {"name": "Lamp"}),
                ("DELETE", "/api/v1/products/1", None),
            )
            for method, path, payload in requests:
                with self.subTest(method=method, path=path):
                    response = unauthenticated_client.request(method, path, json=payload)
                    self.assertEqual(response.status_code, 401, response.text)
        finally:
            unauthenticated_client.close()

    def test_health_remains_public_with_exact_response(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(
            (response.status_code, response.json()),
            (
                200,
                {
                    "status": "healthy",
                    "service": "ai-sales-support-hub-backend",
                },
            ),
        )

    def test_openapi_documents_oauth2_password_flow_and_form_login(self) -> None:
        response = self.client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        schemes = schema["components"]["securitySchemes"]
        oauth = next(value for value in schemes.values() if value["type"] == "oauth2")
        self.assertEqual(oauth["flows"]["password"]["tokenUrl"], "/api/v1/auth/login")
        login_form = schema["paths"]["/api/v1/auth/login"]["post"]["requestBody"]
        self.assertIn("application/x-www-form-urlencoded", login_form["content"])

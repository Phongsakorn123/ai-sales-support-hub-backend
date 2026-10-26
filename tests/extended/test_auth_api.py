import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import jwt
from pydantic import SecretStr
from sqlalchemy import URL, create_engine, select
from sqlalchemy.exc import SQLAlchemyError
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
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, autocommit=False
        )
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

    def register(
        self,
        username: str = "Sales.User",
        email: str = "Sales.User@example.com",
        password: str = PASSWORD,
    ):
        return self.client.post(
            "/api/v1/auth/register",
            json={"username": username, "email": email, "password": password},
        )

    def login(
        self,
        identifier: str = "sales.user@example.com",
        password: str = PASSWORD,
    ):
        return self.client.post(
            "/api/v1/auth/login",
            data={"username": identifier, "password": password},
        )

    def test_register_normalizes_username_and_email_hashes_password_and_returns_safe_profile(
        self,
    ) -> None:
        response = self.register()

        self.assertEqual(response.status_code, 201, response.text)
        user_data = response.json()
        self.assertEqual(user_data["username"], "sales.user")
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
        duplicate = self.register(
            username="other.user",
            email=" SALES.USER@EXAMPLE.COM ",
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertNotIn("hashed_password", duplicate.json())

    def test_duplicate_username_registration_returns_conflict(self) -> None:
        self.assertEqual(self.register().status_code, 201)

        duplicate = self.register(
            username=" SALES.USER ",
            email="another.user@example.com",
        )

        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("username", duplicate.json()["detail"])

    def test_missing_registration_fields_are_rejected(self) -> None:
        for payload in (
            {"email": "valid@example.com", "password": PASSWORD},
            {"username": "valid-user", "password": PASSWORD},
            {"username": "valid-user", "email": "valid@example.com"},
        ):
            with self.subTest(
                missing_field=set({"username", "email", "password"}) - payload.keys()
            ):
                response = self.client.post("/api/v1/auth/register", json=payload)
                self.assertEqual(response.status_code, 422, response.text)

    def test_concurrent_duplicate_registrations_create_at_most_one_account(
        self,
    ) -> None:
        def register_once():
            with TestClient(app) as client:
                return client.post(
                    "/api/v1/auth/register",
                    json={
                        "username": "concurrent-user",
                        "email": "concurrent@example.com",
                        "password": PASSWORD,
                    },
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: register_once(), range(2)))

        self.assertCountEqual(
            [response.status_code for response in responses],
            [201, 409],
        )
        with self.session_factory() as db:
            users = db.scalars(
                select(User).where(User.username == "concurrent-user")
            ).all()
        self.assertEqual(len(users), 1)

    def test_invalid_email_and_weak_password_are_rejected(self) -> None:
        invalid_email = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "valid-user",
                "email": "not-an-email",
                "password": PASSWORD,
            },
        )
        weak_password = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "valid-user",
                "email": "valid@example.com",
                "password": "short",
            },
        )
        invalid_username = self.client.post(
            "/api/v1/auth/register",
            json={
                "username": "contains spaces",
                "email": "valid@example.com",
                "password": PASSWORD,
            },
        )
        self.assertEqual(invalid_email.status_code, 422)
        self.assertEqual(weak_password.status_code, 422)
        self.assertEqual(invalid_username.status_code, 422)

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
        self.assertEqual(current.json()["username"], "sales.user")
        self.assertNotIn("hashed_password", current.json())

    def test_login_with_username_succeeds(self) -> None:
        self.assertEqual(self.register().status_code, 201)

        response = self.login(identifier=" SALES.USER ")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["token_type"], "bearer")
        self.assertTrue(response.json()["access_token"])

    def test_access_token_expires_after_sixty_minutes(self) -> None:
        self.assertEqual(self.register().status_code, 201)

        token = self.login(identifier="sales.user").json()["access_token"]
        claims = jwt.decode(
            token,
            TEST_SECRET.get_secret_value(),
            algorithms=["HS256"],
        )

        self.assertEqual(claims["exp"] - claims["iat"], 60 * 60)

    def test_wrong_password_and_unknown_email_share_generic_unauthorized(self) -> None:
        self.assertEqual(self.register().status_code, 201)

        wrong_password = self.login(password="incorrect password")
        unknown_email = self.login(identifier="nobody@example.com")
        unknown_username = self.login(identifier="nobody")

        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_email.status_code, 401)
        self.assertEqual(unknown_username.status_code, 401)
        self.assertEqual(wrong_password.json(), unknown_email.json())
        self.assertEqual(wrong_password.json(), unknown_username.json())
        self.assertEqual(
            wrong_password.json()["detail"],
            "Invalid username/email or password.",
        )

    def test_unknown_and_inactive_users_still_use_password_verification(self) -> None:
        self.assertEqual(self.register().status_code, 201)
        with self.session_factory() as db:
            user = db.scalar(select(User).where(User.username == "sales.user"))
            user.is_active = False
            db.commit()

        with patch(
            "app.backend.services.auth.verify_password",
            wraps=verify_password,
        ) as password_verification:
            unknown = self.login(identifier="unknown-user")
            inactive = self.login(identifier="sales.user")

        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(inactive.status_code, 401)
        self.assertEqual(password_verification.call_count, 2)

    def test_database_errors_do_not_log_sql_details_or_tracebacks(self) -> None:
        failing_session = Mock()
        failing_session.get.side_effect = SQLAlchemyError("sensitive SQL error")
        previous_override = app.dependency_overrides[get_db]

        def failing_get_db():
            yield failing_session

        app.dependency_overrides[get_db] = failing_get_db
        try:
            with patch("app.backend.dependencies.auth.logger") as auth_logger:
                token = create_access_token(1)
                response = self.client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            app.dependency_overrides[get_db] = previous_override

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("sensitive SQL error", response.text)
        auth_logger.error.assert_called_once_with("Could not load authenticated user")

    def test_missing_or_placeholder_jwt_secret_fails_closed(self) -> None:
        self.assertEqual(self.register().status_code, 201)
        for secret in (
            None,
            SecretStr("replace-with-a-placeholder-secret-long-enough"),
        ):
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
                    response = unauthenticated_client.request(
                        method, path, json=payload
                    )
                    self.assertEqual(response.status_code, 401, response.text)
        finally:
            unauthenticated_client.close()

    def test_product_routes_reject_invalid_and_expired_tokens_and_accept_valid_token(
        self,
    ) -> None:
        self.assertEqual(self.register().status_code, 201)
        valid_token = self.login(identifier="sales.user").json()["access_token"]
        expired_token = create_access_token(1, expires_delta=timedelta(minutes=-1))

        for token in ("not-a-token", expired_token):
            with self.subTest(token=token[:10]):
                response = self.client.get(
                    "/api/v1/products",
                    headers={"Authorization": f"Bearer {token}"},
                )
                self.assertEqual(response.status_code, 401)

        authorized = self.client.get(
            "/api/v1/products",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        self.assertEqual(authorized.status_code, 200, authorized.text)

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
        register_form = schema["paths"]["/api/v1/auth/register"]["post"]["requestBody"]
        self.assertIn("application/json", register_form["content"])

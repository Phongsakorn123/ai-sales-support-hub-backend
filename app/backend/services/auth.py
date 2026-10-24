import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import SecretStr
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.backend.models.user import User
from app.backend.schemas.auth import UserRegister
from app.core.config import settings

JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60
_password_hasher = PasswordHasher()
_DUMMY_PASSWORD_HASH = _password_hasher.hash(secrets.token_urlsafe(32))


class DuplicateUserError(Exception):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


class AuthenticationConfigurationError(Exception):
    pass


class AuthenticationPersistenceError(Exception):
    pass


def _signing_secret() -> str:
    configured_secret: SecretStr | None = settings.jwt_secret_key
    if configured_secret is None:
        raise AuthenticationConfigurationError
    secret = configured_secret.get_secret_value()
    if (
        len(secret.encode("utf-8")) < 32
        or secret.lower().startswith(("replace-", "change-me", "your-secret"))
    ):
        raise AuthenticationConfigurationError
    return secret


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        return _password_hasher.verify(hashed_password, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def register_user(db: Session, user_data: UserRegister) -> User:
    username = user_data.username
    email = user_data.email
    try:
        if db.scalar(select(User.id).where(User.username == username)) is not None:
            raise DuplicateUserError("username")
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise DuplicateUserError("email")

        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(user_data.password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    except DuplicateUserError:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        try:
            if db.scalar(select(User.id).where(User.username == username)) is not None:
                raise DuplicateUserError("username") from error
            if db.scalar(select(User.id).where(User.email == email)) is not None:
                raise DuplicateUserError("email") from error
        except DuplicateUserError:
            raise
        except SQLAlchemyError:
            raise AuthenticationPersistenceError from None
        raise AuthenticationPersistenceError from None
    except SQLAlchemyError as error:
        db.rollback()
        raise AuthenticationPersistenceError from None


def authenticate_user(db: Session, identifier: str, password: str) -> User | None:
    normalized_identifier = identifier.strip().lower()
    try:
        user = db.scalar(
            select(User).where(
                or_(
                    User.username == normalized_identifier,
                    User.email == normalized_identifier,
                )
            )
        )
    except SQLAlchemyError:
        db.rollback()
        raise AuthenticationPersistenceError from None
    password_hash = user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH
    password_matches = verify_password(password, password_hash)
    if user is None or not user.is_active or not password_matches:
        return None
    return user


def create_access_token(
    user_id: int,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    secret = _signing_secret()
    now = datetime.now(timezone.utc)
    expiration = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": expiration,
            "type": "access",
        },
        secret,
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> int | None:
    secret = _signing_secret()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "type"]},
        )
    except jwt.InvalidTokenError:
        return None

    subject = payload.get("sub")
    if payload.get("type") != "access" or not isinstance(subject, str) or not subject.isdecimal():
        return None
    user_id = int(subject)
    return user_id if user_id > 0 else None

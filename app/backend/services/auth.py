from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.backend.models.user import User
from app.backend.schemas.auth import UserRegister
from app.core.config import settings

JWT_ALGORITHM = "HS256"
_password_hasher = PasswordHasher()


class DuplicateEmailError(Exception):
    pass


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
    email = user_data.email
    try:
        existing_user = db.scalar(select(User).where(User.email == email))
        if existing_user is not None:
            raise DuplicateEmailError

        user = User(
            email=email,
            hashed_password=hash_password(user_data.password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    except DuplicateEmailError:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        existing_user = db.scalar(select(User.id).where(User.email == email))
        if existing_user is not None:
            raise DuplicateEmailError from error
        raise AuthenticationPersistenceError from None
    except SQLAlchemyError as error:
        db.rollback()
        raise AuthenticationPersistenceError from None


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    try:
        user = db.scalar(select(User).where(User.email == email))
    except SQLAlchemyError as error:
        db.rollback()
        raise AuthenticationPersistenceError from None
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(
    user_id: int,
    *,
    expires_delta: timedelta | None = None,
) -> str:
    secret = _signing_secret()
    if settings.jwt_access_token_expire_minutes < 1:
        raise AuthenticationConfigurationError
    now = datetime.now(timezone.utc)
    expiration = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.jwt_access_token_expire_minutes)
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

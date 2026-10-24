import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.backend.dependencies.auth import get_current_user
from app.backend.models.user import User
from app.backend.schemas.auth import TokenResponse, UserRead, UserRegister
from app.backend.services.auth import (
    AuthenticationConfigurationError,
    AuthenticationPersistenceError,
    DuplicateEmailError,
    authenticate_user,
    create_access_token,
    register_user,
)
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])
_email_adapter = TypeAdapter(EmailStr)


def _persistence_error(error: AuthenticationPersistenceError) -> HTTPException:
    logger.error("Authentication database operation failed")
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An internal error occurred.",
    )


def _configuration_error(error: AuthenticationConfigurationError) -> HTTPException:
    logger.error("Authentication signing secret is missing or insecure")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication is not configured.",
    )


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register an account",
    responses={
        409: {"description": "Email is already registered"},
        422: {"description": "Invalid registration fields"},
    },
)
def register(
    user_data: UserRegister,
    db: Session = Depends(get_db),
) -> User:
    try:
        return register_user(db, user_data)
    except DuplicateEmailError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from error
    except AuthenticationPersistenceError as error:
        raise _persistence_error(error) from error


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Get an access token",
    responses={
        401: {"description": "Invalid email or password"},
        503: {"description": "Authentication is not configured"},
    },
)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResponse:
    try:
        email = str(_email_adapter.validate_python(form_data.username)).lower()
    except ValidationError:
        email = ""
    try:
        user = authenticate_user(db, email, form_data.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenResponse(access_token=create_access_token(user.id))
    except AuthenticationConfigurationError as error:
        raise _configuration_error(error) from error
    except AuthenticationPersistenceError as error:
        raise _persistence_error(error) from error


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get the current user",
    responses={
        401: {"description": "Invalid, missing, or expired access token"},
        503: {"description": "Authentication is not configured"},
    },
)
def read_current_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[
        str,
        Field(
            min_length=3,
            max_length=64,
            pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        ),
    ]
    email: EmailStr
    password: Annotated[str, Field(min_length=12, max_length=128)]

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

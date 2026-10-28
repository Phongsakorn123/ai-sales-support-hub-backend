"""SQLAlchemy persistence operations for user accounts."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.backend.models.user import User


def get_user_id_by_username(db: Session, username: str) -> int | None:
    return db.scalar(select(User.id).where(User.username == username))


def get_user_id_by_email(db: Session, email: str) -> int | None:
    return db.scalar(select(User.id).where(User.email == email))


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_identifier(db: Session, identifier: str) -> User | None:
    return db.scalar(
        select(User).where(
            or_(User.username == identifier, User.email == identifier)
        )
    )


def add_user(db: Session, user: User) -> User:
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

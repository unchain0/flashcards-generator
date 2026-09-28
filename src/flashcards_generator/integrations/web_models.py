from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WebBase(DeclarativeBase):
    pass


class WebUserRecord(WebBase):
    __tablename__ = "web_users"
    __table_args__ = (UniqueConstraint("password_lookup"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    password_lookup: Mapped[str] = mapped_column(String(64), index=True)
    password_hash: Mapped[str] = mapped_column(String(512))


class WebSessionRecord(WebBase):
    __tablename__ = "web_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("web_users.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )

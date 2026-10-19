from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ORGANIZATION_KINDS = ("group", "client")


class Organization(Base):
    """Организация: своя группа компаний или независимый клиент. Записи не удаляются — архивируются."""

    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("kind IN ('group', 'client')", name="kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Дата, на которую введены начальные остатки; обычные операции — только позже неё
    opening_balance_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LegalEntity(Base):
    """Юрлицо (или ИП) внутри организации."""

    __tablename__ = "legal_entities"
    __table_args__ = (
        UniqueConstraint("organization_id", "inn", name="uq_legal_entities_organization_inn"),
        CheckConstraint("inn ~ '^([0-9]{10}|[0-9]{12})$'", name="inn"),
        CheckConstraint("kpp IS NULL OR kpp ~ '^[0-9]{9}$'", name="kpp"),
        CheckConstraint("ogrn IS NULL OR ogrn ~ '^([0-9]{13}|[0-9]{15})$'", name="ogrn"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(500))
    short_name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str] = mapped_column(String(12))
    kpp: Mapped[str | None] = mapped_column(String(9))
    ogrn: Mapped[str | None] = mapped_column(String(15))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

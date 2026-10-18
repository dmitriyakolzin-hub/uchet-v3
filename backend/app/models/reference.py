"""Справочники части 1.3б: банки (общие), расчётные счета, зарплатные проекты, контрагенты, договоры."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.db.base import Base


class Bank(Base):
    """Банк. Общий справочник, изменяет администратор."""

    __tablename__ = "banks"
    __table_args__ = (
        CheckConstraint("bic ~ '^[0-9]{9}$'", name="bic"),
        CheckConstraint("corr_account IS NULL OR corr_account ~ '^[0-9]{20}$'", name="corr_account"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    bic: Mapped[str] = mapped_column(String(9), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    corr_account: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(100), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class OrgScoped:
    """Запись справочника организации: принадлежит одной организации, не удаляется — архивируется."""

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)

    @declared_attr
    def organization_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BankAccount(OrgScoped, Base):
    """Расчётный счёт юрлица."""

    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint("legal_entity_id", "number", name="uq_bank_accounts_entity_number"),
        CheckConstraint("number ~ '^[0-9]{20}$'", name="number"),
    )

    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    bank_id: Mapped[int] = mapped_column(ForeignKey("banks.id", ondelete="RESTRICT"))
    currency_id: Mapped[int] = mapped_column(ForeignKey("currencies.id", ondelete="RESTRICT"))
    number: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(255))


class SalaryProject(OrgScoped, Base):
    """Зарплатный проект: юрлицо + банк."""

    __tablename__ = "salary_projects"

    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    bank_id: Mapped[int] = mapped_column(ForeignKey("banks.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(255))


class Counterparty(OrgScoped, Base):
    """Контрагент организации: покупатель, поставщик или прочий."""

    __tablename__ = "counterparties"
    __table_args__ = (
        CheckConstraint("kind IN ('legal', 'entrepreneur', 'person')", name="kind"),
        CheckConstraint(
            "(kind = 'legal' AND inn ~ '^[0-9]{10}$')"
            " OR (kind = 'entrepreneur' AND inn ~ '^[0-9]{12}$')"
            " OR (kind = 'person' AND (inn IS NULL OR inn ~ '^[0-9]{12}$'))",
            name="inn",
        ),
        CheckConstraint("kpp IS NULL OR kpp ~ '^[0-9]{9}$'", name="kpp"),
        # ИНН не повторяется внутри организации (у физлиц без ИНН ограничения нет)
        Index(
            "uq_counterparties_organization_inn",
            "organization_id",
            "inn",
            unique=True,
            postgresql_where=text("inn IS NOT NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(500), default="", server_default="")
    kind: Mapped[str] = mapped_column(String(20))
    inn: Mapped[str | None] = mapped_column(String(12))
    kpp: Mapped[str | None] = mapped_column(String(9))
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class Contract(OrgScoped, Base):
    """Договор нашего юрлица с контрагентом."""

    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("kind IN ('customer', 'supplier', 'other')", name="kind"),
    )

    counterparty_id: Mapped[int] = mapped_column(
        ForeignKey("counterparties.id", ondelete="RESTRICT"), index=True
    )
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    currency_id: Mapped[int] = mapped_column(ForeignKey("currencies.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(20))
    number: Mapped[str] = mapped_column(String(100))
    contract_date: Mapped[date | None] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(255), default="", server_default="")

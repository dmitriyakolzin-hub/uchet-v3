"""Справочники части 1.3б: банки (общие), расчётные счета, зарплатные проекты, контрагенты, договоры."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
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
        CheckConstraint("account_number IS NULL OR account_number ~ '^[0-9]{20}$'", name="account_number"),
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
    # реквизиты для платёжного поручения (docs/TZ.md, раздел 24, «Детали 14.4»); заполняются и из выписки
    account_number: Mapped[str | None] = mapped_column(String(20))
    bank_id: Mapped[int | None] = mapped_column(ForeignKey("banks.id", ondelete="RESTRICT"))


class Contract(OrgScoped, Base):
    """Договор нашего юрлица с контрагентом."""

    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("kind IN ('customer', 'supplier', 'other')", name="kind"),
        CheckConstraint("deferral_days IS NULL OR deferral_days BETWEEN 0 AND 365", name="deferral_days"),
        CheckConstraint("debt_limit IS NULL OR debt_limit > 0", name="debt_limit"),
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
    project_id: Mapped[int | None] = mapped_column(ForeignKey("investment_projects.id", ondelete="RESTRICT"))  # инвестпроект
    # поставщику: отсрочка оплаты накладных, дней, и лимит задолженности (docs/TZ.md, раздел 24, «Детали 14.5»)
    deferral_days: Mapped[int | None] = mapped_column(SmallInteger)
    debt_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class Unit(Base):
    """Единица измерения (по ОКЕИ). Общий справочник, изменяет администратор."""

    __tablename__ = "units"
    __table_args__ = (CheckConstraint("okei ~ '^[0-9]{3,4}$'", name="okei"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    okei: Mapped[str] = mapped_column(String(4), unique=True)
    short_name: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Nomenclature(OrgScoped, Base):
    """Номенклатура организации: товар, материал, продукция или услуга."""

    __tablename__ = "nomenclature"
    __table_args__ = (
        CheckConstraint("kind IN ('goods', 'material', 'product', 'service')", name="kind"),
        Index(
            "uq_nomenclature_organization_article",
            "organization_id",
            "article",
            unique=True,
            postgresql_where=text("article <> ''"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255))
    article: Mapped[str] = mapped_column(String(100), default="", server_default="")
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(20))


class Warehouse(OrgScoped, Base):
    """Склад организации."""

    __tablename__ = "warehouses"

    name: Mapped[str] = mapped_column(String(255))
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))


class ContractPayment(Base):
    """Периодический платёж (docs/TZ.md, раздел 24, «Детали 14.3»; раздел 26, «Детали 16.4»): сумма в определённое число
    месяца — раз в месяц, квартал или год. По договору или без него (зарплата, налоги, кредит): тогда юрлицо и контрагент
    указываются в самих условиях. Изменение условий по договору — новая запись «действует с»; прежняя закрывается днём раньше."""

    __tablename__ = "contract_payments"
    __table_args__ = (
        CheckConstraint("day BETWEEN 1 AND 31", name="day"),
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="period"),
        CheckConstraint("frequency IN ('month', 'quarter', 'year')", name="frequency"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(255), default="", server_default="")  # «Зарплата», «НДФЛ», «Кредит Сбер»
    frequency: Mapped[str] = mapped_column(String(10), default="month", server_default="month")
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    day: Mapped[int] = mapped_column(SmallInteger)  # число месяца; выходной — ближайший рабочий день раньше
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="RESTRICT"))
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))  # статья ДДС
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("executors.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

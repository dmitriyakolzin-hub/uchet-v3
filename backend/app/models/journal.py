"""Журнал операций: шапка операции, проводки, история действий (docs/TZ.md, раздел 9)."""

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
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Вид аналитики → (суффикс колонки в проводке, таблица справочника)
ANALYTIC_COLUMNS: dict[str, tuple[str, str]] = {
    "bank_account": ("bank_account_id", "bank_accounts"),
    "counterparty": ("counterparty_id", "counterparties"),
    "contract": ("contract_id", "contracts"),
    "employee": ("employee_id", "employees"),
    "nomenclature": ("nomenclature_id", "nomenclature"),
    "warehouse": ("warehouse_id", "warehouses"),
    "cfo": ("cfo_id", "cfos"),
    "cash_flow_item": ("cash_flow_article_id", "articles"),
    "pl_item": ("pl_article_id", "articles"),
}
SIDES = {"debit": "dt", "credit": "ct"}


def analytic_column(side: str, kind: str) -> str:
    """Имя колонки: analytic_column("debit", "counterparty") → "dt_counterparty_id"."""
    return f"{SIDES[side]}_{ANALYTIC_COLUMNS[kind][0]}"


OPERATION_STATUSES = {"draft": "Черновик", "posted": "Проведена", "cancelled": "Аннулирована", "plan": "План"}


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        # номер журнала — только у фактических операций; плановые (бюджет) — без номера (0)
        Index("uq_operations_organization_number", "organization_id", "number", unique=True,
              postgresql_where=text("budget_version_id IS NULL")),
        CheckConstraint("status IN ('draft', 'posted', 'cancelled', 'plan')", name="status"),
        CheckConstraint("(status = 'plan') = (budget_version_id IS NOT NULL)", name="plan_has_version"),
        CheckConstraint("plan_kind IS NULL OR plan_kind IN ('pl', 'cf')", name="plan_kind"),
        # ячейка бюджета: версия + вид плана (ОПиУ / ДДС) + ЦФО + статья + месяц — одна плановая операция
        Index("uq_operations_plan_cell", "budget_version_id", "plan_kind", "plan_cfo_id", "article_id", "op_date",
              unique=True, postgresql_nulls_not_distinct=True,
              postgresql_where=text("budget_version_id IS NOT NULL")),
        Index("ix_operations_organization_date", "organization_id", "op_date"),
        Index(
            "uq_operations_organization_import_key",
            "organization_id",
            "import_key",
            unique=True,
            postgresql_where=text("import_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    number: Mapped[int] = mapped_column(Integer)
    op_date: Mapped[date] = mapped_column(Date)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    # Ввод начальных остатков (проводки против счёта 00, дата — дата остатков организации)
    is_opening: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Ключ платежа из банковской выписки: повторная загрузка того же платежа не создаёт дубль
    import_key: Mapped[str | None] = mapped_column(String(300))
    # Плановая операция бюджета (docs/TZ.md, раздел 13): статус plan, версия, вид плана и ЦФО ячейки
    budget_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_versions.id", ondelete="CASCADE"), index=True
    )
    plan_kind: Mapped[str | None] = mapped_column(String(2))
    plan_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))

    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    postings: Mapped[list["Posting"]] = relationship(
        order_by="Posting.line_no", cascade="all, delete-orphan", lazy="selectin"
    )


class Posting(Base):
    """Проводка: Дт счёт + аналитика, Кт счёт + аналитика, сумма в валюте и в рублях, количество.

    У каждой стороны свой набор колонок аналитики (dt_*, ct_*) с внешними ключами на справочники.
    В черновике счета и суммы могут быть не заполнены; полнота проверяется при проведении.
    """

    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(SmallInteger)
    debit_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    credit_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency_id: Mapped[int] = mapped_column(ForeignKey("currencies.id", ondelete="RESTRICT"))
    amount_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # True — сумму в рублях ввели вручную, при изменении даты/суммы её не пересчитываем
    amount_rub_manual: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))

    # Аналитика: dt_counterparty_id, ct_contract_id и т.д. — см. ANALYTIC_COLUMNS
    for _side in SIDES.values():
        for _column, _table in ANALYTIC_COLUMNS.values():
            locals()[f"{_side}_{_column}"] = mapped_column(
                BigInteger, ForeignKey(f"{_table}.id", ondelete="RESTRICT"), nullable=True
            )
    del _side, _column, _table


class OperationEvent(Base):
    """История операции: кто и когда что сделал."""

    __tablename__ = "operation_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('created', 'updated', 'posted', 'unposted', 'cancelled', 'restored')",
            name="action",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

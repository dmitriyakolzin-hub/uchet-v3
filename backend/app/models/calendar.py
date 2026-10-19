"""Платёжный календарь (docs/TZ.md, раздел 18): заявки на оплату и ожидаемые поступления."""

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
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

REQUEST_STATUSES = ("draft", "pending", "approved", "rejected", "paid")


class PaymentRequest(Base):
    """Заявка: черновик → на согласовании → согласована → оплачена; отклонена — правится и подаётся снова.
    Ожидаемое поступление (direction='in') согласования не требует."""

    __tablename__ = "payment_requests"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_payment_requests_organization_number"),
        CheckConstraint("direction IN ('out', 'in')", name="direction"),
        CheckConstraint("status IN ('draft', 'pending', 'approved', 'rejected', 'paid')", name="status"),
        CheckConstraint("amount > 0", name="amount"),
        CheckConstraint("bank_account_id IS NULL OR cash_cfo_id IS NULL", name="source"),
        CheckConstraint("(status = 'paid') = (paid_on IS NOT NULL)", name="paid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(String(3))
    pay_date: Mapped[date] = mapped_column(Date)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    # откуда платим (куда поступит): расчётный счёт или касса (ЦФО); до согласования можно не указывать
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="RESTRICT"))
    cash_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="RESTRICT"))
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))  # статья ДДС
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("executors.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    description: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft")
    reject_reason: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_on: Mapped[date | None] = mapped_column(Date)
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    confirmed_by_statement: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

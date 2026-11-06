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
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

REQUEST_STATUSES = ("draft", "pending", "revision", "approved", "rejected", "paid")


class PaymentRequest(Base):
    """Заявка: черновик → на согласовании → согласована → оплачена; отклонена — правится и подаётся снова.
    Ожидаемое поступление (direction='in') согласования не требует."""

    __tablename__ = "payment_requests"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_payment_requests_organization_number"),
        CheckConstraint("direction IN ('out', 'in')", name="direction"),
        CheckConstraint("status IN ('draft', 'pending', 'revision', 'approved', 'rejected', 'paid')", name="status"),
        CheckConstraint("source IN ('request', 'periodic', 'supplier')", name="source_kind"),
        CheckConstraint("initiator IN ('cfo', 'executor', 'finance')", name="initiator"),
        UniqueConstraint("periodic_id", "period", name="uq_payment_requests_periodic_period"),
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
    # Маршрут согласования (docs/TZ.md, раздел 24): откуда заявка (обычная, периодический платёж, поставщики),
    # кто её принёс (ЦФО, ЦФУ или финансовая служба), шаги маршрута и текущий шаг (пока «на согласовании»)
    source: Mapped[str] = mapped_column(String(10), default="request", server_default="request")
    initiator: Mapped[str] = mapped_column(String(10), default="finance", server_default="finance")
    route: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    step: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    # периодический платёж: условия договора и месяц (первое число), за который создана заявка
    periodic_id: Mapped[int | None] = mapped_column(ForeignKey("contract_payments.id", ondelete="SET NULL"))
    # реестр на оплату: платёжка выгружена в банк-клиент (или отмечено «отправлено в банк»)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    period: Mapped[date | None] = mapped_column(Date)
    # платёж внутри группы (docs/TZ.md, раздел 24, «Детали 14.5»): у поступления получателя — ссылка на выплату
    # плательщика; такое поступление меняется вместе с выплатой
    linked_id: Mapped[int | None] = mapped_column(ForeignKey("payment_requests.id", ondelete="CASCADE"), index=True)
    # сумму или дату поправили вручную в календаре (docs/TZ.md, раздел 24, «Детали 14.6»): периодический платёж
    # больше не подстраивает такую заявку под условия договора
    adjusted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class PaymentRequestEvent(Base):
    """История заявки: подана, согласована на шаге, отклонена, отправлена на доработку, отозвана, оплачена."""

    __tablename__ = "payment_request_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("payment_requests.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(20))
    step: Mapped[str] = mapped_column(String(20), default="", server_default="")
    comment: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentRequestFile(Base):
    """Файл к заявке (счёт, договор, акт): хранится в базе, попадает в бэкап. До 10 МБ."""

    __tablename__ = "payment_request_files"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("payment_requests.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupplierInvoice(Base):
    """Накладная (или аванс) поставщика из реестра накладных: долг нашего юрлица перед контрагентом.
    Срок оплаты — дата документа + отсрочка договора; оплачивается заявками (supplier_payments)."""

    __tablename__ = "supplier_invoices"
    __table_args__ = (
        CheckConstraint("kind IN ('invoice', 'advance')", name="kind"),
        CheckConstraint("amount > 0", name="amount"),
        UniqueConstraint("legal_entity_id", "counterparty_id", "kind", "number", "doc_date",
                         name="uq_supplier_invoices_document"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"), index=True)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(10), default="invoice", server_default="invoice")
    number: Mapped[str] = mapped_column(String(50))
    doc_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupplierPayment(Base):
    """Часть заявки на оплату, которая закрывает накладную (одна заявка может закрывать несколько накладных)."""

    __tablename__ = "supplier_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount"),
        UniqueConstraint("invoice_id", "request_id", name="uq_supplier_payments_invoice_request"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("supplier_invoices.id", ondelete="CASCADE"), index=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("payment_requests.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class CalendarBalance(Base):
    """Остаток на начало дня, введённый вручную (финансовый менеджер, по банк-клиенту): расчётный счёт или касса."""

    __tablename__ = "calendar_balances"
    __table_args__ = (
        CheckConstraint("(bank_account_id IS NULL) <> (cash_cfo_id IS NULL)", name="source"),
        UniqueConstraint("bank_account_id", "cash_cfo_id", "day", name="uq_calendar_balances_source_day",
                         postgresql_nulls_not_distinct=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"))
    cash_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    entered_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CalendarTransfer(Base):
    """Плановая техническая операция, введённая в календаре (docs/TZ.md, раздел 26, «Детали 16.2»): перевод между
    своими счетами, сдача наличных в банк, снятие в кассу. Пока перевода нет в журнале — план; проведённый перевод
    с тех же денег на те же на ту же сумму (±14 дней) его закрывает."""

    __tablename__ = "calendar_transfers"
    __table_args__ = (
        CheckConstraint("(from_bank_account_id IS NULL) <> (from_cash_cfo_id IS NULL)", name="from_source"),
        CheckConstraint("(to_bank_account_id IS NULL) <> (to_cash_cfo_id IS NULL)", name="to_source"),
        CheckConstraint("amount > 0", name="amount"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    legal_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    from_bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"))
    from_cash_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="CASCADE"))
    to_bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"))
    to_cash_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

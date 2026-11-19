"""Внутренние цены по SKU — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.3»).

Цена товара действует с даты; бывает общей для организации (ЦФО не указан) или своей у филиала / магазина
(действует и для его подчинённых). На дату и ЦФО берётся самая «близкая» цена: сначала ЦФО, потом его родители,
потом общая; на каждом уровне — последняя, начавшая действовать не позже даты.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric
from sqlalchemy import SmallInteger, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Вид цены → (название, звено-продавец, по которому берётся цена при передаче)
PRICE_KINDS = {
    "production": "Цена производства",  # производство → логистика (СГП)
    "logistics": "Цена логистики",  # логистика → торговля
    "sale": "Цена продажи",  # торговля → клиент (план выручки)
    "discount": "Цена дисконта",  # торговля → дисконт
    "purchase": "Цена закупки",  # плановая закупка материалов и покупных товаров (себестоимость в плане)
}
VAT_RATES = (0, 5, 7, 10, 20, 22)


class NomenclaturePrice(Base):
    __tablename__ = "nomenclature_prices"
    __table_args__ = (
        Index("uq_nomenclature_prices_key", "nomenclature_id", "cfo_id", "valid_from", unique=True,
              postgresql_nulls_not_distinct=True),
        CheckConstraint("vat_rate IN (0, 5, 7, 10, 20, 22)", name="vat_rate"),
        CheckConstraint("coalesce(production, 0) >= 0 AND coalesce(logistics, 0) >= 0 AND coalesce(sale, 0) >= 0"
                        " AND coalesce(discount, 0) >= 0 AND coalesce(purchase, 0) >= 0", name="not_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    nomenclature_id: Mapped[int] = mapped_column(ForeignKey("nomenclature.id", ondelete="CASCADE"), index=True)
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="CASCADE"), index=True)
    valid_from: Mapped[dt.date] = mapped_column(Date)
    production: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    logistics: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    sale: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    discount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    purchase: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    vat_included: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # цены с НДС
    vat_rate: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


TRANSFER_SOURCES = {"manual": "Вручную", "excel": "Из Excel", "journal": "Из журнала"}


class InternalTransfer(Base):
    """Документ внутренней передачи между направлениями (docs/TZ.md, раздел 29, «Детали 20.4»): строки — товары
    с количеством по внутренней цене; проведение — одна операция журнала (внутренняя выручка / закупка)."""

    __tablename__ = "internal_transfers"
    __table_args__ = (
        Index("uq_internal_transfers_number", "organization_id", "number", unique=True),
        Index("uq_internal_transfers_source_operation", "source_operation_id", "from_cfo_id", "to_cfo_id", unique=True,
              postgresql_where=text("source_operation_id IS NOT NULL AND status = 'posted'")),
        CheckConstraint("status IN ('posted', 'cancelled')", name="status"),
        CheckConstraint("source IN ('manual', 'excel', 'journal')", name="source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    on_date: Mapped[dt.date] = mapped_column(Date)
    from_cfo_id: Mapped[int] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    to_cfo_id: Mapped[int] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    source: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    # перемещение в журнале, по которому создана передача (не предлагается повторно)
    source_operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="posted", server_default="posted")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InternalTransferLine(Base):
    __tablename__ = "internal_transfer_lines"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("internal_transfers.id", ondelete="CASCADE"), index=True)
    nomenclature_id: Mapped[int | None] = mapped_column(ForeignKey("nomenclature.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(255), default="", server_default="")  # строка без товара
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    price_kind: Mapped[str | None] = mapped_column(String(20))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # без НДС; пусто — сумма введена вручную
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))

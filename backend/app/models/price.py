"""Внутренние цены по SKU — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.3»).

Цена товара действует с даты; бывает общей для организации (ЦФО не указан) или своей у филиала / магазина
(действует и для его подчинённых). На дату и ЦФО берётся самая «близкая» цена: сначала ЦФО, потом его родители,
потом общая; на каждом уровне — последняя, начавшая действовать не позже даты.
"""

import datetime as dt
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Numeric
from sqlalchemy import SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Вид цены → (название, звено-продавец, по которому берётся цена при передаче)
PRICE_KINDS = {
    "production": "Цена производства",  # производство → логистика (СГП)
    "logistics": "Цена логистики",  # логистика → торговля
    "sale": "Цена продажи",  # торговля → клиент (план выручки)
    "discount": "Цена дисконта",  # торговля → дисконт
}
VAT_RATES = (0, 5, 7, 10, 20, 22)


class NomenclaturePrice(Base):
    __tablename__ = "nomenclature_prices"
    __table_args__ = (
        Index("uq_nomenclature_prices_key", "nomenclature_id", "cfo_id", "valid_from", unique=True,
              postgresql_nulls_not_distinct=True),
        CheckConstraint("vat_rate IN (0, 5, 7, 10, 20, 22)", name="vat_rate"),
        CheckConstraint("coalesce(production, 0) >= 0 AND coalesce(logistics, 0) >= 0 AND coalesce(sale, 0) >= 0"
                        " AND coalesce(discount, 0) >= 0", name="not_negative"),
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
    vat_included: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # цены с НДС
    vat_rate: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

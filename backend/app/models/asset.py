"""Движение имущества (docs/TZ.md, раздел 28): единица имущества, её история и файлы, категории."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.organization import Organization
from app.models.reference import OrgScoped

ASSET_STATUSES = {"stock": "На складе", "in_use": "В эксплуатации", "repair": "В ремонте", "disposed": "Выбыло"}
OWNERSHIP = {"own": "Собственное", "leasing": "Лизинг", "rent": "Аренда"}
DEFAULT_CATEGORIES = ["Оборудование", "Мебель", "Компьютерная техника", "Инструмент", "Инвентарь", "Транспорт",
                      "Прочее"]


class AssetCategory(OrgScoped, Base):
    """Категория имущества: оборудование, мебель, техника, инструмент, инвентарь…"""

    __tablename__ = "asset_categories"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    name: Mapped[str] = mapped_column(String(255))


class AssetItem(Base):
    """Единица имущества: одна штука со своим инвентарным номером, местом и МОЛ."""

    __tablename__ = "asset_items"
    __table_args__ = (
        UniqueConstraint("organization_id", "number"),
        CheckConstraint("status IN ('stock', 'in_use', 'repair', 'disposed')", name="status"),
        CheckConstraint("ownership IN ('own', 'leasing', 'rent')", name="ownership"),
        CheckConstraint("purchase_price IS NULL OR purchase_price >= 0", name="purchase_price"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    number: Mapped[str] = mapped_column(String(30))  # инвентарный номер
    name: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("asset_categories.id", ondelete="RESTRICT"))
    ownership: Mapped[str] = mapped_column(String(10), default="own", server_default="own")
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    serial_number: Mapped[str] = mapped_column(String(100), default="", server_default="")
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"), index=True)  # место
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), index=True)  # МОЛ
    status: Mapped[str] = mapped_column(String(10), default="stock", server_default="stock")
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    upgraded_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # цена с удорожанием (19.4)
    lease_remaining: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # остаток лизинга (19.4)
    acquired_on: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    # карточка основного средства (амортизация, ввод, выбытие — шаги 4.4, 5.2)
    fixed_asset_id: Mapped[int | None] = mapped_column(ForeignKey("fixed_assets.id", ondelete="SET NULL"),
                                                       unique=True)
    # приёмка по заявке на имущество (шаг 19.3)
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("asset_receipts.id", ondelete="SET NULL"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetEvent(Base):
    """История единицы: кто, когда, что сделал; details — «что было → что стало»."""

    __tablename__ = "asset_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("asset_items.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    action: Mapped[str] = mapped_column(String(20))
    details: Mapped[str] = mapped_column(Text, default="", server_default="")


class AssetItemFile(Base):
    """Файл единицы (акт, фото, паспорт): хранится в базе, попадает в бэкап. До 10 МБ."""

    __tablename__ = "asset_item_files"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("asset_items.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def seed_asset_categories(connection, organization_id: int) -> None:
    for name in DEFAULT_CATEGORIES:
        connection.execute(text("INSERT INTO asset_categories (organization_id, name, is_active) "
                                "VALUES (:org, :name, true)"), {"org": organization_id, "name": name})


@event.listens_for(Organization, "after_insert")
def _organization_asset_categories(_mapper, connection, target: Organization) -> None:
    seed_asset_categories(connection, target.id)


ASSET_REQUEST_STATUSES = {
    "draft": "Черновик", "pending": "На решении ФД", "rejected": "Отклонена", "approved": "Решение принято",
    "ordered": "Заказано", "shipped": "Отгружено", "delivered": "Доставлено", "received": "Принято",
    "cancelled": "Отменена",
}
PURCHASE_KINDS = {"prepay": "Предоплата", "deferral": "Отсрочка", "leasing": "Лизинг"}


class AssetRequest(Base):
    """Заявка на имущество (docs/TZ.md, раздел 28, «Детали 19.2»): что нужно → решение ФД (наименование, поставщик,
    ответственный, вариант закупки) → заявки на оплату в календаре → статус поставки."""

    __tablename__ = "asset_requests"
    __table_args__ = (
        UniqueConstraint("organization_id", "number"),
        CheckConstraint("status IN ('draft', 'pending', 'rejected', 'approved', 'ordered', 'shipped', 'delivered', "
                        "'received', 'cancelled')", name="status"),
        CheckConstraint("purchase_kind IS NULL OR purchase_kind IN ('prepay', 'deferral', 'leasing')",
                        name="purchase_kind"),
        CheckConstraint("quantity > 0", name="quantity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10), default="draft", server_default="draft")
    # что нужно (подаёт инженер, оператор АХО, ФД)
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("asset_categories.id", ondelete="RESTRICT"))
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"), index=True)
    legal_entity_id: Mapped[int | None] = mapped_column(ForeignKey("legal_entities.id", ondelete="RESTRICT"))
    justification: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    estimated_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # ориентир цены за единицу
    # решение финансового директора
    model_name: Mapped[str] = mapped_column(String(255), default="", server_default="")  # утверждённое наименование
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="RESTRICT"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))  # ответственный
    purchase_kind: Mapped[str | None] = mapped_column(String(10))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # цена за единицу
    pay_date: Mapped[date | None] = mapped_column(Date)  # оплата (предоплата, отсрочка) или первый платёж лизинга
    bank_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="RESTRICT"))
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))  # статья ДДС
    lease_payment: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # ежемесячный платёж лизинга
    lease_months: Mapped[int | None] = mapped_column(BigInteger)
    periodic_id: Mapped[int | None] = mapped_column(ForeignKey("contract_payments.id", ondelete="SET NULL"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    # поставка
    expected_on: Mapped[date | None] = mapped_column(Date)
    delivered_on: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetRequestEvent(Base):
    """История заявки на имущество."""

    __tablename__ = "asset_request_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("asset_requests.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    action: Mapped[str] = mapped_column(String(20))
    details: Mapped[str] = mapped_column(Text, default="", server_default="")


class AssetReceipt(Base):
    """Приёмка по заявке на имущество (docs/TZ.md, раздел 28, «Детали 19.3»): дата, кто принял, сколько пришло,
    расхождения; каждая принятая штука — единица имущества. По одной заявке может быть несколько приёмок."""

    __tablename__ = "asset_receipts"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("asset_requests.id", ondelete="CASCADE"), index=True)
    received_on: Mapped[date] = mapped_column(Date)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))  # кто принял
    quantity: Mapped[int] = mapped_column(BigInteger)
    discrepancy: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetReceiptFile(Base):
    """Акт приёмки, фото: хранится в базе, до 10 МБ."""

    __tablename__ = "asset_receipt_files"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("asset_receipts.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

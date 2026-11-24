"""Движение имущества (docs/TZ.md, раздел 28): единица имущества, её история и файлы, категории."""

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
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.organization import Organization
from app.models.reference import OrgScoped

ASSET_STATUSES = {"stock": "На складе", "in_use": "В эксплуатации", "repair": "В ремонте", "disposed": "Выбыло"}
# как единица учтена бухгалтером (docs/TZ.md, раздел 28, «Детали 19.4»)
ACCOUNTING = {"fixed_asset": "Основное средство", "expense": "Малоценка, в расходах", "off_balance": "За балансом",
              "earlier": "Учтено ранее"}
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
        CheckConstraint("accounting IS NULL OR accounting IN ('fixed_asset', 'expense', 'off_balance', 'earlier')",
                        name="accounting"),
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
    # договор лизинга (docs/TZ.md, раздел 28, «Детали 21.2»): по нему видно всё имущество договора
    lease_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), index=True)
    acquired_on: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    # карточка основного средства (амортизация, ввод, выбытие — шаги 4.4, 5.2)
    fixed_asset_id: Mapped[int | None] = mapped_column(ForeignKey("fixed_assets.id", ondelete="SET NULL"),
                                                       unique=True)
    # учёт (шаг 19.4): как учтена, когда, операция поступления (Дт 08 / счёт затрат — Кт 60.01)
    accounting: Mapped[str | None] = mapped_column(String(12))
    accounted_on: Mapped[date | None] = mapped_column(Date)
    accounting_operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))
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
    "draft": "Черновик", "pending": "На согласовании", "revision": "На доработке", "rejected": "Отклонена",
    "approved": "Решение принято",
    "ordered": "Заказано", "shipped": "Отгружено", "delivered": "Доставлено", "received": "Принято",
    "cancelled": "Отменена",
}
BUDGET_MARKS = {"within": "В рамках бюджета", "over": "Сверх бюджета"}
PURCHASE_KINDS = {"prepay": "Предоплата", "deferral": "Отсрочка", "leasing": "Лизинг"}


class AssetRequest(Base):
    """Заявка на имущество (docs/TZ.md, раздел 28, «Детали 19.2»): что нужно → решение ФД (наименование, поставщик,
    ответственный, вариант закупки) → заявки на оплату в календаре → статус поставки."""

    __tablename__ = "asset_requests"
    __table_args__ = (
        UniqueConstraint("organization_id", "number"),
        CheckConstraint("status IN ('draft', 'pending', 'revision', 'rejected', 'approved', 'ordered', 'shipped', "
                        "'delivered', 'received', 'cancelled')", name="status"),
        CheckConstraint("budget_mark IS NULL OR budget_mark IN ('within', 'over')", name="budget_mark"),
        CheckConstraint("cost_kind IS NULL OR cost_kind IN ('invest', 'opex')", name="cost_kind"),
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
    needed_on: Mapped[date | None] = mapped_column(Date)  # когда нужно — месяц, по бюджету которого проверяется заявка
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("executors.id", ondelete="SET NULL"))  # кто закупает
    # маршрут согласования (docs/TZ.md, раздел 28, «Детали 21.1»): «cfo:<id>» снизу вверх, «ceo», «fd»
    route: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    step: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # отметка о бюджете: within / over; budget_* — расчёт на момент отметки
    budget_mark: Mapped[str | None] = mapped_column(String(6))
    budget_manual: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    budget_comment: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    budget_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="SET NULL"))
    budget_plan: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # план ДДС ветки на месяц (None — плана нет)
    budget_used: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # другие заявки на имущество ветки в месяце
    # вид затрат (docs/TZ.md, раздел 28, «Детали 21.3»): invest — инвестпроект (+ статья вложений), opex — статья
    # расходов ОПиУ; бюджет заявки — план вложений проекта или план ОПиУ ветки ЦФО; NULL — прежние заявки (план ДДС)
    cost_kind: Mapped[str | None] = mapped_column(String(6))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("investment_projects.id", ondelete="SET NULL"),
                                                   index=True)
    budget_article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="SET NULL"))
    budget_fact: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # факт месяца (вложения / расходы)
    budget_limit_left: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # остаток лимита проекта
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


TRANSFER_STATUSES = {"pending": "Ждёт подтверждения", "accepted": "Принято", "rejected": "Отказ", "cancelled": "Отменено"}
WORK_KINDS = {"modernization": "Модернизация", "repair": "Ремонт", "maintenance": "ТО"}


class AssetTransfer(Base):
    """Перемещение / передача имущества (docs/TZ.md, раздел 28, «Детали 19.5»): куда (место, склад), кому (МОЛ),
    в каком статусе. Единицы переходят, когда получатель подтверждает."""

    __tablename__ = "asset_transfers"
    __table_args__ = (
        UniqueConstraint("organization_id", "number"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected', 'cancelled')", name="status"),
        CheckConstraint("to_status IS NULL OR to_status IN ('stock', 'in_use', 'repair')", name="to_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(BigInteger)
    on_date: Mapped[date] = mapped_column(Date)
    to_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    to_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"))
    to_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    to_status: Mapped[str | None] = mapped_column(String(10))  # «в эксплуатацию», «на склад», «в ремонт»
    reason: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    reject_reason: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # согласование АХО (docs/TZ.md, раздел 30, «Детали 22.3»): до него перемещение не принимают, выбытие не решают
    aho_approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    aho_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetTransferItem(Base):
    """Единица в перемещении: откуда (место, склад, МОЛ на момент создания)."""

    __tablename__ = "asset_transfer_items"
    __table_args__ = (UniqueConstraint("transfer_id", "item_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("asset_transfers.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("asset_items.id", ondelete="CASCADE"), index=True)
    from_cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    from_warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"))
    from_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))


class AssetWork(Base):
    """Работа по единице: модернизация (увеличивает стоимость единицы), ремонт, ТО — кто делал, сколько стоило."""

    __tablename__ = "asset_works"
    __table_args__ = (
        CheckConstraint("kind IN ('modernization', 'repair', 'maintenance')", name="kind"),
        CheckConstraint("amount IS NULL OR amount >= 0", name="amount"),
        CheckConstraint("finished_on IS NULL OR finished_on >= started_on", name="dates"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("asset_items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(15))
    started_on: Mapped[date] = mapped_column(Date)
    finished_on: Mapped[date | None] = mapped_column(Date)  # ремонт: пока не закончен — единица «в ремонте»
    description: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))  # кто делал
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


DISPOSAL_KINDS = {"sale": "Продажа", "writeoff": "Списание", "loss": "Гибель / утрата"}
DISPOSAL_STATUSES = {"pending": "На согласовании ФД", "done": "Выбыло", "rejected": "Отклонено", "cancelled": "Отменено",
                     "undone": "Выбытие отменено"}


class AssetDisposal(Base):
    """Выбытие имущества (docs/TZ.md, раздел 28, «Детали 19.6»): продажа, списание, гибель / утрата. Предлагают инженер,
    АХО, бухгалтер; согласует ФД — тогда единицы выбывают и создаются проводки."""

    __tablename__ = "asset_disposals"
    __table_args__ = (
        UniqueConstraint("organization_id", "number"),
        CheckConstraint("kind IN ('sale', 'writeoff', 'loss')", name="kind"),
        CheckConstraint("status IN ('pending', 'done', 'rejected', 'cancelled', 'undone')", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(10))
    on_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id", ondelete="RESTRICT"))  # покупатель
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="RESTRICT"))
    vat_rate: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))  # виновное лицо
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    reject_reason: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # согласование АХО (docs/TZ.md, раздел 30, «Детали 22.3»): до него перемещение не принимают, выбытие не решают
    aho_approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    aho_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetDisposalItem(Base):
    """Единица в выбытии: цена продажи, статус до выбытия, операция (продажа не-ОС)."""

    __tablename__ = "asset_disposal_items"
    __table_args__ = (UniqueConstraint("disposal_id", "item_id"),
                      CheckConstraint("price IS NULL OR price >= 0", name="price"))

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    disposal_id: Mapped[int] = mapped_column(ForeignKey("asset_disposals.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("asset_items.id", ondelete="CASCADE"), index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    prev_status: Mapped[str | None] = mapped_column(String(10))
    operation_id: Mapped[int | None] = mapped_column(ForeignKey("operations.id", ondelete="SET NULL"))


INVENTORY_STATUSES = {"open": "Идёт", "closed": "Завершена"}


class AssetInventory(Base):
    """Инвентаризация (docs/TZ.md, раздел 28, «Детали 19.7»): что должно быть (место с подчинёнными ЦФО, склад, МОЛ —
    снимок при создании) и что найдено сканированием QR или вводом номера."""

    __tablename__ = "asset_inventories"
    __table_args__ = (
        UniqueConstraint("organization_id", "number"),
        CheckConstraint("status IN ('open', 'closed')", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    number: Mapped[int] = mapped_column(BigInteger)
    on_date: Mapped[date] = mapped_column(Date)
    cfo_id: Mapped[int | None] = mapped_column(ForeignKey("cfos.id", ondelete="RESTRICT"))
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    note: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetInventoryLine(Base):
    """Строка инвентаризации: expected — числилась в этом месте при создании; found — найдена (отсканирована)."""

    __tablename__ = "asset_inventory_lines"
    __table_args__ = (UniqueConstraint("inventory_id", "item_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    inventory_id: Mapped[int] = mapped_column(ForeignKey("asset_inventories.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("asset_items.id", ondelete="CASCADE"), index=True)
    expected: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    found: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    found_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")

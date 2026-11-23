"""Единицы имущества (docs/TZ.md, раздел 28, «Детали 19.1»): номера, видимость, история, связь с карточками ОС,
реестр и загрузка имеющегося имущества из Excel."""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.models import (
    AssetCategory,
    AssetEvent,
    AssetItem,
    Cfo,
    Contract,
    Employee,
    FixedAsset,
    LegalEntity,
    User,
    Warehouse,
)
from app.models.asset import ASSET_STATUSES, OWNERSHIP
from app.services.excel_import import (
    Column,
    RowError,
    RowProblem,
    _date,
    _decimal,
    _digits,
    _help_sheet,
    _read_rows,
    _sheet_with_header,
    _text,
    _to_bytes,
    cfo_lookup,
)

NUMBER_PREFIX = "ИН-"
MAX_COUNT = 1000  # сколько одинаковых единиц можно завести за раз
MANUAL_STATUSES = ("stock", "in_use", "repair")  # «выбыло» — только через выбытие (шаг 19.6) или карточку ОС
FIELD_TITLES = {
    "number": "инв. №", "name": "наименование", "category_id": "категория", "ownership": "владение",
    "legal_entity_id": "юрлицо", "serial_number": "серийный №", "cfo_id": "место", "warehouse_id": "склад",
    "employee_id": "МОЛ", "status": "статус", "purchase_price": "цена приобретения", "acquired_on": "дата приобретения",
    "note": "примечание", "lease_contract_id": "договор лизинга",
}


class AssetError(ValueError):
    pass


def next_numbers(db: Session, organization_id: int, count: int) -> list[str]:
    """Следующие свободные номера «ИН-000001»… (под блокировкой строки организации)."""
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    current = db.scalar(text(
        "SELECT max(substring(number from '^ИН-([0-9]+)$')::bigint) FROM asset_items WHERE organization_id = :org"
    ).bindparams(org=organization_id)) or 0
    return [f"{NUMBER_PREFIX}{current + i:06d}" for i in range(1, count + 1)]


def event(db: Session, item: AssetItem, user: User | None, action: str, details: str = "") -> None:
    db.add(AssetEvent(item_id=item.id, user_id=user.id if user else None, action=action, details=details))


# --- видимость ---


def own_employee_ids(db: Session, user: User, organization_id: int) -> list[int]:
    """Сотрудники, с которыми связан пользователь (МОЛ)."""
    return list(db.scalars(select(Employee.id).where(Employee.organization_id == organization_id,
                                                     Employee.user_id == user.id)))


# --- подписи ---


def cfo_labels(db: Session, organization_id: int) -> dict[int, str]:
    cfos = list(db.scalars(select(Cfo).where(Cfo.organization_id == organization_id)))
    names = {c.id: c.name for c in cfos}
    return {c.id: " / ".join(names[int(i)] for i in str(c.path).split(".")) for c in cfos}


@dataclass
class Labels:
    categories: dict[int, str]
    entities: dict[int, str]
    cfos: dict[int, str]
    warehouses: dict[int, str]
    employees: dict[int, str]
    contracts: dict[int, str] = field(default_factory=dict)  # договоры лизинга

    @classmethod
    def load(cls, db: Session, organization_id: int) -> "Labels":
        def names(model, attr="name"):
            return {x.id: getattr(x, attr) for x in db.scalars(select(model).where(model.organization_id == organization_id))}
        contracts = {c.id: contract_label(c) for c in db.scalars(select(Contract).where(
            Contract.organization_id == organization_id, Contract.kind == "leasing"))}
        return cls(names(AssetCategory), names(LegalEntity, "short_name"), cfo_labels(db, organization_id),
                   names(Warehouse), names(Employee, "full_name"), contracts)

    def value(self, key: str, value) -> str:
        if value is None or value == "":
            return "—"
        lookup = {"category_id": self.categories, "legal_entity_id": self.entities, "cfo_id": self.cfos,
                  "warehouse_id": self.warehouses, "employee_id": self.employees,
                  "lease_contract_id": self.contracts}.get(key)
        if lookup is not None:
            return lookup.get(value, str(value))
        if key == "status":
            return ASSET_STATUSES.get(value, value)
        if key == "ownership":
            return OWNERSHIP.get(value, value)
        if isinstance(value, Decimal):
            return f"{value:.2f}"
        if isinstance(value, dt.date):
            return f"{value:%d.%m.%Y}"
        return str(value)


def changes_text(labels: Labels, before: dict, after: dict) -> str:
    """«место: Склад → Магазин 1; МОЛ: — → Иванов»."""
    parts = []
    for key, title in FIELD_TITLES.items():
        if key in after and before.get(key) != after[key]:
            parts.append(f"{title}: {labels.value(key, before.get(key))} → {labels.value(key, after[key])}")
    return "; ".join(parts)


def snapshot(item: AssetItem) -> dict:
    return {key: getattr(item, key) for key in FIELD_TITLES}


def contract_label(c: Contract) -> str:
    return f"№ {c.number}" + (f" от {c.contract_date:%d.%m.%Y}" if c.contract_date else "") + (f" {c.name}" if c.name else "")


# --- проверка ссылок ---


def validate(db: Session, item: AssetItem) -> None:
    org = item.organization_id
    for model, attr, title in ((AssetCategory, "category_id", "Категория"), (LegalEntity, "legal_entity_id", "Юрлицо"),
                               (Cfo, "cfo_id", "Место (ЦФО)"), (Warehouse, "warehouse_id", "Склад"),
                               (Employee, "employee_id", "МОЛ")):
        value = getattr(item, attr)
        if value is not None:
            record = db.get(model, value)
            if record is None or record.organization_id != org:
                raise AssetError(f"{title}: не найдено в этой организации")
    if item.lease_contract_id is not None:
        contract = db.get(Contract, item.lease_contract_id)
        if contract is None or contract.organization_id != org or contract.kind != "leasing":
            raise AssetError("Договор лизинга: не найден в этой организации")
        if item.legal_entity_id is not None and contract.legal_entity_id != item.legal_entity_id:
            raise AssetError("Договор лизинга — другого юрлица")
        item.ownership = "leasing"
    if item.ownership not in OWNERSHIP:
        raise AssetError("Владение: собственное, лизинг или аренда")
    if not item.name.strip():
        raise AssetError("Не указано наименование")
    if not item.number.strip():
        raise AssetError("Не указан инвентарный номер")
    duplicate = db.scalar(select(AssetItem.id).where(AssetItem.organization_id == org,
                                                     AssetItem.number == item.number, AssetItem.id != item.id))
    if duplicate:
        raise AssetError(f"Инвентарный № {item.number} уже есть")


# --- связь с карточками основных средств ---


def ensure(db: Session, organization_id: int) -> bool:
    """Каждой карточке ОС — единица имущества; статус единицы следует за вводом и выбытием ОС."""
    changed = False
    linked = {i.fixed_asset_id: i for i in db.scalars(select(AssetItem).where(
        AssetItem.organization_id == organization_id, AssetItem.fixed_asset_id.is_not(None)))}
    category = db.scalar(select(AssetCategory.id).where(AssetCategory.organization_id == organization_id,
                                                        AssetCategory.name == "Оборудование"))
    for fa in db.scalars(select(FixedAsset).where(FixedAsset.organization_id == organization_id)
                         .order_by(FixedAsset.id)):
        status = "disposed" if fa.disposed_on else "in_use" if fa.commissioned_on else None
        item = linked.get(fa.id)
        if item is None:
            taken = fa.inventory_number and db.scalar(select(AssetItem.id).where(
                AssetItem.organization_id == organization_id, AssetItem.number == fa.inventory_number))
            number = fa.inventory_number if fa.inventory_number and not taken else next_numbers(db, organization_id, 1)[0]
            item = AssetItem(organization_id=organization_id, number=number, name=fa.name, category_id=category,
                             legal_entity_id=fa.legal_entity_id, cfo_id=fa.cfo_id, status=status or "stock",
                             purchase_price=fa.cost, acquired_on=fa.commissioned_on, fixed_asset_id=fa.id)
            db.add(item)
            db.flush()
            event(db, item, None, "created", "из карточки основного средства")
            changed = True
        else:
            # выбытие ОС — «выбыло»; отмена выбытия — обратно; ввод в эксплуатацию — со склада «в эксплуатации»
            new = None
            if status == "disposed" and item.status != "disposed":
                new = "disposed"
            elif item.status == "disposed" and status != "disposed":
                new = status or "stock"
            elif item.status == "stock" and status == "in_use":
                new = "in_use"
            if new:
                event(db, item, None, "edited",
                      f"статус: {ASSET_STATUSES[item.status]} → {ASSET_STATUSES[new]} (карточка основного средства)")
                item.status = new
                changed = True
    return changed


# --- Excel: реестр в формате загрузки и загрузка имеющегося имущества ---

STATUS_BY_TITLE = {t.lower(): k for k, t in ASSET_STATUSES.items() if k in MANUAL_STATUSES}
OWNERSHIP_BY_TITLE = {t.lower(): k for k, t in OWNERSHIP.items()}

ASSET_COLUMNS = [
    Column("Наименование", "name", True, 34),
    Column("Количество", "count", False, 11, hint="Сколько одинаковых единиц завести; пусто — 1"),
    Column("Инвентарный №", "number", False, 14, True, "Пусто — номер «ИН-000001» присвоится сам; при количестве больше 1 — пусто"),
    Column("Категория", "category", False, 20, hint="Из справочника категорий имущества"),
    Column("Владение", "ownership", False, 14, hint="Собственное, лизинг или аренда; пусто — собственное"),
    Column("Юрлицо (ИНН или название)", "entity", False, 22, True),
    Column("Серийный №", "serial", False, 16, True),
    Column("Место (ЦФО)", "cfo", False, 30, hint="Код ЦФО, путь «Компания / … / Магазин» или название"),
    Column("Склад", "warehouse", False, 18),
    Column("МОЛ", "employee", False, 26, hint="ФИО, табельный или внутренний № сотрудника"),
    Column("Статус", "status", False, 16, hint="На складе, в эксплуатации или в ремонте; пусто — на складе"),
    Column("Цена приобретения", "price", False, 15, hint="₽ за единицу"),
    Column("Дата приобретения", "acquired_on", False, 14, hint="ДД.ММ.ГГГГ"),
    Column("Примечание", "note", False, 30),
]


def registry_xlsx(db: Session, items: list[AssetItem] | None, organization_id: int) -> bytes:
    """Реестр (items) или пустой шаблон (None) в формате загрузки."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Имущество"
    _sheet_with_header(ws, ASSET_COLUMNS)
    if items:
        labels = Labels.load(db, organization_id)
        entities = {e.id: e for e in db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id))}
        for r, i in enumerate(items, start=2):
            entity = entities.get(i.legal_entity_id)
            values = [i.name, 1, i.number, labels.categories.get(i.category_id), OWNERSHIP[i.ownership],
                      entity.inn if entity else None, i.serial_number or None, labels.cfos.get(i.cfo_id),
                      labels.warehouses.get(i.warehouse_id), labels.employees.get(i.employee_id),
                      ASSET_STATUSES[i.status], i.purchase_price, i.acquired_on, i.note or None]
            for c, value in enumerate(values, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                if c == 13:
                    cell.number_format = "DD.MM.YYYY"
                elif c == 12:
                    cell.number_format = "#,##0.00"
    _help_sheet(wb, ASSET_COLUMNS, [
        "Ввод имеющегося имущества: одна строка — одна единица (или несколько одинаковых — колонка «Количество»).",
        "Каждая единица получает свою карточку и инвентарный номер.",
        "Строки с ошибками не загружаются — исправьте их и загрузите файл ещё раз (загруженные строки удалите из файла).",
    ])
    return _to_bytes(wb)


@dataclass
class ImportResult:
    rows: int = 0
    created: int = 0
    errors: list[RowError] = field(default_factory=list)


class _Lookups:
    def __init__(self, db: Session, organization_id: int):
        org = organization_id
        self.categories = list(db.scalars(select(AssetCategory).where(AssetCategory.organization_id == org)))
        self.entities = list(db.scalars(select(LegalEntity).where(LegalEntity.organization_id == org)))
        self.warehouses = list(db.scalars(select(Warehouse).where(Warehouse.organization_id == org)))
        self.employees = list(db.scalars(select(Employee).where(Employee.organization_id == org)))
        self.cfos = cfo_lookup(db, org)

    @staticmethod
    def _one(found: list, title: str, value: str):
        if not found:
            raise RowProblem(f"{title} «{value}» не найден(а) в организации")
        if len(found) > 1:
            raise RowProblem(f"{title} «{value}»: несколько с таким названием")
        return found[0].id

    def category(self, value: str) -> int:
        return self._one([c for c in self.categories if c.name.strip().lower() == value.lower()], "Категория", value)

    def entity(self, value) -> int:
        text_, digits = _text(value), _digits(value)
        found = [e for e in self.entities if digits and e.inn == digits] or \
            [e for e in self.entities if e.short_name.strip().lower() == text_.lower()]
        return self._one(found, "Юрлицо", text_)

    def warehouse(self, value: str) -> int:
        return self._one([w for w in self.warehouses if w.name.strip().lower() == value.lower()], "Склад", value)

    def employee(self, value: str) -> int:
        low = value.lower()
        found = [e for e in self.employees if low in (e.personnel_number.lower(), e.internal_number.lower())
                 and low] or [e for e in self.employees if e.full_name.strip().lower() == low]
        return self._one(found, "МОЛ", value)


def import_items(db: Session, organization_id: int, user: User, raw: bytes) -> ImportResult:
    rows = _read_rows(raw, ASSET_COLUMNS)
    result = ImportResult(rows=len(rows))
    look = _Lookups(db, organization_id)
    for row_number, row in rows:
        try:
            with db.begin_nested():
                name = _text(row.get("name"))[:255]
                if not name:
                    raise RowProblem("Не указано наименование")
                count = 1
                if _text(row.get("count")):
                    value = _decimal(row.get("count"), "Количество", 0)
                    if value is None or value < 1 or value > MAX_COUNT:
                        raise RowProblem(f"«Количество» — целое число от 1 до {MAX_COUNT}")
                    count = int(value)
                number = _text(row.get("number"))[:30]
                if number and count > 1:
                    raise RowProblem("Инвентарный № указывается только для одной единицы")
                values: dict = {"name": name, "serial_number": _text(row.get("serial"))[:100],
                                "note": _text(row.get("note"))[:2000], "ownership": "own", "status": "stock"}
                if _text(row.get("category")):
                    values["category_id"] = look.category(_text(row.get("category")))
                ownership = _text(row.get("ownership")).lower()
                if ownership:
                    if ownership not in OWNERSHIP_BY_TITLE:
                        raise RowProblem("«Владение»: собственное, лизинг или аренда")
                    values["ownership"] = OWNERSHIP_BY_TITLE[ownership]
                if _text(row.get("entity")):
                    values["legal_entity_id"] = look.entity(row.get("entity"))
                if _text(row.get("cfo")):
                    values["cfo_id"] = look.cfos.find(_text(row.get("cfo")))
                if _text(row.get("warehouse")):
                    values["warehouse_id"] = look.warehouse(_text(row.get("warehouse")))
                if _text(row.get("employee")):
                    values["employee_id"] = look.employee(_text(row.get("employee")))
                status = _text(row.get("status")).lower()
                if status:
                    if status not in STATUS_BY_TITLE:
                        raise RowProblem("«Статус»: на складе, в эксплуатации или в ремонте")
                    values["status"] = STATUS_BY_TITLE[status]
                if _text(row.get("price")):
                    price = _decimal(row.get("price"), "Цена приобретения", 2)
                    values["purchase_price"] = price  # больше нуля — проверяет _decimal
                if _text(row.get("acquired_on")):
                    values["acquired_on"] = _date(row.get("acquired_on"))
                numbers = [number] if number else next_numbers(db, organization_id, count)
                for n in numbers:
                    item = AssetItem(organization_id=organization_id, number=n, created_by=user.id, **values)
                    try:
                        validate(db, item)
                    except AssetError as exc:
                        raise RowProblem(str(exc)) from None
                    db.add(item)
                    db.flush()
                    event(db, item, user, "imported", "загружено из Excel")
                db.flush()
        except RowProblem as exc:
            result.errors.append(RowError(row_number, str(exc)))
            continue
        result.created += count
    return result


def search_condition(search: str):
    pattern = f"%{search.strip()}%"
    return or_(AssetItem.name.ilike(pattern), AssetItem.number.ilike(pattern), AssetItem.serial_number.ilike(pattern))


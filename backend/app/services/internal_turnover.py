"""Внутренние обороты между направлениями — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.1»).

Передача от ЦФО-продавца ЦФО-покупателю — одна проводка Дт 90.02 [покупатель, «Внутренняя закупка»] —
Кт 90.01 [продавец, «Внутренняя выручка»]: у продавца выручка, у покупателя расход, у компании в целом — ноль.
Деньги и баланс не меняются. Только внутри одного юрлица: продажи между юрлицами группы — настоящие продажи.
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.core.money import round_money
from app.models import Account, Cfo, Nomenclature, Operation, Posting, User, Warehouse
from app.models.journal import analytic_column
from app.models.price import PRICE_KINDS, InternalTransfer, InternalTransferLine
from app.schemas.journal import AnalyticsIn, OperationIn, PostingIn
from app.services import journal, prices, routes
from app.services.ledger import select_lines, subtree
from app.services.articles import system_article
from app.services.plan import _legal_entity


class InternalError(ValueError):
    pass


def articles(db: Session, organization_id: int):
    revenue = system_article(db, organization_id, "internal_revenue")
    purchase = system_article(db, organization_id, "internal_purchase")
    if revenue is None or purchase is None:
        raise InternalError("Нет системных статей «Внутренняя выручка» и «Внутренняя закупка»")
    return revenue, purchase


def create_transfer(db: Session, organization_id: int, user: User, *, on_date: dt.date, from_cfo_id: int,
                    to_cfo_id: int, amount: Decimal, description: str,
                    extra_postings: list[PostingIn] | None = None) -> Operation:
    """Проведённая операция внутренней передачи."""
    cfos = {}
    for cfo_id, title in ((from_cfo_id, "Продавец (ЦФО)"), (to_cfo_id, "Покупатель (ЦФО)")):
        cfo = db.get(Cfo, cfo_id)
        if cfo is None or cfo.organization_id != organization_id:
            raise InternalError(f"{title}: не найден в этой организации")
        cfos[cfo_id] = cfo
    if from_cfo_id == to_cfo_id:
        raise InternalError("Продавец и покупатель — один и тот же ЦФО")
    if amount <= 0:
        raise InternalError("Сумма должна быть больше нуля")
    entity = _legal_entity(db, organization_id, cfos[from_cfo_id])
    if _legal_entity(db, organization_id, cfos[to_cfo_id]) != entity:
        raise InternalError("ЦФО относятся к разным юрлицам — это продажа внутри группы, а не внутренняя передача")
    revenue, purchase = articles(db, organization_id)
    codes = dict(db.execute(select(Account.code, Account.id).where(Account.code.in_(("90.01", "90.02")))).all())
    text = description.strip() or f"Внутренняя передача: {cfos[from_cfo_id].name} → {cfos[to_cfo_id].name}"
    try:
        return journal.create_posted(db, organization_id, OperationIn(
            legal_entity_id=entity, op_date=on_date, article_id=revenue.id, description=text,
            postings=[PostingIn(debit_account_id=codes["90.02"], credit_account_id=codes["90.01"], amount=amount,
                                debit=AnalyticsIn(cfo=to_cfo_id, pl_item=purchase.id),
                                credit=AnalyticsIn(cfo=from_cfo_id, pl_item=revenue.id)),
                      *(extra_postings or [])]), user)
    except journal.JournalError as exc:
        raise InternalError(f"Проводка не прошла проверку журнала: {exc}") from None


# --- Документ передачи по количеству (docs/TZ.md, раздел 29, «Детали 20.4») ---

STOCK_ACCOUNTS = ("10", "41", "43")  # количественные счета: перемещение между складами разных направлений


@dataclass
class LineIn:
    nomenclature_id: int | None = None
    quantity: Decimal | None = None
    amount: Decimal | None = None  # сумма вручную (разовая передача без цены в справочнике)
    title: str = ""


@dataclass
class PricedLine:
    nomenclature_id: int | None
    title: str
    quantity: Decimal | None
    price_kind: str | None
    price: Decimal | None
    amount: Decimal


def price_kind(db: Session, from_cfo: Cfo) -> str:
    """Вид цены по звену передающего: производство → цена производства, логистика → логистики, торговля → дисконта."""
    link = prices.chain_link(db, from_cfo)
    if link not in prices.TRANSFER_PRICE:
        raise InternalError(f"У ЦФО «{from_cfo.name}» не указано звено цепочки (производство, логистика или торговля) — "
                            "укажите его в «Справочниках» → «ЦФО» или введите сумму вручную")
    return prices.TRANSFER_PRICE[link]


def price_lines(db: Session, organization_id: int, on_date: dt.date, from_cfo: Cfo, to_cfo: Cfo,
                lines: list[LineIn]) -> list[PricedLine]:
    """Строки с суммами: количество × цена без НДС (вид — по звену передающего, цена — для ЦФО получателя)."""
    if not lines:
        raise InternalError("Добавьте хотя бы одну строку")
    kind = None
    result, problems = [], []
    for i, line in enumerate(lines, start=1):
        item = None
        if line.nomenclature_id is not None:
            item = db.get(Nomenclature, line.nomenclature_id)
            if item is None or item.organization_id != organization_id:
                raise InternalError(f"Строка {i}: товар не найден в этой организации")
        title = item.name if item else (line.title.strip() or "Передача")
        if line.quantity is not None and line.quantity <= 0:
            raise InternalError(f"Строка {i}: количество должно быть больше нуля")
        if line.amount is not None:  # сумма введена вручную
            if line.amount <= 0:
                raise InternalError(f"Строка {i}: сумма должна быть больше нуля")
            result.append(PricedLine(line.nomenclature_id, title, line.quantity, None, None, line.amount))
            continue
        if item is None or line.quantity is None:
            raise InternalError(f"Строка {i}: укажите товар и количество или сумму вручную")
        kind = kind or price_kind(db, from_cfo)
        found = prices.price_on(db, organization_id, item.id, to_cfo.id, on_date, kind)
        if found is None:
            problems.append(f"«{item.name}» — нет цены «{PRICE_KINDS[kind].lower()}» на {on_date:%d.%m.%Y}")
            continue
        result.append(PricedLine(item.id, title, line.quantity, kind, found.net, round_money(line.quantity * found.net)))
    if problems:
        raise InternalError("Не хватает цен: " + "; ".join(problems))
    return result


@dataclass
class Writeoff:
    material_id: int
    title: str
    quantity: Decimal
    price: Decimal
    amount: Decimal
    by_stock: bool  # по средней цене склада сырья (иначе — по цене закупки)


def recipe_writeoff(db: Session, organization_id: int, on_date: dt.date, producer: Cfo,
                    priced: list[PricedLine]) -> list[Writeoff]:
    """Материалы по техкарте для передачи производства на СГП (docs/TZ.md, раздел 29, «Детали 20.7»): количество =
    передано × норма; цена — средняя по складу сырья на дату, если материала хватает, иначе цена закупки."""
    need: dict[int, Decimal] = {}
    for line in priced:
        if line.nomenclature_id is None or line.quantity is None:
            continue
        for r in routes.recipe_of(db, line.nomenclature_id):
            need[r.material_id] = need.get(r.material_id, Decimal(0)) + line.quantity * r.quantity
    if not need:
        return []
    raw = db.scalar(select(Warehouse).where(Warehouse.organization_id == organization_id, Warehouse.is_raw))
    if raw is None:
        raise InternalError("Не указан склад сырья производства: отметьте его в «Справочниках» → «Склады»")
    account_10 = list(db.scalars(select(Account.id).where(Account.code == "10")))
    result, problems = [], []
    for material_id, quantity in need.items():
        material = db.get(Nomenclature, material_id)
        quantity = quantity.quantize(Decimal("0.001"))
        lines = select_lines(organization_id, date_to=on_date, account_ids=account_10,
                             analytics={"nomenclature": material_id, "warehouse": raw.id}).subquery()
        sign = case((lines.c.side == "debit", 1), else_=-1)
        have_qty, have_amount = db.execute(select(
            func.coalesce(func.sum(sign * func.coalesce(lines.c.quantity, 0)), 0),
            func.coalesce(func.sum(sign * lines.c.amount_rub), 0))).one()
        have_qty, have_amount = Decimal(have_qty), Decimal(have_amount)
        if have_qty > 0 and have_qty >= quantity:
            price = round_money(have_amount / have_qty)
            amount = have_amount if quantity == have_qty else round_money(quantity * have_amount / have_qty)
            result.append(Writeoff(material_id, material.name, quantity, price, amount, True))
            continue
        found = prices.price_on(db, organization_id, material_id, producer.id, on_date, "purchase")
        if found is None:
            problems.append(f"«{material.name}» — на складе сырья не хватает, а цены закупки на {on_date:%d.%m.%Y} нет")
            continue
        result.append(Writeoff(material_id, material.name, quantity, found.net, round_money(quantity * found.net), False))
    if problems:
        raise InternalError("Не списать материалы по техкарте: " + "; ".join(problems))
    return [w for w in result if w.amount > 0]


def writeoff_postings(db: Session, organization_id: int, producer: Cfo, writeoffs: list[Writeoff]) -> list[PostingIn]:
    """Дт 90.02 [производство, «Себестоимость по техкарте»] — Кт 10 [материал, склад сырья, количество]."""
    if not writeoffs:
        return []
    cost = system_article(db, organization_id, "cost_of_sales")
    if cost is None:
        raise InternalError("Нет системной статьи «Себестоимость по техкарте»")
    raw = db.scalar(select(Warehouse).where(Warehouse.organization_id == organization_id, Warehouse.is_raw))
    codes = dict(db.execute(select(Account.code, Account.id).where(Account.code.in_(("10", "90.02")))).all())
    return [PostingIn(debit_account_id=codes["90.02"], credit_account_id=codes["10"], amount=w.amount,
                      quantity=w.quantity, debit=AnalyticsIn(cfo=producer.id, pl_item=cost.id),
                      credit=AnalyticsIn(nomenclature=w.material_id, warehouse=raw.id)) for w in writeoffs]


def _cfos(db: Session, organization_id: int, from_cfo_id: int, to_cfo_id: int) -> tuple[Cfo, Cfo]:
    found = []
    for cfo_id, title in ((from_cfo_id, "Кто передаёт (ЦФО)"), (to_cfo_id, "Кто получает (ЦФО)")):
        cfo = db.get(Cfo, cfo_id)
        if cfo is None or cfo.organization_id != organization_id:
            raise InternalError(f"{title}: не найден в этой организации")
        found.append(cfo)
    return found[0], found[1]


def create_document(db: Session, organization_id: int, user: User, *, on_date: dt.date, from_cfo_id: int,
                    to_cfo_id: int, lines: list[LineIn], description: str = "", source: str = "manual",
                    source_operation_id: int | None = None) -> InternalTransfer:
    """Документ передачи и его проведённая операция (одна проводка на всю сумму)."""
    from_cfo, to_cfo = _cfos(db, organization_id, from_cfo_id, to_cfo_id)
    priced = price_lines(db, organization_id, on_date, from_cfo, to_cfo, lines)
    total = sum((p.amount for p in priced), Decimal("0.00"))
    if source_operation_id is not None and db.scalar(select(InternalTransfer.id).where(
            InternalTransfer.source_operation_id == source_operation_id, InternalTransfer.status == "posted",
            InternalTransfer.from_cfo_id == from_cfo_id, InternalTransfer.to_cfo_id == to_cfo_id)):
        raise InternalError("По этому перемещению передача уже создана")
    db.execute(text("SELECT id FROM organizations WHERE id = :id FOR UPDATE"), {"id": organization_id})
    number = (db.scalar(select(func.max(InternalTransfer.number)).where(
        InternalTransfer.organization_id == organization_id)) or 0) + 1
    names = ", ".join(f"{p.title}{f' × {p.quantity.normalize():f}' if p.quantity is not None else ''}" for p in priced[:5])
    text_ = description.strip() or (f"Внутренняя передача № {number}: {from_cfo.name} → {to_cfo.name}"
                                     + (f" ({names}{'…' if len(priced) > 5 else ''})" if names else ""))
    # производство передаёт на СГП — списываются материалы по техкарте (себестоимость)
    extra = []
    if prices.chain_link(db, from_cfo) == "production":
        extra = writeoff_postings(db, organization_id, from_cfo,
                                  recipe_writeoff(db, organization_id, on_date, from_cfo, priced))
    op = create_transfer(db, organization_id, user, on_date=on_date, from_cfo_id=from_cfo_id, to_cfo_id=to_cfo_id,
                         amount=total, description=text_[:500], extra_postings=extra)
    doc = InternalTransfer(organization_id=organization_id, number=number, on_date=on_date, from_cfo_id=from_cfo_id,
                           to_cfo_id=to_cfo_id, description=description.strip()[:500], total=total, source=source,
                           source_operation_id=source_operation_id, operation_id=op.id, created_by=user.id)
    db.add(doc)
    db.flush()
    for p in priced:
        db.add(InternalTransferLine(transfer_id=doc.id, nomenclature_id=p.nomenclature_id, title=p.title[:255],
                                    quantity=p.quantity, price_kind=p.price_kind, price=p.price, amount=p.amount))
    db.flush()
    return doc


def cancel_document(db: Session, doc: InternalTransfer) -> None:
    """Отменить передачу: её операция удаляется (в закрытом периоде — нельзя, это проверит журнал)."""
    if doc.status != "posted":
        raise InternalError("Передача уже отменена")
    if doc.operation_id is not None:
        op = db.get(Operation, doc.operation_id)
        if op is not None:
            db.delete(op)
    doc.operation_id, doc.status = None, "cancelled"
    db.flush()


@dataclass
class Proposal:
    operation_id: int
    operation_number: int
    on_date: dt.date
    from_cfo_id: int
    to_cfo_id: int
    lines: list[LineIn] = field(default_factory=list)


def journal_proposals(db: Session, organization_id: int, date_from: dt.date, date_to: dt.date) -> list[Proposal]:
    """Проведённые перемещения товара между складами разных направлений (Дт 10/41/43 — Кт 10/41/43, склад
    у обеих сторон, ЦФО складов — с разными звеньями), по которым ещё нет передачи. Откуда — склад по кредиту."""
    stock = subtree(list(db.scalars(select(Account.id).where(Account.code.in_(STOCK_ACCOUNTS)))))
    dt_wh, ct_wh = getattr(Posting, analytic_column("debit", "warehouse")), getattr(Posting, analytic_column("credit", "warehouse"))
    dt_nom = getattr(Posting, analytic_column("debit", "nomenclature"))
    used = set(db.execute(select(InternalTransfer.source_operation_id, InternalTransfer.from_cfo_id,
                                 InternalTransfer.to_cfo_id).where(
        InternalTransfer.organization_id == organization_id, InternalTransfer.status == "posted",
        InternalTransfer.source_operation_id.is_not(None))).all())
    rows = db.execute(
        select(Operation.id, Operation.number, Operation.op_date, dt_wh, ct_wh, dt_nom, Posting.quantity)
        .join(Posting, Posting.operation_id == Operation.id)
        .where(Operation.organization_id == organization_id, Operation.status == "posted",
               Operation.op_date >= date_from, Operation.op_date <= date_to,
               Posting.debit_account_id.in_(stock), Posting.credit_account_id.in_(stock),
               dt_wh.is_not(None), ct_wh.is_not(None), dt_wh != ct_wh, Posting.quantity.is_not(None))
        .order_by(Operation.op_date, Operation.number)).all()
    warehouses = {w.id: w for w in db.scalars(select(Warehouse).where(Warehouse.organization_id == organization_id))}
    cfos = {c.id: c for c in db.scalars(select(Cfo).where(Cfo.organization_id == organization_id))}
    links: dict[int, str | None] = {}

    def link(cfo_id):
        if cfo_id not in links:
            links[cfo_id] = prices.chain_link(db, cfos.get(cfo_id))
        return links[cfo_id]

    result: dict[tuple, Proposal] = {}
    for op_id, op_number, op_date, to_wh, from_wh, nom, quantity in rows:
        to_cfo, from_cfo = warehouses[to_wh].cfo_id, warehouses[from_wh].cfo_id
        if to_cfo is None or from_cfo is None or to_cfo == from_cfo:
            continue
        if link(from_cfo) not in prices.TRANSFER_PRICE or link(from_cfo) == link(to_cfo):
            continue
        if (op_id, from_cfo, to_cfo) in used:
            continue
        prop = result.setdefault((op_id, from_cfo, to_cfo), Proposal(op_id, op_number, op_date, from_cfo, to_cfo))
        prop.lines.append(LineIn(nomenclature_id=nom, quantity=Decimal(quantity)))
    return list(result.values())

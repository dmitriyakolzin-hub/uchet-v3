"""Внутренние передачи между направлениями — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.1», «Детали 20.4»).

Смотреть — право просмотра; создавать и отменять (проведённая операция) — право проводить, не руководителю ЦФО.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Account, Cfo, Nomenclature, Operation, Posting, User
from app.models.journal import analytic_column
from app.models.price import PRICE_KINDS, TRANSFER_SOURCES, InternalTransfer, InternalTransferLine
from app.services import internal_turnover as service

router = APIRouter(prefix="/api", tags=["Журнал операций"])

Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]


class LineIn(BaseModel):
    nomenclature_id: int | None = None
    quantity: Quantity | None = None
    amount: Money | None = None  # сумма вручную — когда цены в справочнике нет
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""


class TransferIn(BaseModel):
    on_date: dt.date
    from_cfo_id: int  # кто передаёт: у него «Внутренняя выручка»
    to_cfo_id: int  # кто получает: у него «Внутренняя закупка»
    lines: Annotated[list[LineIn], Field(max_length=500)] = []
    amount: Money | None = None  # без строк — одна строка на эту сумму (как в 20.1)
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class PreviewIn(BaseModel):
    on_date: dt.date
    from_cfo_id: int
    to_cfo_id: int
    lines: Annotated[list[LineIn], Field(min_length=1, max_length=500)]


class LineOut(BaseModel):
    nomenclature_id: int | None
    title: str
    quantity: Decimal | None
    price_kind: str | None
    price_title: str
    price: Decimal | None
    amount: Decimal


class PreviewOut(BaseModel):
    lines: list[LineOut]
    total: Decimal


class TransferOut(BaseModel):
    id: int
    transfer_number: int
    number: int | None  # № операции журнала
    operation_id: int | None
    on_date: dt.date
    from_cfo_id: int
    from_name: str
    to_cfo_id: int
    to_name: str
    description: str
    total: Decimal
    source: str
    source_title: str
    source_operation_number: int | None
    status: str
    lines: list[LineOut] = []
    materials: list[dict] = []  # списание по техкарте при передаче производства на СГП (docs/TZ.md, «Детали 20.7»)


class ProposalOut(BaseModel):
    operation_id: int
    operation_number: int
    on_date: dt.date
    from_cfo_id: int
    from_name: str
    to_cfo_id: int
    to_name: str
    lines: list[LineOut]
    total: Decimal | None
    error: str = ""


class FromJournalIn(BaseModel):
    operation_ids: Annotated[list[int], Field(min_length=1, max_length=500)]
    date_from: dt.date
    date_to: dt.date


class FromJournalOut(BaseModel):
    created: list[int]
    errors: list[str]


def _line_out(line) -> LineOut:
    return LineOut(nomenclature_id=line.nomenclature_id, title=line.title, quantity=line.quantity,
                   price_kind=line.price_kind, price_title=PRICE_KINDS.get(line.price_kind or "", "вручную"),
                   price=line.price, amount=line.amount)


def _lines(body_lines: list[LineIn]) -> list[service.LineIn]:
    return [service.LineIn(**line.model_dump()) for line in body_lines]


def _out(db: Session, docs: list[InternalTransfer], with_lines: bool = False) -> list[TransferOut]:
    if not docs:
        return []
    cfos = dict(db.execute(select(Cfo.id, Cfo.name).where(
        Cfo.id.in_({d.from_cfo_id for d in docs} | {d.to_cfo_id for d in docs}))).all())
    op_ids = {d.operation_id for d in docs if d.operation_id} | {d.source_operation_id for d in docs
                                                                 if d.source_operation_id}
    numbers = dict(db.execute(select(Operation.id, Operation.number).where(Operation.id.in_(op_ids))).all()) \
        if op_ids else {}
    result = []
    for d in docs:
        out = TransferOut(id=d.id, transfer_number=d.number, number=numbers.get(d.operation_id),
                          operation_id=d.operation_id, on_date=d.on_date, from_cfo_id=d.from_cfo_id,
                          from_name=cfos.get(d.from_cfo_id, ""), to_cfo_id=d.to_cfo_id, to_name=cfos.get(d.to_cfo_id, ""),
                          description=d.description, total=d.total, source=d.source,
                          source_title=TRANSFER_SOURCES[d.source],
                          source_operation_number=numbers.get(d.source_operation_id), status=d.status)
        if with_lines:
            out.lines = [_line_out(line) for line in db.scalars(select(InternalTransferLine).where(
                InternalTransferLine.transfer_id == d.id).order_by(InternalTransferLine.id))]
            if d.operation_id:
                out.materials = _materials(db, d.operation_id)
        result.append(out)
    return result


def _materials(db: Session, operation_id: int) -> list[dict]:
    material = getattr(Posting, analytic_column("credit", "nomenclature"))
    rows = db.execute(select(Nomenclature.name, Posting.quantity, Posting.amount)
                      .join(Account, Account.id == Posting.credit_account_id)
                      .join(Nomenclature, Nomenclature.id == material)
                      .where(Posting.operation_id == operation_id, Account.code == "10")
                      .order_by(Posting.line_no)).all()
    return [{"title": n, "quantity": q, "amount": a} for n, q, a in rows]


def _can_post(db: Session, user: User, organization_id: int) -> None:
    get_organization_for(db, user, organization_id, Perm.POST)
    forbid_scoped(db, user, organization_id)


def _fail(db: Session, exc: Exception):
    db.rollback()
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


@router.post("/organizations/{organization_id}/internal-transfers", response_model=TransferOut, status_code=201)
def create_transfer(organization_id: int, body: TransferIn, user: CurrentUser, db: DbSession):
    _can_post(db, user, organization_id)
    lines = _lines(body.lines) or ([service.LineIn(amount=body.amount)] if body.amount else [])
    try:
        doc = service.create_document(db, organization_id, user, on_date=body.on_date, from_cfo_id=body.from_cfo_id,
                                      to_cfo_id=body.to_cfo_id, lines=lines, description=body.description)
    except service.InternalError as exc:
        _fail(db, exc)
    db.commit()
    return _out(db, [doc], with_lines=True)[0]


@router.post("/organizations/{organization_id}/internal-transfers/preview", response_model=PreviewOut)
def preview(organization_id: int, body: PreviewIn, user: CurrentUser, db: DbSession):
    """Суммы строк по ценам — для формы, ничего не сохраняет."""
    get_organization_for(db, user, organization_id, Perm.VIEW)
    try:
        from_cfo, to_cfo = service._cfos(db, organization_id, body.from_cfo_id, body.to_cfo_id)
        priced = service.price_lines(db, organization_id, body.on_date, from_cfo, to_cfo, _lines(body.lines))
    except service.InternalError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return PreviewOut(lines=[_line_out(p) for p in priced], total=sum((p.amount for p in priced), Decimal("0.00")))


@router.get("/organizations/{organization_id}/internal-transfers", response_model=list[TransferOut])
def list_transfers(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date | None = None,
                   date_to: dt.date | None = None, with_cancelled: bool = False):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    query = select(InternalTransfer).where(InternalTransfer.organization_id == organization_id)
    if date_from:
        query = query.where(InternalTransfer.on_date >= date_from)
    if date_to:
        query = query.where(InternalTransfer.on_date <= date_to)
    if not with_cancelled:
        query = query.where(InternalTransfer.status == "posted")
    return _out(db, list(db.scalars(query.order_by(InternalTransfer.on_date.desc(), InternalTransfer.number.desc())
                                    .limit(1000))))


def _doc(db: Session, user: User, transfer_id: int, perm: Perm) -> InternalTransfer:
    doc = db.get(InternalTransfer, transfer_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Передача не найдена")
    get_organization_for(db, user, doc.organization_id, perm)
    return doc


@router.get("/internal-transfers/{transfer_id}", response_model=TransferOut)
def get_transfer(transfer_id: int, user: CurrentUser, db: DbSession):
    return _out(db, [_doc(db, user, transfer_id, Perm.VIEW)], with_lines=True)[0]


@router.post("/internal-transfers/{transfer_id}/cancel", response_model=TransferOut)
def cancel_transfer(transfer_id: int, user: CurrentUser, db: DbSession):
    doc = _doc(db, user, transfer_id, Perm.VIEW)
    _can_post(db, user, doc.organization_id)
    try:
        service.cancel_document(db, doc)
    except service.InternalError as exc:
        _fail(db, exc)
    db.commit()
    return _out(db, [doc], with_lines=True)[0]


def _proposal_out(db: Session, organization_id: int, prop: service.Proposal, names: dict) -> ProposalOut:
    out = ProposalOut(operation_id=prop.operation_id, operation_number=prop.operation_number, on_date=prop.on_date,
                      from_cfo_id=prop.from_cfo_id, from_name=names.get(prop.from_cfo_id, ""),
                      to_cfo_id=prop.to_cfo_id, to_name=names.get(prop.to_cfo_id, ""), lines=[], total=None)
    try:
        from_cfo, to_cfo = service._cfos(db, organization_id, prop.from_cfo_id, prop.to_cfo_id)
        priced = service.price_lines(db, organization_id, prop.on_date, from_cfo, to_cfo, prop.lines)
        out.lines = [_line_out(p) for p in priced]
        out.total = sum((p.amount for p in priced), Decimal("0.00"))
    except service.InternalError as exc:
        out.error = str(exc)
    return out


@router.get("/organizations/{organization_id}/internal-transfers/journal", response_model=list[ProposalOut])
def journal_proposals(organization_id: int, date_from: dt.date, date_to: dt.date, user: CurrentUser, db: DbSession):
    """Перемещения товара между складами разных направлений, по которым ещё нет передачи."""
    get_organization_for(db, user, organization_id, Perm.VIEW)
    props = service.journal_proposals(db, organization_id, date_from, date_to)
    names = dict(db.execute(select(Cfo.id, Cfo.name).where(Cfo.organization_id == organization_id)).all())
    return [_proposal_out(db, organization_id, p, names) for p in props]


@router.post("/organizations/{organization_id}/internal-transfers/from-journal", response_model=FromJournalOut)
def from_journal(organization_id: int, body: FromJournalIn, user: CurrentUser, db: DbSession):
    """Создать передачи по отмеченным перемещениям; ошибочные пропускаются (с причиной)."""
    _can_post(db, user, organization_id)
    props = [p for p in service.journal_proposals(db, organization_id, body.date_from, body.date_to)
             if p.operation_id in set(body.operation_ids)]
    created, errors = [], []
    for prop in props:
        try:
            with db.begin_nested():
                doc = service.create_document(db, organization_id, user, on_date=prop.on_date,
                                              from_cfo_id=prop.from_cfo_id, to_cfo_id=prop.to_cfo_id, lines=prop.lines,
                                              source="journal", source_operation_id=prop.operation_id)
                created.append(doc.number)
        except service.InternalError as exc:
            errors.append(f"Операция № {prop.operation_number}: {exc}")
    db.commit()
    return FromJournalOut(created=created, errors=errors)


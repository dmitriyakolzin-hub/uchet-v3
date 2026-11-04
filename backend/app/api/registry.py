"""Реестр на оплату (docs/TZ.md, раздел 24, «Детали 14.4»). Всё — право проводить (финансовый менеджер,
финансовый директор, администратор)."""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.api.payment_requests import XLSX_TYPE, RequestOut, _out
from app.core.permissions import Perm
from app.models import BankAccount, PaymentRequest, User
from app.services import approval, registry
from app.services.report_xlsx import XlsxColumn, build

router = APIRouter(prefix="/api", tags=["Платёжный календарь"])


class RegistryRow(RequestOut):
    problems: list[str]
    warnings: list[str]


class GroupOut(BaseModel):
    pay_date: dt.date
    bank_account_id: int | None
    cash_cfo_id: int | None
    title: str
    total: Decimal
    available: Decimal | None
    shortage: Decimal
    overdue: bool
    rows: list[RegistryRow]


class RegistryOut(BaseModel):
    groups: list[GroupOut]
    total: Decimal
    count: int


class IdsIn(BaseModel):
    ids: Annotated[list[int], Field(min_length=1, max_length=500)]


class SentIn(IdsIn):
    sent: bool = True


def _build(db: Session, organization_id: int, date_from, date_to, include_sent: bool) -> RegistryOut:
    requests = registry.approved(db, organization_id, date_from, date_to, include_sent)
    today = dt.date.today()
    outs = {o.id: o for o in _out(db, requests, False)}
    accounts = {a.id: a for a in db.scalars(select(BankAccount).where(
        BankAccount.id.in_({r.bank_account_id for r in requests if r.bank_account_id})))}
    result = []
    for g in registry.groups(db, organization_id, requests, today):
        if g.cash_cfo_id is not None:
            title = registry.cash_name(db, g.cash_cfo_id)
        elif g.bank_account_id is not None:
            a = accounts[g.bank_account_id]
            title = f"{a.name} ··{a.number[-4:]}"
        else:
            title = "Расчётный счёт не выбран"
        rows = [RegistryRow(**outs[r.id].model_dump(), problems=registry.problems(db, r)
                            if r.cash_cfo_id is None else [], warnings=registry.warnings(r)) for r in g.requests]
        result.append(GroupOut(pay_date=g.pay_date, bank_account_id=g.bank_account_id, cash_cfo_id=g.cash_cfo_id,
                               title=title, total=g.total, available=g.available, shortage=g.shortage,
                               overdue=g.pay_date < today, rows=rows))
    return RegistryOut(groups=result, total=sum((g.total for g in result), Decimal("0.00")), count=len(requests))


@router.get("/organizations/{organization_id}/payment-registry", response_model=RegistryOut)
def get_registry(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date | None = None,
                 date_to: dt.date | None = None, include_sent: bool = True):
    """Согласованные неоплаченные выплаты по дням и счетам; без дат — все, в том числе просроченные."""
    get_organization_for(db, user, organization_id, Perm.POST)
    return _build(db, organization_id, date_from, date_to, include_sent)


@router.get("/organizations/{organization_id}/payment-registry.xlsx")
def download_registry(organization_id: int, user: CurrentUser, db: DbSession, date_from: dt.date | None = None,
                      date_to: dt.date | None = None, include_sent: bool = True):
    get_organization_for(db, user, organization_id, Perm.POST)
    data = _build(db, organization_id, date_from, date_to, include_sent)
    columns = [XlsxColumn("Дата / №", 14), XlsxColumn("Контрагент", 28), XlsxColumn("Договор", 18),
               XlsxColumn("Статья ДДС", 26), XlsxColumn("ЦФО", 20), XlsxColumn("Назначение", 44),
               XlsxColumn("Сумма", 15, money=True), XlsxColumn("Отправлено", 12), XlsxColumn("Замечания", 30)]
    rows = []
    for g in data.groups:
        note = f"не хватает {g.shortage:.2f}" if g.shortage else ""
        rows.append((0, [g.pay_date.strftime("%d.%m.%Y"), g.title, None, None, None,
                         f"Остаток: {g.available:.2f}" if g.available is not None else None, g.total, None, note], True))
        for r in g.rows:
            rows.append((1, [f"№ {r.number}", r.counterparty_name, r.contract_name, r.article_name, r.cfo_name,
                             r.description, r.amount, r.sent_at.strftime("%d.%m.%Y") if r.sent_at else "",
                             "; ".join(r.problems)]))
    period = " — ".join(d.strftime("%d.%m.%Y") for d in (date_from, date_to) if d) or "все даты"
    content = build("Реестр на оплату", f"Период: {period}", columns, rows,
                    total=["Итого", None, None, None, None, None, data.total, None, None])
    return Response(content, media_type=XLSX_TYPE,
                    headers={"Content-Disposition": 'attachment; filename="payment_registry.xlsx"'})


def _selected(db: Session, organization_id: int, ids: list[int]) -> list[PaymentRequest]:
    requests = list(db.scalars(select(PaymentRequest).where(PaymentRequest.id.in_(ids),
                                                            PaymentRequest.organization_id == organization_id)
                               .order_by(PaymentRequest.pay_date, PaymentRequest.number)))
    if len(requests) != len(set(ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Заявка не найдена")
    wrong = [r.number for r in requests if r.direction != "out" or r.status != "approved"]
    if wrong:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"В реестре только согласованные неоплаченные выплаты: № {', '.join(map(str, wrong))}")
    return requests


def _mark(db: Session, requests: list[PaymentRequest], user: User, sent: bool) -> None:
    now = dt.datetime.now(dt.UTC)
    for r in requests:
        if sent:
            r.sent_at, r.sent_by = now, user.id
            approval.event(db, r, user, "sent")
        elif r.sent_at is not None:
            r.sent_at = r.sent_by = None
            approval.event(db, r, user, "unsent")


@router.post("/organizations/{organization_id}/payment-registry/export")
def export_registry(organization_id: int, body: IdsIn, user: CurrentUser, db: DbSession):
    """Файл платёжных поручений для банк-клиента (формат 1С); выгруженные заявки — «отправлено в банк»."""
    get_organization_for(db, user, organization_id, Perm.POST)
    requests = _selected(db, organization_id, body.ids)
    try:
        content = registry.export_1c(db, requests)
    except registry.RegistryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    _mark(db, requests, user, True)
    db.commit()
    return Response(content, media_type="text/plain; charset=windows-1251",
                    headers={"Content-Disposition": 'attachment; filename="1c_to_kl.txt"'})


@router.post("/organizations/{organization_id}/payment-registry/sent", status_code=204)
def mark_sent(organization_id: int, body: SentIn, user: CurrentUser, db: DbSession) -> Response:
    """Отметить «отправлено в банк» без файла (платёжки заведены в банк-клиенте вручную) или снять отметку."""
    get_organization_for(db, user, organization_id, Perm.POST)
    _mark(db, _selected(db, organization_id, body.ids), user, body.sent)
    db.commit()
    return Response(status_code=204)

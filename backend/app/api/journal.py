"""Журнал операций (docs/TZ.md, раздел 9).

Права: просмотр — VIEW; черновики — DRAFT (без права POST — только свои); проведение,
возврат в черновик, аннулирование, восстановление — POST.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.access import get_organization_for, org_permissions
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Operation, OperationEvent, User
from app.models.journal import ANALYTIC_COLUMNS, analytic_column
from app.schemas.journal import (
    BulkIn,
    BulkOut,
    BulkResult,
    EventOut,
    OperationIn,
    OperationList,
    OperationOut,
    PostingOut,
)
from app.services import journal
from app.services.journal import JournalError
from app.services.ledger import select_lines

router = APIRouter(prefix="/api", tags=["Журнал операций"])

ACTION_PERMS = {"post": Perm.POST, "unpost": Perm.POST, "cancel": Perm.POST, "restore": Perm.POST}


def operation_out(op: Operation) -> OperationOut:
    postings = []
    for p in op.postings:
        sides = {
            side: {
                kind: getattr(p, analytic_column(side, kind))
                for kind in ANALYTIC_COLUMNS
                if getattr(p, analytic_column(side, kind)) is not None
            }
            for side in ("debit", "credit")
        }
        postings.append(PostingOut(
            id=p.id, line_no=p.line_no, debit_account_id=p.debit_account_id,
            credit_account_id=p.credit_account_id, amount=p.amount, currency_id=p.currency_id,
            amount_rub=p.amount_rub, amount_rub_manual=p.amount_rub_manual, quantity=p.quantity,
            debit=sides["debit"], credit=sides["credit"],
        ))
    return OperationOut(
        id=op.id, organization_id=op.organization_id, legal_entity_id=op.legal_entity_id,
        number=op.number, op_date=op.op_date, is_opening=op.is_opening,
        article_id=op.article_id, description=op.description,
        status=op.status, total_rub=sum((p.amount_rub or Decimal(0) for p in op.postings), Decimal(0)),
        postings=postings, created_by=op.created_by, created_at=op.created_at,
        updated_by=op.updated_by, updated_at=op.updated_at, posted_by=op.posted_by, posted_at=op.posted_at,
    )


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


def _forbidden(message: str = "Недостаточно прав") -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=message)


def _get_operation(db: Session, user: User, operation_id: int, perm: Perm) -> Operation:
    op = db.get(Operation, operation_id)
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="Операция не найдена")
    if op is None:
        raise not_found
    try:
        get_organization_for(db, user, op.organization_id, perm)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise not_found from None
        raise
    return op


def _check_can_change_draft(db: Session, user: User, op: Operation) -> None:
    """Черновик меняет тот, у кого есть право проводить, или его автор (руководитель ЦФО)."""
    if op.status != "draft":
        raise _unprocessable("Изменять и удалять можно только черновик — сначала верните операцию в черновик")
    if Perm.POST not in org_permissions(db, user, op.organization_id) and op.created_by != user.id:
        raise _forbidden("Можно изменять только свои черновики")


def _post_if_requested(db: Session, op: Operation, user: User, post_now: bool) -> None:
    if not post_now:
        return
    if Perm.POST not in org_permissions(db, user, op.organization_id):
        raise _forbidden("Нет права проводить операции")
    journal.post(db, op, user)


# --- Список и карточка ---


@router.get("/organizations/{organization_id}/operations", response_model=OperationList)
def list_operations(
    organization_id: int,
    user: CurrentUser,
    db: DbSession,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    status_: Annotated[list[str] | None, Query(alias="status")] = None,
    legal_entity_id: int | None = None,
    account_id: int | None = None,
    counterparty_id: int | None = None,
    article_id: int | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    opening: bool | None = None,  # true — только ввод остатков, false — без него, не указано — все
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OperationList:
    get_organization_for(db, user, organization_id, Perm.VIEW)
    query = select(Operation).where(Operation.organization_id == organization_id)
    if date_from:
        query = query.where(Operation.op_date >= date_from)
    if date_to:
        query = query.where(Operation.op_date <= date_to)
    if status_:
        query = query.where(Operation.status.in_(status_))
    if legal_entity_id:
        query = query.where(Operation.legal_entity_id == legal_entity_id)
    if q:
        query = query.where(Operation.description.ilike(f"%{q}%"))
    if opening is not None:
        query = query.where(Operation.is_opening == opening)
    if article_id:
        # статья в шапке или в аналитике проводок
        by_lines = select_lines(organization_id, statuses=("draft", "posted", "cancelled"))
        lines = by_lines.subquery()
        query = query.where(or_(
            Operation.article_id == article_id,
            Operation.id.in_(select(lines.c.operation_id).where(
                or_(lines.c.cash_flow_item == article_id, lines.c.pl_item == article_id))),
        ))
    if account_id or counterparty_id:
        # тот же отбор проводок, что и в отчётах (правило 6)
        lines = select_lines(
            organization_id, statuses=("draft", "posted", "cancelled"), account_id=account_id,
            analytics={"counterparty": counterparty_id} if counterparty_id else None,
        ).subquery()
        query = query.where(Operation.id.in_(select(lines.c.operation_id)))

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    ops = db.scalars(query.order_by(Operation.op_date.desc(), Operation.number.desc()).limit(limit).offset(offset))
    return OperationList(items=[operation_out(op) for op in ops], total=total)


@router.get("/operations/{operation_id}", response_model=OperationOut)
def get_operation(operation_id: int, user: CurrentUser, db: DbSession) -> OperationOut:
    return operation_out(_get_operation(db, user, operation_id, Perm.VIEW))


@router.get("/operations/{operation_id}/history", response_model=list[EventOut])
def operation_history(operation_id: int, user: CurrentUser, db: DbSession) -> list[EventOut]:
    _get_operation(db, user, operation_id, Perm.VIEW)
    rows = db.execute(
        select(OperationEvent, User)
        .join(User, User.id == OperationEvent.user_id)
        .where(OperationEvent.operation_id == operation_id)
        .order_by(OperationEvent.at, OperationEvent.id)
    )
    return [
        EventOut(action=e.action, at=e.at, user_id=u.id, user_name=u.full_name or u.login)
        for e, u in rows
    ]


# --- Создание, изменение, удаление ---


@router.post("/organizations/{organization_id}/operations", response_model=OperationOut, status_code=201)
def create_operation(
    organization_id: int, body: OperationIn, user: CurrentUser, db: DbSession, post: bool = False
) -> OperationOut:
    get_organization_for(db, user, organization_id, Perm.DRAFT)
    op = Operation(
        organization_id=organization_id, created_by=user.id, status="draft", is_opening=body.is_opening
    )
    try:
        journal.save_draft(db, op, body, user)
        op.number = journal.next_number(db, organization_id)
        db.add(op)
        db.flush()
        journal._event(db, op, user, "created")
        _post_if_requested(db, op, user, post)
    except JournalError as exc:
        db.rollback()
        raise _unprocessable(str(exc)) from None
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.expire_all()  # отдать значения так, как они записаны в базе (10.5 → 10.50)
    return operation_out(op)


@router.put("/operations/{operation_id}", response_model=OperationOut)
def update_operation(
    operation_id: int, body: OperationIn, user: CurrentUser, db: DbSession, post: bool = False
) -> OperationOut:
    op = _get_operation(db, user, operation_id, Perm.DRAFT)
    _check_can_change_draft(db, user, op)
    try:
        journal.save_draft(db, op, body, user)
        db.flush()
        journal._event(db, op, user, "updated")
        _post_if_requested(db, op, user, post)
    except JournalError as exc:
        db.rollback()
        raise _unprocessable(str(exc)) from None
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    db.expire_all()  # отдать значения так, как они записаны в базе (10.5 → 10.50)
    return operation_out(op)


@router.delete("/operations/{operation_id}", status_code=204)
def delete_operation(operation_id: int, user: CurrentUser, db: DbSession) -> None:
    op = _get_operation(db, user, operation_id, Perm.DRAFT)
    _check_can_change_draft(db, user, op)
    db.delete(op)
    db.commit()


# --- Смена статуса: провести / вернуть в черновик / аннулировать / восстановить ---


def _make_action_route(action: str):
    def run_action(operation_id: int, user: CurrentUser, db: DbSession) -> OperationOut:
        op = _get_operation(db, user, operation_id, ACTION_PERMS[action])
        try:
            journal.TRANSITIONS[action](db, op, user)
        except JournalError as exc:
            db.rollback()
            raise _unprocessable(str(exc)) from None
        db.commit()
        db.expire_all()
        return operation_out(op)

    run_action.__name__ = f"{action}_operation"
    return run_action


for _action in ACTION_PERMS:
    router.add_api_route(
        f"/operations/{{operation_id}}/{_action}", _make_action_route(_action),
        methods=["POST"], response_model=OperationOut,
    )


# --- Массовые действия ---


@router.post("/organizations/{organization_id}/operations/bulk", response_model=BulkOut)
def bulk_action(organization_id: int, body: BulkIn, user: CurrentUser, db: DbSession) -> BulkOut:
    get_organization_for(db, user, organization_id, Perm.VIEW)
    perms = org_permissions(db, user, organization_id)
    results = []
    for operation_id in dict.fromkeys(body.ids):  # без повторов, порядок сохраняется
        op = db.get(Operation, operation_id)
        if op is None or op.organization_id != organization_id:
            results.append(BulkResult(id=operation_id, ok=False, error="Операция не найдена"))
            continue
        try:
            with db.begin_nested():  # ошибка в одной операции не отменяет остальные
                if body.action == "delete":
                    if Perm.DRAFT not in perms:
                        raise JournalError("Недостаточно прав")
                    if op.status != "draft":
                        raise JournalError("Удалить можно только черновик")
                    if Perm.POST not in perms and op.created_by != user.id:
                        raise JournalError("Можно удалять только свои черновики")
                    db.delete(op)
                else:
                    if ACTION_PERMS[body.action] not in perms:
                        raise JournalError("Недостаточно прав")
                    journal.TRANSITIONS[body.action](db, op, user)
                db.flush()
            results.append(BulkResult(id=operation_id, ok=True))
        except JournalError as exc:
            results.append(BulkResult(id=operation_id, ok=False, error=str(exc)))
    db.commit()
    done = sum(r.ok for r in results)
    return BulkOut(done=done, failed=len(results) - done, results=results)

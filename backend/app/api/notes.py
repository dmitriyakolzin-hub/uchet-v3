"""Примечания к цифрам отчётов и к операциям журнала (docs/TZ.md, раздел 23).

Пишут и видят все, у кого есть просмотр организации (в том числе CEO); руководитель ЦФО — только примечания
своей ветки (ЦФО отчёта из его ветки или операция, которую он видит). Менять текст, метку и удалять — автор,
администратор и финансовый директор; отметить «решено» и вернуть в работу — любой, кто видит примечание.
"""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import branch_ids, cfo_scope, get_organization_for, org_permissions
from app.api.deps import CurrentUser, DbSession
from app.api.journal import _get_operation, _operation_cfos
from app.core.permissions import Perm
from app.models import Cfo, Executor, LegalEntity, Note, NoteLabel, Operation, User

router = APIRouter(prefix="/api", tags=["Примечания"])

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Key = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class NoteCreate(BaseModel):
    report: Literal["osv", "dds", "pl", "management", "balance", "debts", "stock", "planfact", "investments", "plan",
                    "operation"]
    variant: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] = ""
    row_key: Key = ""
    row_title: Short = ""
    column_key: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] = ""
    column_title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    legal_entity_id: int | None = None
    cfo_id: int | None = None
    executor_id: int | None = None
    operation_id: int | None = None
    label_id: int | None = None
    text: Text


class NoteUpdate(BaseModel):
    text: Text | None = None
    label_id: int | None = None
    clear_label: bool = False
    status: Literal["open", "resolved"] | None = None


class NoteOut(BaseModel):
    id: int
    organization_id: int
    report: str
    variant: str
    row_key: str
    row_title: str
    column_key: str
    column_title: str
    date_from: dt.date | None
    date_to: dt.date | None
    legal_entity_id: int | None
    cfo_id: int | None
    executor_id: int | None
    operation_id: int | None
    operation_number: int | None = None
    label_id: int | None
    text: str
    status: str
    created_by: int
    author: str
    created_at: dt.datetime
    updated_at: dt.datetime | None
    resolved_at: dt.datetime | None
    can_edit: bool = False


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Примечание не найдено")


def _visible(db: Session, note: Note, branch: set[int] | None) -> bool:
    if branch is None:
        return True
    if note.operation_id is not None:
        op = db.get(Operation, note.operation_id)
        return op is not None and bool(_operation_cfos(op) & branch)
    return note.cfo_id is not None and note.cfo_id in branch


def _out(db: Session, user: User, note: Note, supervisor: bool) -> NoteOut:
    author = db.get(User, note.created_by)
    op = db.get(Operation, note.operation_id) if note.operation_id else None
    return NoteOut(**{c: getattr(note, c) for c in NoteOut.model_fields if hasattr(note, c)},
                   author=(author.full_name or author.login) if author else "",
                   operation_number=op.number if op else None,
                   can_edit=supervisor or note.created_by == user.id)


def _branch(db: Session, user: User, organization_id: int) -> set[int] | None:
    scope = cfo_scope(db, user, organization_id)
    return None if scope is None else branch_ids(db, scope)


def _supervisor(db: Session, user: User, organization_id: int) -> bool:
    return Perm.POST in org_permissions(db, user, organization_id)


@router.get("/organizations/{organization_id}/notes", response_model=list[NoteOut])
def list_notes(organization_id: int, user: CurrentUser, db: DbSession, report: str | None = None,
               variant: str | None = None, operation_id: int | None = None,
               status_: Annotated[Literal["open", "resolved"] | None, Query(alias="status")] = None,
               label_id: int | None = None,
               mine: bool = False):
    """Примечания организации (новые — сверху); фильтры: отчёт и вариант, операция, статус, метка, «мои»."""
    get_organization_for(db, user, organization_id, Perm.VIEW)
    query = select(Note).where(Note.organization_id == organization_id)
    if report is not None:
        query = query.where(Note.report == report)
    if variant is not None:
        query = query.where(Note.variant == variant)
    if operation_id is not None:
        query = query.where(Note.operation_id == operation_id)
    if status_ is not None:
        query = query.where(Note.status == status_)
    if label_id is not None:
        query = query.where(Note.label_id == label_id)
    if mine:
        query = query.where(Note.created_by == user.id)
    branch = _branch(db, user, organization_id)
    supervisor = _supervisor(db, user, organization_id)
    notes = db.scalars(query.order_by(Note.created_at.desc(), Note.id.desc()).limit(2000))
    return [_out(db, user, n, supervisor) for n in notes if _visible(db, n, branch)]


def _check_refs(db: Session, organization_id: int, body: NoteCreate | NoteUpdate) -> None:
    pairs = [(NoteLabel, body.label_id, "Метка")]
    if isinstance(body, NoteCreate):
        pairs += [(LegalEntity, body.legal_entity_id, "Юрлицо"), (Cfo, body.cfo_id, "ЦФО"), (Executor, body.executor_id, "ЦФУ")]
    for model, value, title in pairs:
        if value is not None and ((obj := db.get(model, value)) is None or obj.organization_id != organization_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{title} не найдено в этой организации")


@router.post("/organizations/{organization_id}/notes", response_model=NoteOut, status_code=201)
def create_note(organization_id: int, body: NoteCreate, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    _check_refs(db, organization_id, body)
    data = body.model_dump()
    branch = _branch(db, user, organization_id)
    if body.report == "operation":
        if body.operation_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Не указана операция")
        op = _get_operation(db, user, body.operation_id, Perm.VIEW)  # своя организация и ветка, не план
        if op.organization_id != organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Операция не найдена")
        data.update(row_key=f"op:{op.id}", row_title=f"Операция № {op.number}")
    else:
        data["operation_id"] = None
        if branch is not None:  # руководитель ЦФО: отчёт всегда по его ветке
            if data["cfo_id"] is None:
                data["cfo_id"] = cfo_scope(db, user, organization_id)
            elif data["cfo_id"] not in branch:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="ЦФО не найдено в вашей ветке")
    note = Note(organization_id=organization_id, created_by=user.id, **data)
    db.add(note)
    db.commit()
    db.refresh(note)
    return _out(db, user, note, _supervisor(db, user, organization_id))


def _get_note(db: Session, user: User, note_id: int) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise _not_found()
    try:
        get_organization_for(db, user, note.organization_id, Perm.VIEW)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _not_found() from None
        raise
    if not _visible(db, note, _branch(db, user, note.organization_id)):
        raise _not_found()
    return note


@router.patch("/notes/{note_id}", response_model=NoteOut)
def update_note(note_id: int, body: NoteUpdate, user: CurrentUser, db: DbSession):
    note = _get_note(db, user, note_id)
    supervisor = _supervisor(db, user, note.organization_id)
    content = body.text is not None or body.label_id is not None or body.clear_label
    if content and not (supervisor or note.created_by == user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Менять примечание может автор, администратор "
                                                              "и финансовый директор")
    _check_refs(db, note.organization_id, body)
    now = dt.datetime.now(dt.UTC)
    if body.text is not None:
        note.text, note.updated_at = body.text, now
    if body.clear_label:
        note.label_id = None
    elif body.label_id is not None:
        note.label_id = body.label_id
    if body.status is not None and body.status != note.status:
        note.status = body.status
        note.resolved_by, note.resolved_at = (user.id, now) if body.status == "resolved" else (None, None)
    db.commit()
    db.refresh(note)
    return _out(db, user, note, supervisor)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, user: CurrentUser, db: DbSession) -> Response:
    note = _get_note(db, user, note_id)
    if not (_supervisor(db, user, note.organization_id) or note.created_by == user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Удалить примечание может автор, администратор "
                                                              "и финансовый директор")
    db.delete(note)
    db.commit()
    return Response(status_code=204)

"""Нормативы планирования — вкладка «Нормативы» страницы «Бюджеты» (docs/TZ.md, раздел 31, «Детали 23.1»).
Смотрят все с просмотром организации, задают финансовый директор и администратор (право SUPERVISE)."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Cfo, PlanNorm, ReportFormRow, User
from app.services import plan_norms as service
from app.services.articles import labels as article_labels

router = APIRouter(prefix="/api", tags=["Бюджет"])


class NormIn(BaseModel):
    article_id: int
    kind: Literal["calc", "fact"]
    cfo_id: int | None = None
    valid_from: dt.date
    base_article_id: int | None = None
    base_form_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None
    percent: Annotated[Decimal, Field(ge=0, le=100000, max_digits=9, decimal_places=4)] | None = None
    amount: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] | None = None
    comment: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class NormOut(BaseModel):
    id: int
    article_id: int
    article_name: str
    kind: str
    cfo_id: int | None
    cfo_name: str
    valid_from: dt.date
    base_article_id: int | None
    base_form_code: str | None
    base_title: str
    percent: Decimal | None
    amount: Decimal | None
    comment: str
    updated_by_name: str
    updated_at: dt.datetime


def _out(db: Session, organization_id: int, norms: list[PlanNorm]) -> list[NormOut]:
    names = article_labels(db, organization_id)
    cfos = dict(db.execute(select(Cfo.id, Cfo.name).where(Cfo.organization_id == organization_id)).all())
    rows = {r.code: r.name for r in db.scalars(select(ReportFormRow).where(
        ReportFormRow.organization_id == organization_id, ReportFormRow.code.is_not(None)))}
    users = {u.id: u.full_name or u.login for u in db.scalars(select(User).where(
        User.id.in_({n.updated_by for n in norms if n.updated_by})))}
    result = []
    for n in norms:
        if n.base_article_id is not None:
            base = names.get(n.base_article_id, "")
        elif n.base_form_code:
            base = f"{n.base_form_code} {rows.get(n.base_form_code, '')}".strip()
        else:
            base = ""
        result.append(NormOut(
            id=n.id, article_id=n.article_id, article_name=names.get(n.article_id, ""), kind=n.kind, cfo_id=n.cfo_id,
            cfo_name=cfos.get(n.cfo_id, "") if n.cfo_id else "", valid_from=n.valid_from,
            base_article_id=n.base_article_id, base_form_code=n.base_form_code, base_title=base, percent=n.percent,
            amount=n.amount, comment=n.comment, updated_by_name=users.get(n.updated_by, ""), updated_at=n.updated_at))
    return result


def _save(db: Session, norm: PlanNorm, body: NormIn, user: User) -> NormOut:
    for key, value in body.model_dump().items():
        setattr(norm, key, value)
    norm.updated_by = user.id
    db.add(norm)
    try:
        with db.no_autoflush:
            service.validate(db, norm)
        db.commit()
    except service.NormError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Такой норматив (статья, вид, ЦФО, месяц) уже есть — "
                                                             "измените его") from None
    db.refresh(norm)
    return _out(db, norm.organization_id, [norm])[0]


def _norm(db: Session, user: User, norm_id: int) -> PlanNorm:
    norm = db.get(PlanNorm, norm_id)
    if norm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Норматив не найден")
    try:
        get_organization_for(db, user, norm.organization_id, Perm.SUPERVISE)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Норматив не найден") from None
        raise
    return norm


@router.get("/organizations/{organization_id}/plan-norms", response_model=list[NormOut])
def list_norms(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    norms = list(db.scalars(select(PlanNorm).where(PlanNorm.organization_id == organization_id)
                            .order_by(PlanNorm.kind, PlanNorm.article_id, PlanNorm.cfo_id.nulls_first(),
                                      PlanNorm.valid_from)))
    result = _out(db, organization_id, norms)
    return sorted(result, key=lambda n: (n.kind, n.article_name, n.cfo_name, n.valid_from))


@router.post("/organizations/{organization_id}/plan-norms", response_model=NormOut, status_code=201)
def create_norm(organization_id: int, body: NormIn, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.SUPERVISE)
    return _save(db, PlanNorm(organization_id=organization_id), body, user)


@router.put("/plan-norms/{norm_id}", response_model=NormOut)
def update_norm(norm_id: int, body: NormIn, user: CurrentUser, db: DbSession):
    return _save(db, _norm(db, user, norm_id), body, user)


@router.delete("/plan-norms/{norm_id}", status_code=204)
def delete_norm(norm_id: int, user: CurrentUser, db: DbSession) -> Response:
    db.delete(_norm(db, user, norm_id))
    db.commit()
    return Response(status_code=204)

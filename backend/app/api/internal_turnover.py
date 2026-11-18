"""Внутренняя передача между направлениями — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.1»).
Создаёт проведённую операцию — право проводить (финансовый директор, бухгалтер)."""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints

from app.api.access import forbid_scoped, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.services import internal_turnover as service

router = APIRouter(prefix="/api", tags=["Журнал операций"])


class TransferIn(BaseModel):
    on_date: dt.date
    from_cfo_id: int  # продавец: у него «Внутренняя выручка»
    to_cfo_id: int  # покупатель: у него «Внутренняя закупка»
    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class TransferOut(BaseModel):
    operation_id: int
    number: int


@router.post("/organizations/{organization_id}/internal-transfers", response_model=TransferOut, status_code=201)
def create_transfer(organization_id: int, body: TransferIn, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.POST)
    forbid_scoped(db, user, organization_id)
    try:
        op = service.create_transfer(db, organization_id, user, **body.model_dump())
    except service.InternalError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return TransferOut(operation_id=op.id, number=op.number)

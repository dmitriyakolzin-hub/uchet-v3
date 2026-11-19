"""Внутренние цены по SKU — бюджет ЦФО (docs/TZ.md, раздел 29, «Детали 20.3»).
Смотреть — право просмотра; заводить, менять и удалять — право правки справочников."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Cfo, Nomenclature, User
from app.models.price import NomenclaturePrice
from app.services import prices as service
from app.services import routes

router = APIRouter(prefix="/api", tags=["Справочники"])

Price = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
Kind = Literal["production", "logistics", "sale", "discount"]


class PriceIn(BaseModel):
    nomenclature_id: int
    cfo_id: int | None = None  # пусто — для всей организации
    valid_from: dt.date
    production: Price | None = None
    logistics: Price | None = None
    sale: Price | None = None
    discount: Price | None = None
    vat_included: bool = False
    vat_rate: int = 0


class PriceOut(BaseModel):
    id: int
    nomenclature_id: int
    nomenclature_name: str
    cfo_id: int | None
    cfo_name: str
    valid_from: dt.date
    production: Decimal | None
    logistics: Decimal | None
    sale: Decimal | None
    discount: Decimal | None
    vat_included: bool
    vat_rate: int


class LookupOut(BaseModel):
    price: Decimal | None
    net: Decimal | None  # без НДС
    vat_included: bool = False
    vat_rate: int = 0
    valid_from: dt.date | None = None
    cfo_id: int | None = None  # чья цена; None — общая


def _out(db: Session, rows: list[NomenclaturePrice]) -> list[PriceOut]:
    names = dict(db.execute(select(Nomenclature.id, Nomenclature.name).where(
        Nomenclature.id.in_({r.nomenclature_id for r in rows}))).all()) if rows else {}
    cfos = dict(db.execute(select(Cfo.id, Cfo.name).where(
        Cfo.id.in_({r.cfo_id for r in rows if r.cfo_id}))).all()) if rows else {}
    return [PriceOut(id=r.id, nomenclature_id=r.nomenclature_id, nomenclature_name=names.get(r.nomenclature_id, ""),
                     cfo_id=r.cfo_id, cfo_name=cfos.get(r.cfo_id, ""), valid_from=r.valid_from,
                     production=r.production, logistics=r.logistics, sale=r.sale, discount=r.discount,
                     vat_included=r.vat_included, vat_rate=r.vat_rate) for r in rows]


def _save(db: Session, organization_id: int, record: NomenclaturePrice, body: PriceIn) -> None:
    data = body.model_dump()
    try:
        service.validate(db, organization_id, nomenclature_id=body.nomenclature_id, cfo_id=body.cfo_id,
                         vat_rate=body.vat_rate, prices={k: data[k] for k in ("production", "logistics", "sale",
                                                                              "discount")})
    except service.PriceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    clash = db.scalar(select(NomenclaturePrice.id).where(
        NomenclaturePrice.nomenclature_id == body.nomenclature_id, NomenclaturePrice.valid_from == body.valid_from,
        NomenclaturePrice.cfo_id.is_(None) if body.cfo_id is None else NomenclaturePrice.cfo_id == body.cfo_id,
        NomenclaturePrice.id != (record.id or 0)))
    if clash:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="На эту дату для этого ЦФО цена уже есть — измените её")
    for key, value in data.items():
        setattr(record, key, value)


@router.get("/organizations/{organization_id}/nomenclature-prices", response_model=list[PriceOut])
def list_prices(organization_id: int, user: CurrentUser, db: DbSession, nomenclature_id: int | None = None):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    query = select(NomenclaturePrice).where(NomenclaturePrice.organization_id == organization_id)
    if nomenclature_id is not None:
        query = query.where(NomenclaturePrice.nomenclature_id == nomenclature_id)
    rows = list(db.scalars(query.order_by(NomenclaturePrice.nomenclature_id, NomenclaturePrice.valid_from.desc())
                           .limit(5000)))
    return _out(db, rows)


@router.post("/organizations/{organization_id}/nomenclature-prices", response_model=PriceOut, status_code=201)
def add_price(organization_id: int, body: PriceIn, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    record = NomenclaturePrice(organization_id=organization_id, created_by=user.id)
    _save(db, organization_id, record, body)
    db.add(record)
    db.commit()
    return _out(db, [record])[0]


def _record(db: Session, user: User, price_id: int) -> NomenclaturePrice:
    record = db.get(NomenclaturePrice, price_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Цена не найдена")
    get_organization_for(db, user, record.organization_id, Perm.EDIT_REFERENCE)
    return record


@router.put("/nomenclature-prices/{price_id}", response_model=PriceOut)
def update_price(price_id: int, body: PriceIn, user: CurrentUser, db: DbSession):
    record = _record(db, user, price_id)
    _save(db, record.organization_id, record, body)
    db.commit()
    return _out(db, [record])[0]


@router.delete("/nomenclature-prices/{price_id}", status_code=204)
def delete_price(price_id: int, user: CurrentUser, db: DbSession) -> Response:
    record = _record(db, user, price_id)
    db.delete(record)
    db.commit()
    return Response(status_code=204)


@router.get("/organizations/{organization_id}/nomenclature-prices/lookup", response_model=LookupOut)
def lookup(organization_id: int, nomenclature_id: int, kind: Kind, on_date: dt.date, user: CurrentUser,
           db: DbSession, cfo_id: int | None = None):
    """Цена на дату для ЦФО: своя, родителей или общая."""
    get_organization_for(db, user, organization_id, Perm.VIEW)
    found = service.price_on(db, organization_id, nomenclature_id, cfo_id, on_date, kind)
    if found is None:
        return LookupOut(price=None, net=None)
    return LookupOut(price=found.price, net=found.net, vat_included=found.vat_included, vat_rate=found.vat_rate,
                     valid_from=found.valid_from, cfo_id=found.cfo_id)


# --- Состав доготавливаемого товара (docs/TZ.md, раздел 29, «Детали 20.5») ---

Norm = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]


class ComponentIn(BaseModel):
    component_id: int
    quantity: Norm


class ComponentOut(BaseModel):
    component_id: int
    name: str
    article: str
    route: str | None
    quantity: Decimal


def _item(db: Session, user: User, item_id: int, perm: Perm) -> Nomenclature:
    item = db.get(Nomenclature, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Номенклатура не найдена")
    get_organization_for(db, user, item.organization_id, perm)
    return item


def _components_out(db: Session, item: Nomenclature) -> list[ComponentOut]:
    rows = routes.components_of(db, item.id)
    parts = {n.id: n for n in db.scalars(select(Nomenclature).where(
        Nomenclature.id.in_([r.component_id for r in rows])))} if rows else {}
    return [ComponentOut(component_id=r.component_id, name=parts[r.component_id].name,
                         article=parts[r.component_id].article, route=parts[r.component_id].route,
                         quantity=r.quantity) for r in rows]


@router.get("/nomenclature/{item_id}/components", response_model=list[ComponentOut])
def list_components(item_id: int, user: CurrentUser, db: DbSession):
    return _components_out(db, _item(db, user, item_id, Perm.VIEW))


@router.put("/nomenclature/{item_id}/components", response_model=list[ComponentOut])
def put_components(item_id: int, body: Annotated[list[ComponentIn], Field(max_length=100)], user: CurrentUser,
                   db: DbSession):
    """Заменить состав: что и сколько на 1 единицу товара (только у маршрута «Доготовка на точке»)."""
    item = _item(db, user, item_id, Perm.EDIT_REFERENCE)
    try:
        routes.set_components(db, item, [routes.ComponentIn(c.component_id, c.quantity) for c in body])
    except routes.RouteError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    db.commit()
    return _components_out(db, item)

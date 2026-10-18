"""Валюты и курсы. Справочник общий для всех организаций."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.money import BASE_CURRENCY
from app.core.permissions import ROLE_PERMS, Perm, Role
from app.models import Currency, ExchangeRate, Membership, User
from app.schemas.currency import (
    CurrencyCreate,
    CurrencyOut,
    CurrencyUpdate,
    EffectiveRateOut,
    RateIn,
    RateOut,
)
from app.services.rates import RateNotFound, rate_on

router = APIRouter(prefix="/api", tags=["Валюты"])


def get_rates_editor(user: CurrentUser, db: DbSession) -> User:
    """Курсы правят администратор и те, у кого есть право изменять справочники хотя бы в одной организации."""
    if user.is_admin:
        return user
    roles = db.scalars(select(Membership.role).where(Membership.user_id == user.id))
    if any(Perm.EDIT_REFERENCE in ROLE_PERMS[Role(r)] for r in roles):
        return user
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")


RatesEditor = Annotated[User, Depends(get_rates_editor)]


def _currency_out(currency: Currency) -> CurrencyOut:
    out = CurrencyOut.model_validate(currency)
    out.is_base = currency.code == BASE_CURRENCY
    return out


def _get_currency(db: Session, currency_id: int) -> Currency:
    currency = db.get(Currency, currency_id)
    if currency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Валюта не найдена")
    return currency


def _get_foreign_currency(db: Session, currency_id: int) -> Currency:
    currency = _get_currency(db, currency_id)
    if currency.code == BASE_CURRENCY:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Курс рубля всегда равен 1, его не вводят"
        )
    return currency


def _commit(db: Session, conflict_message: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=conflict_message) from None


# --- Валюты ---


@router.get("/currencies", response_model=list[CurrencyOut])
def list_currencies(_: CurrentUser, db: DbSession) -> list[CurrencyOut]:
    currencies = db.scalars(select(Currency))
    # Рубль первым, остальные по коду
    return sorted(
        (_currency_out(c) for c in currencies), key=lambda c: (not c.is_base, c.code)
    )


@router.post("/currencies", response_model=CurrencyOut, status_code=201)
def create_currency(body: CurrencyCreate, _: AdminUser, db: DbSession) -> CurrencyOut:
    currency = Currency(**body.model_dump())
    db.add(currency)
    _commit(db, "Валюта с таким буквенным или цифровым кодом уже есть")
    return _currency_out(currency)


@router.patch("/currencies/{currency_id}", response_model=CurrencyOut)
def update_currency(
    currency_id: int, body: CurrencyUpdate, _: AdminUser, db: DbSession
) -> CurrencyOut:
    currency = _get_currency(db, currency_id)
    if body.is_active is False and currency.code == BASE_CURRENCY:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Базовую валюту отключить нельзя")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(currency, field, value)
    db.commit()
    return _currency_out(currency)


# --- Курсы ---


@router.get("/currencies/{currency_id}/rates", response_model=list[RateOut])
def list_rates(currency_id: int, _: CurrentUser, db: DbSession) -> list[ExchangeRate]:
    _get_currency(db, currency_id)
    return list(
        db.scalars(
            select(ExchangeRate)
            .where(ExchangeRate.currency_id == currency_id)
            .order_by(ExchangeRate.rate_date.desc())
        )
    )


@router.post("/currencies/{currency_id}/rates", response_model=RateOut, status_code=201)
def create_rate(currency_id: int, body: RateIn, _: RatesEditor, db: DbSession) -> ExchangeRate:
    _get_foreign_currency(db, currency_id)
    rate = ExchangeRate(currency_id=currency_id, **body.model_dump())
    db.add(rate)
    _commit(db, "Курс на эту дату уже введён — исправьте его")
    db.refresh(rate)  # вернуть значение так, как оно хранится в базе (6 знаков)
    return rate


@router.put("/exchange-rates/{rate_id}", response_model=RateOut)
def update_rate(rate_id: int, body: RateIn, _: RatesEditor, db: DbSession) -> ExchangeRate:
    rate = db.get(ExchangeRate, rate_id)
    if rate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Курс не найден")
    for field, value in body.model_dump().items():
        setattr(rate, field, value)
    _commit(db, "Курс на эту дату уже введён")
    db.refresh(rate)
    return rate


@router.delete("/exchange-rates/{rate_id}", status_code=204)
def delete_rate(rate_id: int, _: RatesEditor, db: DbSession) -> None:
    rate = db.get(ExchangeRate, rate_id)
    if rate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Курс не найден")
    db.delete(rate)
    db.commit()


@router.get("/currencies/{currency_id}/rate", response_model=EffectiveRateOut)
def effective_rate(
    currency_id: int,
    _: CurrentUser,
    db: DbSession,
    on: Annotated[dt.date, Query(description="Дата, на которую нужен курс")],
) -> EffectiveRateOut:
    currency = _get_currency(db, currency_id)
    try:
        found = rate_on(db, currency, on)
    except RateNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    return EffectiveRateOut(
        currency=currency.code, on_date=on, rate_date=found.rate_date,
        rate=found.rate, nominal=found.nominal,
    )

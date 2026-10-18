"""План счетов: общий для всех организаций, изменяет администратор, смотрят все."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.core.analytics import ANALYTIC_TITLES, AnalyticKind
from app.models import Account, AccountAnalytic
from app.schemas.account import AccountCreate, AccountOut, AccountUpdate, AnalyticKindOut

router = APIRouter(prefix="/api", tags=["План счетов"])


def _code_key(code: str) -> tuple[int, ...]:
    return tuple(int(part) for part in code.split("."))


def _account_out(account: Account, parent_ids: set[int]) -> AccountOut:
    return AccountOut(
        id=account.id,
        code=account.code,
        name=account.name,
        parent_id=account.parent_id,
        account_type=account.account_type,
        is_quantitative=account.is_quantitative,
        is_currency=account.is_currency,
        is_active=account.is_active,
        analytics=[a.kind for a in account.analytics],
        has_children=account.id in parent_ids,
    )


def _parent_ids(db: Session) -> set[int]:
    return set(db.scalars(select(Account.parent_id).where(Account.parent_id.is_not(None))))


def _set_analytics(db: Session, account: Account, kinds: list[AnalyticKind]) -> None:
    # Сначала удаляем старый список: иначе новая строка того же вида столкнётся
    # со старой на уникальности (счёт, вид)
    if account.analytics:
        account.analytics.clear()
        db.flush()
    account.analytics = [
        AccountAnalytic(kind=kind.value, position=position)
        for position, kind in enumerate(kinds, start=1)
    ]


@router.get("/analytic-kinds", response_model=list[AnalyticKindOut])
def list_analytic_kinds(_: CurrentUser) -> list[AnalyticKindOut]:
    return [AnalyticKindOut(code=kind, title=ANALYTIC_TITLES[kind]) for kind in AnalyticKind]


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(_: CurrentUser, db: DbSession) -> list[AccountOut]:
    parents = _parent_ids(db)
    accounts = sorted(db.scalars(select(Account)), key=lambda a: _code_key(a.code))
    return [_account_out(a, parents) for a in accounts]


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountCreate, _: AdminUser, db: DbSession) -> AccountOut:
    parent = None
    if "." in body.code:
        parent_code = body.code.rsplit(".", 1)[0]
        parent = db.scalar(select(Account).where(Account.code == parent_code))
        if parent is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Сначала заведите счёт {parent_code}: субсчёт создаётся внутри него",
            )
    if parent is None and body.account_type is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите вид счёта"
        )

    def inherit(value, parent_value):
        return parent_value if value is None and parent is not None else value

    account = Account(
        code=body.code,
        name=body.name,
        parent_id=parent.id if parent else None,
        account_type=inherit(body.account_type, parent.account_type if parent else None),
        is_quantitative=bool(inherit(body.is_quantitative, parent.is_quantitative if parent else None)),
        is_currency=bool(inherit(body.is_currency, parent.is_currency if parent else None)),
    )
    kinds = body.analytics
    if kinds is None:
        kinds = [AnalyticKind(a.kind) for a in parent.analytics] if parent else []
    _set_analytics(db, account, kinds)
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Счёт {body.code} уже есть") from None
    return _account_out(account, _parent_ids(db))


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: int, body: AccountUpdate, _: AdminUser, db: DbSession) -> AccountOut:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Счёт не найден")
    data = body.model_dump(exclude_unset=True)
    kinds = data.pop("analytics", None)
    for field, value in data.items():
        if value is not None:
            setattr(account, field, value)
    if kinds is not None:
        _set_analytics(db, account, [AnalyticKind(k) for k in kinds])
    db.commit()
    db.refresh(account)
    return _account_out(account, _parent_ids(db))

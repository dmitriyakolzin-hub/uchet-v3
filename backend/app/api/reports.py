"""Отчёты (docs/TZ.md, раздел 12). Просмотр — право VIEW (отчёты видят все, у кого есть доступ
к организации). Расшифровка любой цифры — /reports/lines с теми же фильтрами."""

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.access import branch_ids, cfo_scope, get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.analytics import ANALYTIC_TITLES, AnalyticKind
from app.core.permissions import Perm
from app.models import Account, Budget, BudgetVersion, Cfo, LegalEntity
from app.models.journal import ANALYTIC_COLUMNS
from app.services import report_xlsx as xlsx
from app.services import reports
from app.services.excel_import import XLSX_TYPE
from app.services.reports import ReportFilter

router = APIRouter(prefix="/api/organizations/{organization_id}/reports", tags=["Отчёты"])


def report_filter(
    organization_id: int,
    user: CurrentUser,
    db: DbSession,
    date_from: dt.date,
    date_to: dt.date,
    legal_entity_id: int | None = None,
    cfo_id: int | None = None,
    drafts: bool = False,
) -> ReportFilter:
    """Общие фильтры отчётов + проверка права просмотра и принадлежности юрлица и ЦФО организации."""
    get_organization_for(db, user, organization_id, Perm.VIEW)
    if date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Начало периода позже его конца")
    for model, value, title in ((LegalEntity, legal_entity_id, "Юрлицо"), (Cfo, cfo_id, "ЦФО")):
        if value is not None:
            obj = db.get(model, value)
            if obj is None or obj.organization_id != organization_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{title} не найдено в этой организации")
    scope = cfo_scope(db, user, organization_id)
    if scope is not None:
        # руководитель ЦФО видит отчёты только по своей ветке
        if cfo_id is None:
            cfo_id = scope
        elif cfo_id not in branch_ids(db, scope):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="ЦФО не найдено в вашей ветке")
    return ReportFilter(organization_id, date_from, date_to, legal_entity_id, cfo_id, drafts)


Filter = Annotated[ReportFilter, Depends(report_filter)]


class BalancesOut(BaseModel):
    opening_debit: Decimal
    opening_credit: Decimal
    debit: Decimal
    credit: Decimal
    closing_debit: Decimal
    closing_credit: Decimal


class OsvRowOut(BalancesOut):
    account_id: int
    code: str
    name: str
    parent_id: int | None
    has_analytics: bool


class OsvOut(BaseModel):
    rows: list[OsvRowOut]
    total: BalancesOut


class DetailRowOut(BalancesOut):
    value: int | None
    name: str


class DetailsOut(BaseModel):
    kind: str
    kinds: list[dict[str, str]]  # аналитики счёта, по которым можно раскрыть
    rows: list[DetailRowOut]
    total: BalancesOut


class DrillRowOut(BaseModel):
    operation_id: int
    number: int
    op_date: dt.date
    status: str
    description: str
    account: str
    corr_account: str
    side: str
    debit: Decimal | None
    credit: Decimal | None
    analytics: str


class DrillOut(BaseModel):
    rows: list[DrillRowOut]
    count: int
    limit: int
    debit: Decimal
    credit: Decimal


def _osv(db: Session, f: ReportFilter) -> OsvOut:
    result = reports.osv(db, f)
    return OsvOut(
        rows=[OsvRowOut(account_id=r.account_id, code=r.code, name=r.name, parent_id=r.parent_id,
                        has_analytics=r.has_analytics, **r.balances.out()) for r in result.rows],
        total=BalancesOut(**result.total_out),
    )


@router.get("/osv", response_model=OsvOut)
def get_osv(f: Filter, db: DbSession):
    return _osv(db, f)


def _account_with_analytics(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Счёт не найден")
    return account


@router.get("/osv/details", response_model=DetailsOut)
def get_osv_details(f: Filter, db: DbSession, account_id: int, kind: AnalyticKind | None = None):
    """ОСВ по одному счёту в разрезе одной из его аналитик (по умолчанию — первой)."""
    account = _account_with_analytics(db, account_id)
    kinds = [a.kind for a in account.analytics]
    if not kinds:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"По счёту {account.code} аналитика не ведётся")
    chosen = (kind.value if kind else None) or kinds[0]
    if chosen not in kinds:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"По счёту {account.code} эта аналитика не ведётся")
    rows, total = reports.osv_details(db, f, account_id, chosen)
    return DetailsOut(
        kind=chosen,
        kinds=[{"code": k, "title": ANALYTIC_TITLES[AnalyticKind(k)]} for k in kinds],
        rows=[DetailRowOut(value=r.value, name=r.name, **r.balances.out()) for r in rows],
        total=BalancesOut(**total),
    )


def _analytics_param(values: list[str]) -> dict[str, int | None]:
    """«kind:id» или «kind:none» → {kind: id | None}."""
    result: dict[str, int | None] = {}
    for item in values:
        kind, _, value = item.partition(":")
        if kind not in ANALYTIC_COLUMNS or not (value == "none" or value.isdigit()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Неверный фильтр аналитики «{item}»")
        result[kind] = None if value == "none" else int(value)
    return result


@router.get("/lines", response_model=DrillOut)
def get_lines(
    f: Filter,
    db: DbSession,
    period: Literal["opening", "turnover", "closing"] = "turnover",
    account_id: Annotated[list[int] | None, Query()] = None,
    side: Literal["debit", "credit"] | None = None,
    a: Annotated[list[str] | None, Query(description="аналитика: kind:id или kind:none")] = None,
    exclude_corr_account_id: Annotated[list[int] | None, Query()] = None,
    opening: bool | None = None,
    plan_version_id: int | None = None,
):
    """Расшифровка: проводки, из которых сложилась цифра отчёта (те же условия отбора).
    plan_version_id — плановые операции этой версии бюджета вместо факта."""
    if plan_version_id is not None:
        _check_version(db, f.organization_id, plan_version_id)
        months = [c.key for c in reports.month_columns(f.date_from, f.date_to)[:-1]]
        f = replace(f, plan_versions=tuple((m, plan_version_id) for m in months))
    result = reports.drill(db, f, period, account_ids=account_id, side=side, analytics=_analytics_param(a or []),
                           exclude_corr_account_ids=exclude_corr_account_id, opening=opening)
    return DrillOut(rows=result.rows, count=result.count, limit=reports.DRILL_LIMIT,
                    debit=result.debit, credit=result.credit)


class ColumnOut(BaseModel):
    key: str
    title: str
    date_from: dt.date
    date_to: dt.date


class ReportRowOut(BaseModel):
    key: str
    title: str
    level: int
    kind: str
    values: dict[str, Decimal]
    drill: dict | None


class ColumnsReportOut(BaseModel):
    """Отчёт с колонками по месяцам (ДДС, ОПиУ)."""

    columns: list[ColumnOut]
    money_account_ids: list[int] = []
    rows: list[ReportRowOut]


def _dds(db: Session, f: ReportFilter) -> ColumnsReportOut:
    result = reports.dds(db, f)
    return ColumnsReportOut(
        columns=[c.__dict__ for c in result.columns], money_account_ids=result.money_account_ids,
        rows=[r.__dict__ for r in result.rows],
    )


def _pl(db: Session, f: ReportFilter) -> ColumnsReportOut:
    result = reports.pl(db, f)
    return ColumnsReportOut(columns=[c.__dict__ for c in result.columns], rows=[r.__dict__ for r in result.rows])


@router.get("/pl", response_model=ColumnsReportOut)
def get_pl(f: Filter, db: DbSession):
    return _pl(db, f)


def _balance(db: Session, f: ReportFilter) -> ColumnsReportOut:
    result = reports.balance(db, f)
    return ColumnsReportOut(columns=[c.__dict__ for c in result.columns], rows=[r.__dict__ for r in result.rows])


@router.get("/balance", response_model=ColumnsReportOut)
def get_balance(f: Filter, db: DbSession):
    """Управленческий баланс на начало (день перед началом периода) и на конец периода."""
    return _balance(db, f)


class BalanceDetailOut(BaseModel):
    account_id: int
    account: str
    analytics: dict[str, int | None]
    name: str
    amount: Decimal


class BalanceDetailsOut(BaseModel):
    line: str
    title: str
    date: dt.date
    rows: list[BalanceDetailOut]
    total: Decimal


@router.get("/balance/details", response_model=BalanceDetailsOut)
def get_balance_details(f: Filter, db: DbSession, line: str):
    """Строка баланса с развёрнутым сальдо (дебиторка, кредиторка): по счетам и контрагентам на дату date_to."""
    if line not in reports.BALANCE_LINES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Нет такой строки баланса")
    rows = reports.balance_details(db, f, line)
    return BalanceDetailsOut(line=line, title=reports.BALANCE_LINES[line][1], date=f.date_to,
                             rows=[r.__dict__ for r in rows], total=sum((r.amount for r in rows), reports.ZERO))


class DebtRowOut(BaseModel):
    key: str
    parent: str | None
    title: str
    account: str
    receivable: Decimal
    advance_paid: Decimal
    payable: Decimal
    advance_received: Decimal
    opening: Decimal
    saldo: Decimal
    drill: dict


class DebtsOut(BaseModel):
    rows: list[DebtRowOut]
    total: dict[str, Decimal]


def _debts(db: Session, f: ReportFilter) -> DebtsOut:
    rows, total = reports.debts(db, f)
    return DebtsOut(rows=[DebtRowOut(key=r.key, parent=r.parent, title=r.title, account=r.account,
                                     drill=r.drill, **r.values) for r in rows], total=total)


@router.get("/debts", response_model=DebtsOut)
def get_debts(f: Filter, db: DbSession):
    """Дебиторская и кредиторская задолженность на конец периода по контрагентам и договорам."""
    return _debts(db, f)


def _check_version(db: Session, organization_id: int, version_id: int) -> BudgetVersion:
    version = db.get(BudgetVersion, version_id)
    if version is None or db.get(Budget, version.budget_id).organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Версия бюджета не найдена")
    return version


class PlanFactColumn(BaseModel):
    key: str
    title: str
    date_from: dt.date
    date_to: dt.date
    version_id: int | None
    version_title: str | None


class PlanFactOut(BaseModel):
    kind: str
    columns: list[PlanFactColumn]
    money_account_ids: list[int]
    rows: list[dict]


def _plan_fact(db: Session, f: ReportFilter, kind: str, version_id: int | None) -> PlanFactOut:
    if version_id is not None:
        _check_version(db, f.organization_id, version_id)
    result = reports.plan_fact(db, f, kind, version_id)
    titles = {}
    for v_id in set(result.plan_versions.values()):
        version = db.get(BudgetVersion, v_id)
        titles[v_id] = f"{db.get(Budget, version.budget_id).title}: {version.name}"
    columns = []
    for c in result.columns:
        v_id = result.plan_versions.get(c.key)
        columns.append(PlanFactColumn(key=c.key, title=c.title, date_from=c.date_from, date_to=c.date_to,
                                      version_id=v_id, version_title=titles.get(v_id)))
    return PlanFactOut(kind=kind, columns=columns, money_account_ids=result.money_account_ids, rows=result.rows)


@router.get("/plan-fact", response_model=PlanFactOut)
def get_plan_fact(f: Filter, db: DbSession, kind: Literal["pl", "cf"] = "pl", version_id: int | None = None):
    """План/факт по ОПиУ или ДДС. Без version_id — по каждому месяцу актуальный утверждённый бюджет
    (месяц, иначе квартал, иначе год)."""
    return _plan_fact(db, f, kind, version_id)


@router.get("/dds", response_model=ColumnsReportOut)
def get_dds(f: Filter, db: DbSession):
    return _dds(db, f)


# --- Excel ---


def _subtitle(db: Session, f: ReportFilter) -> str:
    parts = [f"Период: {f.date_from:%d.%m.%Y} — {f.date_to:%d.%m.%Y}"]
    if f.legal_entity_id:
        parts.append(f"Юрлицо: {db.get(LegalEntity, f.legal_entity_id).short_name}")
    if f.cfo_id:
        parts.append(f"ЦФО: {db.get(Cfo, f.cfo_id).name} (с подчинёнными)")
    parts.append("с черновиками" if f.with_drafts else "только проведённые операции")
    return "; ".join(parts)


BALANCE_COLUMNS = [
    xlsx.XlsxColumn("Сальдо на начало, Дт", 17, True), xlsx.XlsxColumn("Сальдо на начало, Кт", 17, True),
    xlsx.XlsxColumn("Обороты, Дт", 17, True), xlsx.XlsxColumn("Обороты, Кт", 17, True),
    xlsx.XlsxColumn("Сальдо на конец, Дт", 17, True), xlsx.XlsxColumn("Сальдо на конец, Кт", 17, True),
]
BALANCE_KEYS = ["opening_debit", "opening_credit", "debit", "credit", "closing_debit", "closing_credit"]


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(content, media_type=XLSX_TYPE, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/osv.xlsx")
def get_osv_xlsx(f: Filter, db: DbSession):
    data = _osv(db, f)
    by_id = {r.account_id: r for r in data.rows}

    def level(row) -> int:
        depth, parent = 0, row.parent_id
        while parent in by_id:
            depth, parent = depth + 1, by_id[parent].parent_id
        return depth

    rows = [(level(r), [r.code, r.name] + [getattr(r, k) for k in BALANCE_KEYS]) for r in data.rows]
    total = ["Итого", ""] + [getattr(data.total, k) for k in BALANCE_KEYS]
    content = xlsx.build(
        "Оборотно-сальдовая ведомость", _subtitle(db, f),
        [xlsx.XlsxColumn("Счёт", 10), xlsx.XlsxColumn("Наименование", 40)] + BALANCE_COLUMNS, rows, total,
    )
    return _xlsx(content, f"osv_{f.date_from:%Y-%m-%d}_{f.date_to:%Y-%m-%d}.xlsx")


def _columns_xlsx(title: str, data: ColumnsReportOut, subtitle: str, first_title: str = "Статья") -> bytes:
    rows = [(r.level, [r.title] + [r.values.get(c.key) if r.values else None for c in data.columns],
             r.kind in ("balance", "section", "total", "title")) for r in data.rows]
    return xlsx.build(title, subtitle, [xlsx.XlsxColumn(first_title, 45)]
                      + [xlsx.XlsxColumn(c.title, 16, True) for c in data.columns], rows)


@router.get("/dds.xlsx")
def get_dds_xlsx(f: Filter, db: DbSession):
    content = _columns_xlsx("Движение денежных средств", _dds(db, f), _subtitle(db, f))
    return _xlsx(content, f"dds_{f.date_from:%Y-%m-%d}_{f.date_to:%Y-%m-%d}.xlsx")


@router.get("/pl.xlsx")
def get_pl_xlsx(f: Filter, db: DbSession):
    content = _columns_xlsx("Отчёт о прибылях и убытках", _pl(db, f), _subtitle(db, f))
    return _xlsx(content, f"pl_{f.date_from:%Y-%m-%d}_{f.date_to:%Y-%m-%d}.xlsx")


@router.get("/balance.xlsx")
def get_balance_xlsx(f: Filter, db: DbSession):
    content = _columns_xlsx("Управленческий баланс", _balance(db, f), _subtitle(db, f), "Показатель")
    return _xlsx(content, f"balance_{f.date_to:%Y-%m-%d}.xlsx")


DEBT_XLSX = [("receivable", "Дебиторская задолженность"), ("advance_paid", "Авансы выданные"),
             ("payable", "Кредиторская задолженность"), ("advance_received", "Авансы полученные"),
             ("opening", "Сальдо на начало"), ("saldo", "Сальдо на конец")]


@router.get("/debts.xlsx")
def get_debts_xlsx(f: Filter, db: DbSession):
    data = _debts(db, f)
    rows = [(1 if r.parent else 0, [r.title, r.account] + [getattr(r, k) for k, _ in DEBT_XLSX], r.parent is None)
            for r in data.rows]
    content = xlsx.build(
        "Дебиторская и кредиторская задолженность", _subtitle(db, f),
        [xlsx.XlsxColumn("Контрагент / договор", 45), xlsx.XlsxColumn("Счёт", 9)]
        + [xlsx.XlsxColumn(t, 17, True) for _, t in DEBT_XLSX],
        rows, ["Итого", ""] + [data.total[k] for k, _ in DEBT_XLSX],
    )
    return _xlsx(content, f"debts_{f.date_to:%Y-%m-%d}.xlsx")


@router.get("/plan-fact.xlsx")
def get_plan_fact_xlsx(f: Filter, db: DbSession, kind: Literal["pl", "cf"] = "pl", version_id: int | None = None):
    data = _plan_fact(db, f, kind, version_id)
    columns = [xlsx.XlsxColumn("Статья", 45)]
    for c in data.columns:
        columns += [xlsx.XlsxColumn(f"{c.title}: план", 15, True), xlsx.XlsxColumn(f"{c.title}: факт", 15, True),
                    xlsx.XlsxColumn(f"{c.title}: отклонение", 15, True)]
    columns.append(xlsx.XlsxColumn("Итого: % выполнения", 12))
    rows = []
    for r in data.rows:
        values = [r["title"]]
        for c in data.columns:
            v = r["values"].get(c.key) or {}
            values += [v.get("plan"), v.get("fact"), v.get("dev")]
        total = r["values"].get("total") or {}
        values.append(total.get("pct"))
        rows.append((r["level"], values, r["kind"] in ("section", "total", "title")))
    title = "План/факт: " + ("ОПиУ" if kind == "pl" else "ДДС")
    content = xlsx.build(title, _subtitle(db, f), columns, rows)
    return _xlsx(content, f"plan_fact_{kind}_{f.date_from:%Y-%m-%d}_{f.date_to:%Y-%m-%d}.xlsx")

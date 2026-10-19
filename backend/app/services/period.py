"""Закрытие периода (docs/TZ.md, раздел 14, «Детали 4.5»).

Закрытый период — по organizations.closed_through включительно. Запрет изменений проверяется одним
обработчиком перед записью в базу (guard_closed_period): его не обойти ни из журнала, ни из зарплаты,
амортизации, загрузок выписок и Excel — любая запись, удаление или смена статуса фактической операции
с датой в закрытом периоде отклоняется. Плановые операции бюджета закрытием не затрагиваются.

checks — проверки перед закрытием месяца (блокирующие и предупреждения)
close / reopen — закрыть месяц / открыть последний закрытый
"""

import calendar
import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from app.models import (
    DepreciationRun,
    Employee,
    LegalEntity,
    Operation,
    Organization,
    PayrollRun,
    PeriodClosing,
    Posting,
    User,
)
from app.services import fixed_assets, money, result, routines
from app.services.journal import JournalError

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]


class PeriodClosedError(JournalError):
    """Изменение операции в закрытом периоде."""


class ClosingError(ValueError):
    """Закрыть или открыть период нельзя; текст — для пользователя."""


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def month_title(d: dt.date) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}"


# --- Запрет изменений в закрытом периоде ---


def _touched_operations(session: Session) -> list[tuple[Operation, list[dt.date]]]:
    """Операции, которые меняются при этой записи, и их даты (прежняя и новая)."""
    found: dict[int, tuple[Operation, set[dt.date]]] = {}

    def add(op: Operation | None, dates) -> None:
        if op is None:
            return
        entry = found.setdefault(id(op), (op, set()))
        entry[1].update(d for d in dates if d is not None)

    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, Operation):
            history = inspect(obj).attrs.op_date.history
            add(obj, [obj.op_date, *history.deleted])
        elif isinstance(obj, Posting):
            op = session.get(Operation, obj.operation_id) if obj.operation_id is not None else None
            add(op, [op.op_date] if op is not None else [])
    return list(found.values())


def guard_closed_period(session: Session, _flush_context, _instances) -> None:
    for op, dates in _touched_operations(session):
        if op.budget_version_id is not None or op.status == "plan" or op.organization_id is None:
            continue
        organization = session.get(Organization, op.organization_id)
        closed = organization.closed_through if organization is not None else None
        if closed is not None and dates and min(dates) <= closed:
            number = f"№ {op.number} " if op.number else ""
            raise PeriodClosedError(
                f"Операция {number}от {min(dates):%d.%m.%Y}: период закрыт по {closed:%d.%m.%Y}. "
                "Изменить её может финансовый директор, открыв период на странице «Закрытие периода»")


event.listen(Session, "before_flush", guard_closed_period)


# --- Проверки перед закрытием ---


@dataclass
class Check:
    code: str
    title: str
    ok: bool
    blocking: bool  # не пройдена — закрыть нельзя
    details: list[str] = field(default_factory=list)


def _next_open_month(organization: Organization) -> dt.date | None:
    closed = organization.closed_through
    return closed + dt.timedelta(days=1) if closed else None


def checks(db: Session, organization_id: int, year: int, month: int) -> list[Check]:
    organization = db.get(Organization, organization_id)
    start, end = month_bounds(year, month)
    found_checks: list[Check] = []

    closed = organization.closed_through
    expected = _next_open_month(organization)
    if closed is not None and end <= closed:
        found_checks.append(Check("order", "Месяц ещё открыт", False, True, [f"Период уже закрыт по {closed:%d.%m.%Y}"]))
    elif expected is not None and start != expected:
        found_checks.append(Check("order", "Предыдущие месяцы закрыты", False, True,
                            [f"Сначала закройте {month_title(expected)} — месяцы закрываются по порядку"]))
    else:
        found_checks.append(Check("order", "Предыдущие месяцы закрыты", True, True))

    # черновики — после закрытия их не провести
    drafts = select(Operation.number, Operation.op_date).where(
        Operation.organization_id == organization_id, Operation.budget_version_id.is_(None),
        Operation.status == "draft", Operation.op_date <= end)
    if closed is not None:
        drafts = drafts.where(Operation.op_date > closed)
    found = db.execute(drafts.order_by(Operation.op_date, Operation.number)).all()
    found_checks.append(Check("drafts", "Нет черновиков", not found, True,
                        [f"Черновиков: {len(found)} — проведите, удалите или аннулируйте. Номера: "
                         + ", ".join(str(n) for n, _ in found[:30]) + (" …" if len(found) > 30 else "")]
                        if found else []))

    pending = [r for r in routines.due(db, organization_id, year, month)
               if routines.run_of(db, r.id, year, month) is None]
    found_checks.append(Check("routines", "Регламентные операции выполнены", not pending, True,
                        [f"Не выполнено: {r.name}" for r in pending]))

    entities = list(db.scalars(select(LegalEntity).where(LegalEntity.organization_id == organization_id)
                               .order_by(LegalEntity.short_name)))
    no_depreciation = [
        e.short_name for e in entities
        if fixed_assets.preview(db, organization_id, e.id, year, month)
        and not db.scalar(select(DepreciationRun.id).where(
            DepreciationRun.legal_entity_id == e.id, DepreciationRun.year == year, DepreciationRun.month == month))]
    found_checks.append(Check("depreciation", "Амортизация начислена", not no_depreciation, True,
                        [f"Не начислена: {name}" for name in no_depreciation]))

    not_closed = result.pending(db, organization_id, year, month)
    result_title = "Финансовый результат определён (26, 44, 90, 91 закрыты на 99" + (
        ", реформация 99 → 84)" if month == 12 else ")")
    found_checks.append(Check("result", result_title, not not_closed, True,
                              [f"Не закрыты: {name}" for name in not_closed]))

    no_payroll = []
    for e in entities:
        staff = db.scalar(select(func.count()).select_from(Employee).where(
            Employee.legal_entity_id == e.id, Employee.is_active,
            (Employee.hired_on.is_(None)) | (Employee.hired_on <= end),
            (Employee.fired_on.is_(None)) | (Employee.fired_on >= start)))
        posted = db.scalar(select(PayrollRun.id).where(
            PayrollRun.legal_entity_id == e.id, PayrollRun.year == year, PayrollRun.month == month,
            PayrollRun.status == "posted"))
        if staff and not posted:
            no_payroll.append(e.short_name)
    found_checks.append(Check("payroll", "Зарплата начислена", not no_payroll, False,
                        [f"Нет проведённого начисления: {name}" for name in no_payroll]))

    negative = [f"{r['name']}: {r['closing']:.2f}".replace(".", ",") for r in money.money_overview(db, organization_id, start, end)
                if r["closing"] < 0]
    found_checks.append(Check("money", "Нет отрицательных остатков денег", not negative, False,
                        [f"Остаток на конец месяца: {x}" for x in negative]))

    opening = money.opening_balance_check(db, organization_id)
    found_checks.append(Check("opening", "Начальные остатки сведены (счёт 00 = 0)", opening == 0, False,
                        [f"Сальдо счёта 00: {opening:.2f}".replace(".", ",")] if opening else []))
    return found_checks


def close(db: Session, organization_id: int, year: int, month: int, user: User) -> dt.date:
    failed = [c for c in checks(db, organization_id, year, month) if c.blocking and not c.ok]
    if failed:
        raise ClosingError("Закрыть нельзя: " + "; ".join(
            f"{c.title.lower()} — {' '.join(c.details)}" if c.details else c.title.lower() for c in failed))
    organization = db.get(Organization, organization_id)
    _, end = month_bounds(year, month)
    db.add(PeriodClosing(organization_id=organization_id, action="close", closed_through=end,
                         previous=organization.closed_through, user_id=user.id))
    organization.closed_through = end
    db.flush()
    return end


def reopen(db: Session, organization_id: int, user: User) -> dt.date | None:
    """Открыть последний закрытый месяц: дата закрытия возвращается к значению до его закрытия
    (конец предыдущего месяца; после самого первого закрытия — «не закрыт»)."""
    organization = db.get(Organization, organization_id)
    closed = organization.closed_through
    if closed is None:
        raise ClosingError("Период не закрыт")
    last = db.scalar(select(PeriodClosing).where(
        PeriodClosing.organization_id == organization_id, PeriodClosing.action == "close",
        PeriodClosing.closed_through == closed).order_by(PeriodClosing.id.desc()).limit(1))
    new = last.previous if last is not None else closed.replace(day=1) - dt.timedelta(days=1)
    db.add(PeriodClosing(organization_id=organization_id, action="reopen", closed_through=new, previous=closed,
                         user_id=user.id))
    organization.closed_through = new
    db.flush()
    return new

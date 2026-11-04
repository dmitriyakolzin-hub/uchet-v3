"""Кто может менять план (docs/TZ.md, раздел 22, «Детали 12.5»).

ЦФО закрыт в версии, если утверждён он сам или вышестоящий ЦФО. План закрытого ЦФО и любой план утверждённой
версии меняют только администратор и финансовый директор (право проводить), каждая правка — в истории.
Открытый план черновика — у кого есть право черновиков (руководитель ЦФО — в своей ветке, это проверяет API).
Архивная версия не меняется.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BudgetCfoStatus, BudgetVersion, Cfo
from app.services.plan import PlanError

LOCKED = "План утверждён — менять его могут администратор и финансовый директор"


def locked_cfo_ids(db: Session, version: BudgetVersion) -> set[int]:
    """ЦФО, утверждённые в версии, вместе со всеми подчинёнными."""
    approved = list(db.scalars(select(BudgetCfoStatus.cfo_id).where(BudgetCfoStatus.version_id == version.id,
                                                                     BudgetCfoStatus.approved_at.is_not(None))))
    if not approved:
        return set()
    result: set[int] = set()
    for path in db.scalars(select(Cfo.path).where(Cfo.id.in_(approved))):
        result |= set(db.scalars(select(Cfo.id).where(Cfo.path.op("<@")(path))))
    return result


class Guard:
    """Проверка правки ячеек плана: вызов guard(cfo_id) бросает PlanError, если нельзя, и возвращает True,
    если правка идёт после утверждения (пишется в историю)."""

    def __init__(self, db: Session, version: BudgetVersion, *, supervisor: bool, can_draft: bool):
        self.version, self.supervisor, self.can_draft = version, supervisor, can_draft
        self.locked = locked_cfo_ids(db, version)

    def is_locked(self, cfo_id: int | None) -> bool:
        return self.version.status == "approved" or (cfo_id is not None and cfo_id in self.locked)

    def __call__(self, cfo_id: int | None) -> bool:
        if self.version.status == "archived":
            raise PlanError("Версия в архиве — план не меняется")
        if self.is_locked(cfo_id):
            if not self.supervisor:
                raise PlanError(LOCKED)
            return True
        if not self.can_draft:
            raise PlanError("Недостаточно прав, чтобы менять план")
        return False

    def can_edit(self, cfo_id: int | None) -> bool:
        try:
            self(cfo_id)
        except PlanError:
            return False
        return True

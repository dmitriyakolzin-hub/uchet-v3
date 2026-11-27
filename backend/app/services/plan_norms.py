"""Методы планирования статей и нормативы (docs/TZ.md, раздел 31, «Детали 23.1»)."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article, Cfo, PlanNorm, ReportFormRow
from app.services.articles import is_group

DEFAULT_METHOD = "cfo"  # статья без метода во всей ветке — «Снизу ЦФО» (ввод в сетке бюджета, как было)


class NormError(ValueError):
    pass


def effective_methods(articles: list[Article]) -> dict[int, str]:
    """Метод каждой статьи: свой или ближайшей вышестоящей; нигде нет — «Снизу ЦФО»."""
    by_id = {a.id: a for a in articles}
    result: dict[int, str] = {}

    def method(a: Article, guard: int = 0) -> str:
        if a.id in result:
            return result[a.id]
        if a.plan_method:
            value = a.plan_method
        elif a.parent_id in by_id and guard < 50:
            value = method(by_id[a.parent_id], guard + 1)
        else:
            value = DEFAULT_METHOD
        result[a.id] = value
        return value

    for a in articles:
        method(a)
    return result


def month_start(value: dt.date) -> dt.date:
    return value.replace(day=1)


def validate(db: Session, norm: PlanNorm) -> None:
    article = db.get(Article, norm.article_id)
    if article is None or article.organization_id != norm.organization_id:
        raise NormError("Статья не найдена в этой организации")
    if is_group(db, article.id):
        raise NormError("Норматив задаётся для статьи без подстатей")
    if norm.cfo_id is not None:
        cfo = db.get(Cfo, norm.cfo_id)
        if cfo is None or cfo.organization_id != norm.organization_id:
            raise NormError("ЦФО не найден в этой организации")
    norm.valid_from = month_start(norm.valid_from)
    if norm.kind == "calc":
        if norm.percent is None:
            raise NormError("Укажите процент от базового показателя")
        if (norm.base_article_id is None) == (norm.base_form_code is None):
            raise NormError("Выберите базовый показатель: статью или строку управленческой формы")
        if norm.base_article_id is not None:
            base = db.get(Article, norm.base_article_id)
            if base is None or base.organization_id != norm.organization_id:
                raise NormError("Базовая статья не найдена в этой организации")
            if base.id == article.id:
                raise NormError("Статья не может быть базой сама для себя")
        else:
            row = db.scalar(select(ReportFormRow).where(ReportFormRow.organization_id == norm.organization_id,
                                                        ReportFormRow.code == norm.base_form_code).limit(1))
            if row is None:
                raise NormError(f"Строки с кодом {norm.base_form_code} нет в управленческой форме")
            if row.article_id == article.id:
                raise NormError("Статья не может быть базой сама для себя")
        norm.amount = None
    else:
        if norm.amount is None:
            raise NormError("Укажите сумму в месяц")
        norm.percent, norm.base_article_id, norm.base_form_code = None, None, None


def norms_on(db: Session, organization_id: int, kind: str, month: dt.date) -> list[PlanNorm]:
    """Действующие на месяц нормативы вида: по каждой паре (статья, ЦФО) — последний с `valid_from` ≤ месяца."""
    rows = db.scalars(select(PlanNorm).where(PlanNorm.organization_id == organization_id, PlanNorm.kind == kind,
                                             PlanNorm.valid_from <= month_start(month))
                      .order_by(PlanNorm.valid_from))
    latest: dict[tuple[int, int | None], PlanNorm] = {}
    for norm in rows:
        latest[(norm.article_id, norm.cfo_id)] = norm
    return list(latest.values())

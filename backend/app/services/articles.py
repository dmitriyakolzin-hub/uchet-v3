"""Справочник статей организации деревом (docs/TZ.md, раздел 19, «Детали 9.1»)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article

INHERITED = ("is_cash_flow", "cash_flow_activity", "cash_flow_direction", "is_pl", "pl_section")


class ArticleError(Exception):
    pass


def system_article(db: Session, organization_id: int, key: str) -> Article | None:
    """Статья, которую использует программа (app/core/standard_articles.py: SYSTEM_KEYS)."""
    return db.scalar(select(Article).where(Article.organization_id == organization_id, Article.system_key == key))


def group_ids(db: Session, organization_id: int | None = None) -> set[int]:
    """Статьи, у которых есть подстатьи (группы)."""
    query = select(Article.parent_id).where(Article.parent_id.is_not(None)).distinct()
    if organization_id is not None:
        query = query.where(Article.organization_id == organization_id)
    return set(db.scalars(query))


def is_group(db: Session, article_id: int) -> bool:
    return db.scalar(select(Article.id).where(Article.parent_id == article_id).limit(1)) is not None


def descendants(db: Session, article_id: int) -> list[Article]:
    """Все подстатьи ветки (без самой статьи)."""
    result, level = [], [article_id]
    while level:
        children = list(db.scalars(select(Article).where(Article.parent_id.in_(level))))
        result += children
        level = [c.id for c in children]
    return result


def subtree_ids(db: Session, article_id: int) -> list[int]:
    """Статья вместе со всеми подстатьями."""
    return [article_id] + [a.id for a in descendants(db, article_id)]


def check_tree(db: Session, article: Article, creating: bool) -> None:
    """Родитель — своей организации и не внутри ветки; подстатья наследует признаки родителя;
    системная статья не бывает группой; группа в архив — только когда подстатьи в архиве."""
    parent = db.get(Article, article.parent_id) if article.parent_id is not None else None
    if article.parent_id is not None:
        if parent is None or parent.organization_id != article.organization_id:
            raise ArticleError("Родительская статья не найдена в этой организации")
        if not creating and article.parent_id in subtree_ids(db, article.id):
            raise ArticleError("Статью нельзя перенести внутрь её собственной ветки")
        if parent.system_key:
            raise ArticleError(f"«{parent.name}» — системная статья, у неё не может быть подстатей")
        if parent.is_transfer:
            raise ArticleError("У статьи перевода между своими счетами не может быть подстатей")
        for field in INHERITED:
            setattr(article, field, getattr(parent, field))
        if creating and article.debit_account_id is None:
            article.debit_account_id, article.credit_account_id = parent.debit_account_id, parent.credit_account_id
    if article.is_transfer and article.system_key != "transfer":
        raise ArticleError("Признак «перевод между своими счетами» — только у системной статьи перевода")
    if not creating:
        children = list(db.scalars(select(Article).where(Article.parent_id == article.id)))
        if not article.is_active and any(c.is_active for c in children):
            raise ArticleError("Сначала отправьте в архив подстатьи")
        # признаки группы переходят на всю ветку
        for child in descendants(db, article.id):
            for field in INHERITED:
                setattr(child, field, getattr(article, field))


def labels(db: Session, organization_id: int) -> dict[int, str]:
    """Подпись статьи для списков и сеток: «код путь», например «2.3.2.1. Аренда / переменная часть / газ»."""
    items = {a.id: a for a in db.scalars(select(Article).where(Article.organization_id == organization_id))}

    def path(a: Article) -> str:
        names, guard = [], 0
        while a is not None and guard < 50:
            names.append(a.name)
            a, guard = items.get(a.parent_id), guard + 1
        return " / ".join(reversed(names))

    return {i: f"{a.code} {path(a)}" if a.code else path(a) for i, a in items.items()}

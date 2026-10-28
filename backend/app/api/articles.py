"""Статьи ДДС и ОПиУ организации деревом (docs/TZ.md, раздел 8 и раздел 19, «Детали 9.1»).
Смотрят все с доступом к организации, правят финансовый директор и администратор (право «справочники»)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.access import get_organization_for
from app.api.deps import CurrentUser, DbSession
from app.core.permissions import Perm
from app.models import Account, Article, ReportFormRow, User
from app.schemas.article import ArticleIn, ArticleOut
from app.services import articles as tree
from app.services.management_report import FormulaError, parse_formula

router = APIRouter(prefix="/api", tags=["Статьи"])


class ArticleTreeOut(ArticleOut):
    has_children: bool
    level: int  # 1 — верхний уровень


def _check_template_account(db: Session, account_id: int | None, side: str) -> None:
    if account_id is None:
        return
    account = db.get(Account, account_id)
    if account is None or not account.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Счёт {side}: не найден или в архиве")
    if db.scalar(select(Account.id).where(Account.parent_id == account.id).limit(1)):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Счёт {side} {account.code} имеет субсчета — выберите субсчёт",
        )


def _out(db: Session, organization_id: int, only: Article | None = None) -> list[ArticleTreeOut]:
    items = list(db.scalars(select(Article).where(Article.organization_id == organization_id)))
    groups = {a.parent_id for a in items if a.parent_id is not None}
    by_id = {a.id: a for a in items}

    def level(a: Article) -> int:
        depth = 1
        while a.parent_id is not None and a.parent_id in by_id and depth < 50:
            a, depth = by_id[a.parent_id], depth + 1
        return depth

    chosen = [only] if only is not None else items
    return [ArticleTreeOut(**ArticleOut.model_validate(a).model_dump(), has_children=a.id in groups, level=level(a))
            for a in chosen]


def _save(db: Session, article: Article, body: ArticleIn, creating: bool) -> ArticleTreeOut:
    _check_template_account(db, body.debit_account_id, "дебета")
    _check_template_account(db, body.credit_account_id, "кредита")
    data = body.model_dump()
    if article.system_key == "transfer":
        data["is_transfer"] = True  # системная статья перевода остаётся переводом
    for name, value in data.items():
        setattr(article, name, value)
    db.add(article)
    try:
        with db.no_autoflush:
            tree.check_tree(db, article, creating)
        db.commit()
    except tree.ArticleError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Статья с таким кодом или таким названием в этой группе уже есть") from None
    db.refresh(article)
    return _out(db, article.organization_id, article)[0]


def _article(db: Session, user: User, article_id: int, perm: Perm) -> Article:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Статья не найдена")
    try:
        get_organization_for(db, user, article.organization_id, perm)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Статья не найдена") from None
        raise
    return article


@router.get("/organizations/{organization_id}/articles", response_model=list[ArticleTreeOut])
def list_articles(organization_id: int, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.VIEW)
    rows = _out(db, organization_id)
    return sorted(rows, key=lambda a: (a.sort_order, a.name.lower()))


@router.post("/organizations/{organization_id}/articles", response_model=ArticleTreeOut, status_code=201)
def create_article(organization_id: int, body: ArticleIn, user: CurrentUser, db: DbSession):
    get_organization_for(db, user, organization_id, Perm.EDIT_REFERENCE)
    if not body.sort_order:  # по умолчанию — в конец своей группы
        last = db.scalar(select(func.max(Article.sort_order)).where(Article.organization_id == organization_id,
                                                                    Article.parent_id.is_(body.parent_id)
                                                                    if body.parent_id is None
                                                                    else Article.parent_id == body.parent_id))
        body.sort_order = (last or 0) + 1
    return _save(db, Article(organization_id=organization_id), body, creating=True)


@router.put("/articles/{article_id}", response_model=ArticleTreeOut)
def update_article(article_id: int, body: ArticleIn, user: CurrentUser, db: DbSession):
    article = _article(db, user, article_id, Perm.EDIT_REFERENCE)
    return _save(db, article, body, creating=False)


# --- Форма управленческого отчёта: формула и знак строки (docs/TZ.md, раздел 19, «Детали 9.4») ---


class FormRowIn(BaseModel):
    formula: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    invert: bool = False


class FormRowOut(BaseModel):
    id: int
    code: str | None
    name: str
    kind: str
    formula: str
    invert: bool


@router.put("/report-form-rows/{row_id}", response_model=FormRowOut)
def update_form_row(row_id: int, body: FormRowIn, user: CurrentUser, db: DbSession):
    row = db.get(ReportFormRow, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка формы не найдена")
    try:
        get_organization_for(db, user, row.organization_id, Perm.EDIT_REFERENCE)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Строка формы не найдена") from None
        raise
    if body.formula:
        if row.kind != "indicator":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Формула бывает только у показателя")
        try:
            parse_formula(body.formula)
        except FormulaError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Формула: {exc}") from None
    row.formula, row.invert = body.formula, body.invert
    db.commit()
    return FormRowOut(id=row.id, code=row.code, name=row.name, kind=row.kind, formula=row.formula, invert=row.invert)

"""Статьи ДДС и ОПиУ: общий справочник, изменяет администратор, смотрят все."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models import Account, Article
from app.schemas.article import ArticleIn, ArticleOut

router = APIRouter(prefix="/api", tags=["Статьи"])


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


def _save(db: Session, article: Article, body: ArticleIn) -> Article:
    _check_template_account(db, body.debit_account_id, "дебета")
    _check_template_account(db, body.credit_account_id, "кредита")
    for name, value in body.model_dump().items():
        setattr(article, name, value)
    db.add(article)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Статья с таким наименованием уже есть") from None
    return article


@router.get("/articles", response_model=list[ArticleOut])
def list_articles(_: CurrentUser, db: DbSession) -> list[Article]:
    return list(db.scalars(select(Article).order_by(Article.name)))


@router.post("/articles", response_model=ArticleOut, status_code=201)
def create_article(body: ArticleIn, _: AdminUser, db: DbSession) -> Article:
    return _save(db, Article(), body)


@router.put("/articles/{article_id}", response_model=ArticleOut)
def update_article(article_id: int, body: ArticleIn, _: AdminUser, db: DbSession) -> Article:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Статья не найдена")
    return _save(db, article, body)

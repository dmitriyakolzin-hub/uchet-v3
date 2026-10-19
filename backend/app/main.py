import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import (
    accounts,
    articles,
    auth,
    currencies,
    health,
    journal,
    money,
    organizations,
    references,
    users,
)
from app.core.config import SettingsError, get_settings

# В контейнере фронтенд лежит в /app/frontend, в репозитории — рядом с backend/.
_FRONTEND_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "frontend",
    Path(__file__).resolve().parents[2] / "frontend",
]


def create_app() -> FastAPI:
    # Проверяем настройки до запуска: без JWT_SECRET и пароля базы приложение не стартует.
    get_settings()

    app = FastAPI(
        title="Учёт v3",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(organizations.router)
    app.include_router(users.router)
    app.include_router(currencies.router)
    app.include_router(accounts.router)
    app.include_router(articles.router)
    app.include_router(references.router)
    app.include_router(journal.router)
    app.include_router(money.router)

    frontend = next((p for p in _FRONTEND_CANDIDATES if p.is_dir()), None)
    if frontend is not None:
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


try:
    app = create_app()
except SettingsError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)

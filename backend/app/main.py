import sys
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    accounts,
    articles,
    auth,
    budgets,
    closing,
    currencies,
    fixed_assets,
    health,
    imports,
    journal,
    money,
    organizations,
    payment_requests,
    payroll,
    references,
    reports,
    users,
)
from app.core.config import SettingsError, get_settings
from app.services.period import PeriodClosedError

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
    app.include_router(imports.router)
    app.include_router(reports.router)
    app.include_router(budgets.router)
    app.include_router(payroll.router)
    app.include_router(fixed_assets.router)
    app.include_router(closing.router)
    app.include_router(payment_requests.router)

    # Запрет изменений в закрытом периоде срабатывает при записи в базу (app/services/period.py) —
    # из любого раздела; ответ — понятная ошибка, а не «внутренняя ошибка сервера»
    @app.exception_handler(PeriodClosedError)
    def period_closed(_request: Request, exc: PeriodClosedError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    frontend = next((p for p in _FRONTEND_CANDIDATES if p.is_dir()), None)
    if frontend is not None:
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


try:
    app = create_app()
except SettingsError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)

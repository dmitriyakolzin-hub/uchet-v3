from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DbSession

router = APIRouter(tags=["Служебное"])


@router.get("/api/health")
def health(db: DbSession) -> JSONResponse:
    """Проверка, что приложение работает и видит базу. Без входа, без данных."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error", "db": "недоступна"}, status_code=503)
    return JSONResponse({"status": "ok", "db": "ok"})

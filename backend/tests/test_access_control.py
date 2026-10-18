"""Каждый эндпоинт /api, кроме явно открытых, требует входа (CLAUDE.md, правило 4)."""

import re

from fastapi.routing import APIRoute

from app.api.deps import get_current_user

PUBLIC = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
}


def _walk(routes):
    # В этой версии FastAPI подключённые роутеры лежат в app.routes обёртками — обходим их
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _walk(route.original_router.routes)


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def _api_routes():
    from app.main import app

    for route in _walk(app.routes):
        if route.path.startswith("/api/"):
            for method in route.methods:
                yield method, route


def test_routes_are_found():
    """Защита от пустой проверки: если обход маршрутов сломается, тесты ниже прошли бы впустую."""
    assert len(list(_api_routes())) >= 40


def test_every_api_route_requires_login():
    unprotected = [
        f"{method} {route.path}"
        for method, route in _api_routes()
        if (method, route.path) not in PUBLIC and not _depends_on(route.dependant, get_current_user)
    ]
    assert unprotected == []


def test_every_protected_route_rejects_anonymous(client):
    checked = 0
    for method, route in _api_routes():
        if (method, route.path) in PUBLIC:
            continue
        path = re.sub(r"\{[a-z_]+\}", "1", route.path)
        if route.path.endswith("/rate"):
            path += "?on=2026-01-01"
        resp = client.request(method, path, json={})
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
        checked += 1
    assert checked >= 40

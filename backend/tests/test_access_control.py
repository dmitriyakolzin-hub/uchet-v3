"""Каждый эндпоинт /api, кроме явно открытых, требует входа (CLAUDE.md, правило 4)."""

from fastapi.routing import APIRoute

from app.api.deps import get_current_user

PUBLIC = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
}


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def _api_routes():
    from app.main import app

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in route.methods:
                yield method, route


def test_every_api_route_requires_login():
    unprotected = [
        f"{method} {route.path}"
        for method, route in _api_routes()
        if (method, route.path) not in PUBLIC
        and route.path not in ("/api/openapi.json", "/api/docs")
        and not _depends_on(route.dependant, get_current_user)
    ]
    assert unprotected == []


def test_every_protected_route_rejects_anonymous(client):
    for method, route in _api_routes():
        if (method, route.path) in PUBLIC:
            continue
        path = route.path.replace("{organization_id}", "1").replace("{entity_id}", "1")
        path = path.replace("{user_id}", "1").replace("{currency_id}", "1").replace("{rate_id}", "1")
        resp = client.request(method, path, json={})
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"

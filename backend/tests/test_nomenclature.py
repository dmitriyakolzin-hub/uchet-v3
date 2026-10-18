import pytest


@pytest.fixture
def env(client, headers_for, make_user, make_org):
    admin = make_user(login="admin", is_admin=True)
    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    other_fd = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo")])
    other = make_org("Клиент Б", kind="client", members=[(other_fd, "finance_director")])
    units = {u["short_name"]: u for u in client.get("/api/units", headers=headers_for(fd)).json()}
    return {"admin": admin, "fd": fd, "ceo": ceo, "other_fd": other_fd, "org": org, "other": other,
            "units": units, "h": headers_for}


def test_units_seeded(client, env):
    assert env["units"]["шт"]["okei"] == "796"
    assert {"кг", "л", "м²", "ч", "упак"} <= set(env["units"])


def test_units_admin_only(client, env):
    body = {"okei": "999", "short_name": "x", "name": "Икс"}
    assert client.post("/api/units", headers=env["h"](env["fd"]), json=body).status_code == 403
    assert client.post("/api/units", headers=env["h"](env["admin"]), json=body).status_code == 201
    assert client.post("/api/units", headers=env["h"](env["admin"]), json=body).status_code == 409
    assert client.post("/api/units", headers=env["h"](env["admin"]),
                       json={**body, "okei": "12"}).status_code == 422


def _nom(env, **over):
    body = {"name": "Кофе зерновой 1 кг", "article": "CF-001", "unit_id": env["units"]["шт"]["id"], "kind": "goods"}
    body.update(over)
    return body


def test_nomenclature(client, env):
    url = f"/api/organizations/{env['org'].id}/nomenclature"
    h = env["h"](env["fd"])
    resp = client.post(url, headers=h, json=_nom(env))
    assert resp.status_code == 201, resp.text
    assert client.post(url, headers=h, json=_nom(env, name="Другой")).status_code == 409  # артикул повторяется
    for name in ("Без артикула 1", "Без артикула 2"):
        assert client.post(url, headers=h, json=_nom(env, name=name, article="")).status_code == 201
    other_url = f"/api/organizations/{env['other'].id}/nomenclature"
    assert client.post(other_url, headers=env["h"](env["other_fd"]), json=_nom(env)).status_code == 201
    resp = client.patch(f"/api/nomenclature/{resp.json()['id']}", headers=h,
                        json={"unit_id": env["units"]["кг"]["id"], "kind": "material"})
    assert resp.status_code == 200 and resp.json()["kind"] == "material"


@pytest.mark.parametrize("over", [{"kind": "food"}, {"unit_id": 99999}, {"name": ""}])
def test_nomenclature_validation(client, env, over):
    resp = client.post(f"/api/organizations/{env['org'].id}/nomenclature", headers=env["h"](env["fd"]),
                       json=_nom(env, **over))
    assert resp.status_code == 422


def test_ceo_cannot_add_nomenclature(client, env):
    resp = client.post(f"/api/organizations/{env['org'].id}/nomenclature", headers=env["h"](env["ceo"]),
                       json=_nom(env))
    assert resp.status_code == 403


def test_warehouse_with_own_cfo_only(client, env):
    h = env["h"](env["fd"])
    own_cfo = client.post(f"/api/organizations/{env['org'].id}/cfos", headers=h, json={"name": "АВМВ"}).json()
    foreign_cfo = client.post(f"/api/organizations/{env['other'].id}/cfos", headers=env["h"](env["other_fd"]),
                              json={"name": "Чужая"}).json()
    url = f"/api/organizations/{env['org'].id}/warehouses"
    assert client.post(url, headers=h, json={"name": "Основной", "cfo_id": own_cfo["id"]}).status_code == 201
    assert client.post(url, headers=h, json={"name": "Чужой", "cfo_id": foreign_cfo["id"]}).status_code == 422
    created = client.post(url, headers=h, json={"name": "Без ЦФО"}).json()
    assert created["cfo_id"] is None
    assert [w["name"] for w in client.get(url, headers=env["h"](env["ceo"])).json()] == ["Без ЦФО", "Основной"]

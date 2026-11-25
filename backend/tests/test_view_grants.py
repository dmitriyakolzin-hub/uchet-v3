"""Дополнительный просмотр у роли, шаг 22.6: «видит все заявки», «видит всё имущество» — только смотреть
(docs/TZ.md, раздел 30, «Детали 22.6»). Данные вымышленные."""

from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import create
from tests.test_payment_requests import env  # noqa: F401


def grant(client, env, user, role, **flags):
    resp = client.put(f"/api/users/{user.id}/memberships", headers=env["h"], json={"memberships": [
        {"organization_id": env["org"].id, "role": role, **flags}]})
    assert resp.status_code == 200, resp.text
    return resp.json()["memberships"][0]


def test_view_grants(client, env, db, make_user, headers_for):  # noqa: F811
    o = env["o"]
    shop_req = create(client, env, cfo_id=env["shop"]["id"]).json()  # заявка ФД по магазину — вне ветки офиса

    # руководитель ЦФО офиса заявку магазина не видит, с отметкой — видит, но не правит
    head = [u for u in db.query(__import__("app.models", fromlist=["User"]).User).all() if u.login == "head"][0]
    assert client.get(f"/api/payment-requests/{shop_req['id']}", headers=env["head"]).status_code == 404
    m = grant(client, env, head, "cfo_head", cfo_id=env["office"]["id"], see_all_requests=True)
    assert m["see_all_requests"] is True and m["see_all_assets"] is False
    assert client.get(f"/api/payment-requests/{shop_req['id']}", headers=env["head"]).status_code == 200
    ids = [r["id"] for r in client.get(f"{o}/payment-requests", headers=env["head"]).json()["rows"]]
    assert shop_req["id"] in ids
    assert client.delete(f"/api/payment-requests/{shop_req['id']}", headers=env["head"]).status_code == 404  # не правит

    # инженер (без доступа к заявкам на оплату) с отметкой — видит список
    eng_user, engineer = member(db, make_user, headers_for, env, "eng", "engineer")
    assert client.get(f"{o}/payment-requests", headers=engineer).status_code == 403
    grant(client, env, eng_user, "engineer", see_all_requests=True)
    assert shop_req["id"] in [r["id"] for r in client.get(f"{o}/payment-requests", headers=engineer).json()["rows"]]

    # МОЛ видит только своё; с отметкой «всё имущество» — весь реестр, но править не может
    items = client.post(f"{o}/asset-items", headers=env["h"], json={"name": "Стол", "count": 2}).json()
    mol_user, mol = member(db, make_user, headers_for, env, "mol", "mol")
    assert client.get(f"{o}/asset-items", headers=mol).json() == []
    grant(client, env, mol_user, "mol", see_all_assets=True)
    assert len(client.get(f"{o}/asset-items", headers=mol).json()) == 2
    assert client.put(f"/api/asset-items/{items[0]['id']}", headers=mol,
                      json={"name": "Стол большой"}).status_code == 403
    # заявки на имущество: сотрудник с отметкой «все заявки» видит чужие
    emp_user, emp = member(db, make_user, headers_for, env, "emp", "employee")
    aho_user, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    foreign = client.post(f"{o}/asset-requests", headers=aho, json={"name": "Шкаф", "cfo_id": env["office"]["id"]}).json()
    assert client.get(f"/api/asset-requests/{foreign['id']}", headers=emp).status_code == 404
    grant(client, env, emp_user, "employee", see_all_requests=True)
    seen = client.get(f"/api/asset-requests/{foreign['id']}", headers=emp)
    assert seen.status_code == 200 and seen.json()["can_receive"] is False and seen.json()["can_edit"] is False

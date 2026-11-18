"""Инвентаризация имущества, шаг 19.7: снимок «что должно быть», отметка по QR / инв. / серийному номеру,
найденное не на своём месте, завершение с переносом и выбытием ненайденного (docs/TZ.md, раздел 28, «Детали 19.7»)."""

import datetime as dt

from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today().isoformat()


def test_inventory(client, env, db, make_user, headers_for):  # noqa: F811
    mol_user, mol = member(db, make_user, headers_for, env, "mol", "mol")
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    _, engineer = member(db, make_user, headers_for, env, "engineer", "engineer")
    person = env["post"]("employees", {"full_name": "Матвеев Олег", "user_id": mol_user.id})
    office, shop = env["office"]["id"], env["shop"]["id"]

    def add(name, cfo, count=1, **fields):
        resp = client.post(f"{env['o']}/asset-items", headers=aho, json={"name": name, "count": count, "cfo_id": cfo,
                                                                         **fields})
        assert resp.status_code == 201, resp.text
        return resp.json()

    chairs = add("Стул", office, 3, employee_id=person["id"])
    [printer] = add("Принтер", office, serial_number="SN-777")
    [fridge] = add("Холодильник", shop)

    url = f"{env['o']}/asset-inventories"
    assert client.post(url, headers=engineer, json={"on_date": TODAY, "cfo_id": office}).status_code == 403
    assert client.post(url, headers=aho, json={"on_date": TODAY}).status_code == 422  # что проверяем?
    resp = client.post(url, headers=aho, json={"on_date": TODAY, "cfo_id": office, "employee_id": person["id"]})
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert (inv["number"], inv["expected_count"], inv["found_count"]) == (1, 3, 0)  # только стулья МОЛ
    assert inv["can_scan"] and inv["can_close"]

    scan = f"/api/asset-inventories/{inv['id']}/scan"
    # МОЛ своей инвентаризации видит её и отмечает: по ссылке из QR, по инв. номеру
    assert [i["id"] for i in client.get(url, headers=mol).json()] == [inv["id"]]
    r = client.post(scan, headers=mol, json={"code": f"https://new.cfo-avmv.ru/#/assets?item={chairs[0]['id']}"}).json()
    assert r["result"] == "found" and r["inventory"]["found_count"] == 1
    r = client.post(scan, headers=mol, json={"code": chairs[1]["number"].lower()}).json()
    assert r["result"] == "found"
    assert client.post(scan, headers=mol, json={"code": chairs[1]["number"]}).json()["result"] == "again"
    # инженер: по серийному номеру — принтер не числится на МОЛ, «не на своём месте»
    r = client.post(scan, headers=engineer, json={"code": "sn-777"}).json()
    assert r["result"] == "surplus" and r["inventory"]["surplus_count"] == 1
    assert client.post(scan, headers=engineer, json={"code": "нет-такого"}).status_code == 422
    assert client.post(scan, headers=env["head"], json={"code": "x"}).status_code in (403, 404)

    # снять отметку: лишняя строка исчезает, числящаяся — снова «не найдено»
    lines = client.get(f"/api/asset-inventories/{inv['id']}", headers=aho).json()["lines"]
    wrong = next(x for x in lines if x["item_id"] == chairs[1]["id"])
    after = client.delete(f"/api/asset-inventory-lines/{wrong['id']}", headers=mol).json()
    assert (after["found_count"], after["missing_count"]) == (1, 2)
    client.post(scan, headers=mol, json={"code": chairs[1]["number"]})

    # завершает АХО: принтер переносится на МОЛ, ненайденный стул — в акт выбытия «утрата»
    assert client.post(f"/api/asset-inventories/{inv['id']}/close", headers=mol, json={}).status_code == 403
    resp = client.post(f"/api/asset-inventories/{inv['id']}/close", headers=aho,
                       json={"move_surplus": True, "lost_to_disposal": True})
    assert resp.status_code == 200, resp.text
    closed = resp.json()
    assert closed["inventory"]["status"] == "closed" and closed["disposal_number"] == 1
    moved = client.get(f"/api/asset-items/{printer['id']}", headers=aho).json()
    assert (moved["employee_id"], moved["cfo_id"]) == (person["id"], office)
    assert client.get(f"/api/asset-items/{printer['id']}/events", headers=aho).json()[-1]["action"] == "inventory_moved"
    lost = client.get(f"/api/asset-items/{chairs[2]['id']}", headers=aho).json()
    assert lost["in_disposal"] is True and lost["status"] != "disposed"  # выбытие согласует ФД
    act = client.get(f"{env['o']}/asset-disposals?status=pending", headers=env["h"]).json()[0]
    assert act["kind"] == "loss" and act["employee_name"] == "Матвеев Олег" and len(act["items"]) == 1
    # завершённую — не отметить и не удалить
    assert client.post(scan, headers=aho, json={"code": fridge["number"]}).status_code == 403
    assert client.delete(f"/api/asset-inventories/{inv['id']}", headers=aho).status_code == 422

    # по месту: магазин — холодильник; выбывшую единицу не отметить; черновик можно удалить
    shop_inv = client.post(url, headers=aho, json={"on_date": TODAY, "cfo_id": shop}).json()
    assert shop_inv["expected_count"] == 1
    assert client.get(url, headers=mol).json() == [i for i in client.get(url, headers=mol).json()
                                                     if i["id"] == inv["id"]]  # чужое МОЛ не видит
    assert client.get(f"/api/asset-inventories/{shop_inv['id']}", headers=mol).status_code == 404
    client.post(f"/api/asset-disposals/{act['id']}/approve", headers=env["h"])
    resp = client.post(f"/api/asset-inventories/{shop_inv['id']}/scan", headers=aho, json={"code": chairs[2]["number"]})
    assert resp.status_code == 422 and "выбыла" in resp.json()["detail"]
    assert client.delete(f"/api/asset-inventories/{shop_inv['id']}", headers=aho).status_code == 204

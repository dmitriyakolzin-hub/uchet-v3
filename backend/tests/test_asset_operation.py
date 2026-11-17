"""Эксплуатация имущества, шаг 19.5: перемещения с подтверждением получателем, отказ, отмена; работы — модернизация,
ремонт, ТО (docs/TZ.md, раздел 28, «Детали 19.5»). Данные вымышленные."""

import datetime as dt

from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()


def make_items(client, env, h, count=2, **fields):
    resp = client.post(f"{env['o']}/asset-items", headers=h, json={
        "name": "Витрина", "count": count, "cfo_id": env["office"]["id"], "purchase_price": "50000", **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_transfer_flow(client, env, db, make_user, headers_for):  # noqa: F811
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    old_user, old_mol = member(db, make_user, headers_for, env, "oldmol", "mol")
    new_user, new_mol = member(db, make_user, headers_for, env, "newmol", "mol")
    _, engineer = member(db, make_user, headers_for, env, "engineer", "engineer")
    old = env["post"]("employees", {"full_name": "Старов Степан", "user_id": old_user.id})
    new = env["post"]("employees", {"full_name": "Новикова Нина", "user_id": new_user.id})
    items = make_items(client, env, aho, employee_id=old["id"])
    ids = [i["id"] for i in items]
    url = f"{env['o']}/asset-transfers"
    body = {"ids": ids, "on_date": TODAY.isoformat(), "to_cfo_id": env["shop"]["id"], "to_employee_id": new["id"],
            "to_status": "in_use", "reason": "открытие магазина"}
    assert client.post(url, headers=engineer, json=body).status_code == 403  # не АХО и не МОЛ этих единиц
    assert client.post(url, headers=aho, json={**body, "to_cfo_id": None, "to_employee_id": None,
                                               "to_status": None}).status_code == 422  # ничего не меняется
    resp = client.post(url, headers=old_mol, json=body)  # МОЛ передаёт своё
    assert resp.status_code == 201, resp.text
    t = resp.json()
    assert (t["number"], t["status"], t["to_employee"], t["can_accept"], t["can_cancel"]) == \
        (1, "pending", "Новикова Нина", False, True)
    assert [x["from_employee"] for x in t["items"]] == ["Старов Степан"] * 2
    assert client.post(url, headers=aho, json=body).status_code == 422  # уже перемещаются
    assert all(i["in_transfer"] for i in client.get(f"{env['o']}/asset-items", headers=aho).json())

    # получатель видит «ждут меня», до подтверждения единицы у старого МОЛ
    mine = client.get(url, headers=new_mol, params={"to_me": True}).json()
    assert [x["number"] for x in mine] == [1] and mine[0]["can_accept"] is True
    assert client.post(f"/api/asset-transfers/{t['id']}/accept", headers=old_mol).status_code == 403
    resp = client.post(f"/api/asset-transfers/{t['id']}/accept", headers=new_mol)
    assert resp.status_code == 200 and resp.json()["status"] == "accepted"
    rows = client.get(f"{env['o']}/asset-items", headers=new_mol).json()
    assert [(i["employee_name"], i["cfo_name"], i["status"]) for i in rows] == [("Новикова Нина", "Магазин", "in_use")] * 2
    assert client.get(f"{env['o']}/asset-items", headers=old_mol).json() == []
    events = client.get(f"/api/asset-items/{ids[0]}/events", headers=aho).json()
    assert events[-1]["action"] == "transferred" and "получатель подтвердил" in events[-1]["details"]
    assert "МОЛ: Старов Степан → Новикова Нина" in events[-1]["details"]

    # отказ получателя и отмена — единицы остаются
    t2 = client.post(url, headers=aho, json={**body, "to_cfo_id": env["office"]["id"], "to_employee_id": old["id"]}).json()
    resp = client.post(f"/api/asset-transfers/{t2['id']}/reject", headers=old_mol, json={"reason": "нет места"})
    assert resp.json()["status"] == "rejected" and resp.json()["reject_reason"] == "нет места"
    t3 = client.post(url, headers=aho, json={**body, "to_warehouse_id": None, "to_employee_id": old["id"]}).json()
    assert client.post(f"/api/asset-transfers/{t3['id']}/cancel", headers=new_mol).status_code == 403
    assert client.post(f"/api/asset-transfers/{t3['id']}/cancel", headers=aho).json()["status"] == "cancelled"
    # АХО подтверждает за получателя
    t4 = client.post(url, headers=aho, json={"ids": [ids[0]], "on_date": TODAY.isoformat(),
                                             "to_status": "stock"}).json()
    assert t4["can_accept"] is True
    resp = client.post(f"/api/asset-transfers/{t4['id']}/accept", headers=aho)
    assert resp.json()["status"] == "accepted"
    assert "за получателя" in client.get(f"/api/asset-items/{ids[0]}/events", headers=aho).json()[-1]["details"]
    assert client.get(url, headers=env["other"]).status_code == 404


def test_works(client, env, db, make_user, headers_for):  # noqa: F811
    _, engineer = member(db, make_user, headers_for, env, "engineer", "engineer")
    mol_user, mol = member(db, make_user, headers_for, env, "mol", "mol")
    person = env["post"]("employees", {"full_name": "Матвеев Олег", "user_id": mol_user.id})
    [item] = make_items(client, env, env["h"], count=1, employee_id=person["id"], status="in_use")
    url = f"/api/asset-items/{item['id']}/works"
    start = (TODAY - dt.timedelta(days=5)).isoformat()
    assert client.post(url, headers=mol, json={"kind": "repair", "started_on": start}).status_code == 403
    resp = client.post(url, headers=engineer, json={"kind": "repair", "started_on": start,
                                                    "counterparty_id": env["cp"]["id"], "description": "замена мотора"})
    assert resp.status_code == 201, resp.text
    assert client.get(f"/api/asset-items/{item['id']}", headers=mol).json()["status"] == "repair"
    assert client.post(url, headers=engineer, json={"kind": "modernization", "started_on": start}).status_code == 422
    resp = client.post(url, headers=engineer, json={"kind": "modernization", "started_on": start,
                                                    "finished_on": TODAY.isoformat(), "amount": "12000.50"})
    works = resp.json()
    assert (works["modernization_total"], works["cost_with_modernization"]) == ("12000.50", "62000.50")
    repair = next(w for w in works["works"] if w["kind"] == "repair")
    assert repair["counterparty_name"] == "Арендодатель"
    resp = client.post(f"/api/asset-works/{repair['id']}/finish", headers=engineer,
                       json={"finished_on": (TODAY - dt.timedelta(days=10)).isoformat()})
    assert resp.status_code == 422  # раньше начала
    client.post(f"/api/asset-works/{repair['id']}/finish", headers=engineer, json={"finished_on": TODAY.isoformat()})
    assert client.get(f"/api/asset-items/{item['id']}", headers=mol).json()["status"] == "in_use"
    actions = [e["action"] for e in client.get(f"/api/asset-items/{item['id']}/events", headers=mol).json()]
    assert actions[-3:] == ["work", "work", "work_finished"]
    assert len(client.get(url, headers=mol).json()["works"]) == 2  # МОЛ видит работы по своему
    assert client.delete(f"/api/asset-works/{repair['id']}", headers=engineer).status_code == 204
    assert client.get(url, headers=env["other"]).status_code == 404

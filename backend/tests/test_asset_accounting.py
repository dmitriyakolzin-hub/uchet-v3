"""Учёт имущества, шаг 19.4: ОС с поступлением и вводом, малоценка в расход, за балансом, снятие с учёта,
остаток лизинга, QR-этикетки (docs/TZ.md, раздел 28, «Детали 19.4»). Данные вымышленные."""

import datetime as dt

from tests.test_asset_requests import act
from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()


def received(client, env, aho, *, name, quantity, kind="deferral", **decision):
    req = client.post(f"{env['o']}/asset-requests", headers=aho, json={
        "name": name, "quantity": quantity, "cfo_id": env["office"]["id"]}).json()
    act(client, aho, req, "submit")
    body = {"legal_entity_id": env["le"].id, "counterparty_id": env["cp"]["id"], "contract_id": env["contract"]["id"],
            "purchase_kind": kind, "pay_date": (TODAY + dt.timedelta(days=5)).isoformat(), **decision}
    resp = act(client, env["h"], req, "approve", **body)
    assert resp.status_code == 200, resp.text
    resp = act(client, aho, req, "receive", received_on=TODAY.isoformat(), quantity=quantity,
               cfo_id=env["office"]["id"])
    assert resp.status_code == 200, resp.text
    return [i for i in client.get(f"{env['o']}/asset-items", headers=aho).json() if i["name"] == name]


def test_fixed_asset_and_expense(client, env, db, make_user, headers_for):  # noqa: F811
    from app.models import Operation, Posting

    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    _, buh = member(db, make_user, headers_for, env, "buh", "accountant")
    url = f"{env['o']}/asset-items/account"
    [oven] = received(client, env, aho, name="Печь", quantity=1, price="300000")
    assert (oven["supplier_id"], oven["supplier_contract_id"]) == (env["cp"]["id"], env["contract"]["id"])
    body = {"ids": [oven["id"]], "kind": "fixed_asset", "on_date": TODAY.isoformat(),
            "counterparty_id": oven["supplier_id"], "contract_id": oven["supplier_contract_id"],
            "useful_life_months": 60, "commission_on": TODAY.isoformat()}
    assert client.post(url, headers=aho, json=body).status_code == 403  # АХО на учёт не ставит
    assert client.post(url, headers=buh, json={**body, "useful_life_months": None}).status_code == 422
    resp = client.post(url, headers=buh, json=body)
    assert resp.status_code == 200, resp.text
    [oven] = resp.json()
    assert (oven["accounting"], oven["accounting_title"], oven["status"]) == ("fixed_asset", "Основное средство", "in_use")
    assert oven["fixed_asset_id"] and oven["accounting_operation_number"]
    fa = client.get(f"{env['o']}/fixed-assets", headers=env["h"]).json()[0]
    assert (fa["inventory_number"], fa["cost"], fa["commissioned_on"]) == (oven["number"], "300000.00", TODAY.isoformat())
    assert client.post(url, headers=buh, json=body).status_code == 422  # уже учтена
    # введена в эксплуатацию — снять нельзя
    assert client.post(f"/api/asset-items/{oven['id']}/unaccount", headers=buh).status_code == 422

    # малоценка: две единицы одной операцией, Дт 26 — Кт 60.01
    knives = received(client, env, aho, name="Нож", quantity=2, price="1500")
    body = {"ids": [k["id"] for k in knives], "kind": "expense", "on_date": TODAY.isoformat(),
            "counterparty_id": env["cp"]["id"], "expense_account": "26", "expense_item_id": env["art"]["Аренда"]}
    assert client.post(url, headers=buh, json={**body, "counterparty_id": None}).status_code == 422
    resp = client.post(url, headers=buh, json=body)
    assert resp.status_code == 200, resp.text
    knives = resp.json()
    assert {k["accounting_operation_id"] for k in knives} == {knives[0]["accounting_operation_id"]}
    op_id = knives[0]["accounting_operation_id"]
    op = db.get(Operation, op_id)
    assert op.status == "posted" and db.query(Posting).filter_by(operation_id=op_id).count() == 2
    assert client.delete(f"/api/asset-items/{knives[0]['id']}", headers=aho).status_code == 422
    # снять с учёта — обе единицы, операция удалена
    resp = client.post(f"/api/asset-items/{knives[0]['id']}/unaccount", headers=buh)
    assert resp.status_code == 200 and {k["accounting"] for k in resp.json()} == {None} and len(resp.json()) == 2
    db.expire_all()
    assert db.query(Operation).filter_by(id=op_id).count() == 0

    # учтено ранее и за балансом — без проводок
    [chair] = received(client, env, aho, name="Стул", quantity=1, price="900")
    resp = client.post(url, headers=buh, json={"ids": [chair["id"]], "kind": "earlier", "on_date": TODAY.isoformat()})
    assert resp.json()[0]["accounting"] == "earlier" and resp.json()[0]["accounting_operation_id"] is None
    events = client.get(f"/api/asset-items/{chair['id']}/events", headers=aho).json()
    assert events[-1]["action"] == "accounted"


def test_lease_remaining_and_labels(client, env, db, make_user, headers_for):  # noqa: F811
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    first = (TODAY.replace(day=1) + dt.timedelta(days=40)).replace(day=10)
    items = received(client, env, aho, name="Тестомес", quantity=2, kind="leasing", lease_payment="10000",
                     lease_months=12, pay_date=first.isoformat())
    assert all(i["ownership"] == "leasing" for i in items)
    assert (items[0]["upgraded_price"], items[0]["lease_remaining"]) == ("60000.00", "60000.00")
    # оплата первого платежа лизинга уменьшает остаток
    pay = next(p for p in client.get(f"{env['o']}/payment-requests", headers=env["h"]).json()["rows"]
               if "Лизинг: Тестомес" in p["description"])
    client.post(f"/api/payment-requests/{pay['id']}/submit", headers=env["h"])
    client.post(f"/api/payment-requests/{pay['id']}/approve", headers=env["h"], json={})
    resp = client.post(f"/api/payment-requests/{pay['id']}/pay", headers=env["h"],
                       json={"paid_on": TODAY.isoformat(), "operation_number": None})
    assert resp.status_code == 200, resp.text
    items = [i for i in client.get(f"{env['o']}/asset-items", headers=aho).json() if i["name"] == "Тестомес"]
    assert items[0]["lease_remaining"] == "55000.00"  # (120 000 − 10 000) / 2

    resp = client.get(f"{env['o']}/asset-labels", headers=aho,
                      params={"ids": [i["id"] for i in items], "base": "https://new.example.ru"})
    assert resp.status_code == 200, resp.text
    labels = resp.json()
    assert [x["number"] for x in labels] == [i["number"] for i in items]
    assert labels[0]["svg"].startswith("<svg") and labels[0]["place"] == "Офис"
    assert client.get(f"{env['o']}/asset-labels", headers=aho,
                      params={"ids": [items[0]["id"]], "base": "javascript:alert(1)"}).status_code == 422
    assert client.get(f"{env['o']}/asset-labels", headers=env["other"],
                      params={"ids": [items[0]["id"]], "base": "https://x.ru"}).status_code == 404

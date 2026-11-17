"""Заявки на имущество, шаг 19.2: подача, решение ФД с оплатами в календаре, лизинг, поставка, отмена
(docs/TZ.md, раздел 28, «Детали 19.2»). Данные вымышленные."""

import datetime as dt

from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()


def act(client, h, req, action, **body):
    return client.post(f"/api/asset-requests/{req['id']}/{action}", headers=h, json=body or None)


def payments(client, env, req):
    return client.get(f"/api/asset-requests/{req['id']}", headers=env["h"]).json()["payments"]


def test_request_decision_delivery(client, env, db, make_user, headers_for):  # noqa: F811
    _, engineer = member(db, make_user, headers_for, env, "engineer", "engineer")
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    mol_user, mol = member(db, make_user, headers_for, env, "mol", "mol")
    person = env["post"]("employees", {"full_name": "Матвеев Олег", "user_id": mol_user.id})
    url = f"{env['o']}/asset-requests"
    body = {"name": "Холодильник", "quantity": 2, "cfo_id": env["shop"]["id"], "justification": "сломался старый",
            "estimated_price": "40000"}
    assert client.post(url, headers=mol, json=body).status_code == 403  # МОЛ заявки не подаёт
    resp = client.post(url, headers=engineer, json=body)
    assert resp.status_code == 201, resp.text
    req = resp.json()
    assert (req["number"], req["status"], req["status_title"], req["can_edit"]) == (1, "draft", "Черновик", True)
    assert client.put(f"/api/asset-requests/{req['id']}", headers=aho, json=body).status_code == 403  # чужая
    req = act(client, engineer, req, "submit").json()
    assert req["status"] == "pending"

    decision = {"model_name": "Бирюса 122", "legal_entity_id": env["le"].id, "counterparty_id": env["cp"]["id"],
                "contract_id": env["contract"]["id"], "employee_id": person["id"], "purchase_kind": "prepay",
                "price": "38500.50", "pay_date": (TODAY + dt.timedelta(days=3)).isoformat(),
                "bank_account_id": env["bank_acc"]["id"], "expected_on": (TODAY + dt.timedelta(days=10)).isoformat()}
    assert act(client, engineer, req, "approve", **decision).status_code == 403  # решение — ФД
    assert act(client, env["h"], req, "approve", **{**decision, "price": None}).status_code == 422
    resp = act(client, env["h"], req, "approve", **decision)
    assert resp.status_code == 200, resp.text
    req = resp.json()
    assert (req["status"], req["total"], req["purchase_kind_title"], req["employee_name"]) == \
        ("approved", "77001.00", "Предоплата", "Матвеев Олег")
    [pay] = req["payments"]
    assert (pay["amount"], pay["status"]) == ("77001.00", "approved")
    full = client.get(f"/api/payment-requests/{pay['id']}", headers=env["h"]).json()
    assert full["counterparty_id"] == env["cp"]["id"] and "Бирюса 122" in full["description"]
    assert full["article_name"] == "Покупка основных средств"

    # МОЛ видит заявку, где он ответственный; руководитель ЦФО (офис) — не видит заявку магазина
    assert client.get(f"/api/asset-requests/{req['id']}", headers=mol).status_code == 200
    assert client.get(f"/api/asset-requests/{req['id']}", headers=env["head"]).status_code == 404
    assert client.get(url, headers=env["other"]).status_code == 404

    # вернуть на решение — оплата убирается; снова решение
    req = act(client, env["h"], req, "unapprove").json()
    assert req["status"] == "pending"
    assert client.get(f"/api/payment-requests/{pay['id']}", headers=env["h"]).status_code == 404
    req = act(client, env["h"], req, "approve", **decision).json()
    [pay] = req["payments"]

    # поставка: АХО
    assert act(client, engineer, req, "delivery", status="ordered").status_code == 403
    req = act(client, aho, req, "delivery", status="ordered").json()
    assert req["status"] == "ordered"
    assert act(client, aho, req, "delivery", status="delivered").status_code == 422  # нужна дата
    req = act(client, aho, req, "delivery", status="delivered", on=TODAY.isoformat()).json()
    assert (req["status"], req["delivered_on"]) == ("delivered", TODAY.isoformat())
    events = client.get(f"/api/asset-requests/{req['id']}/events", headers=engineer).json()
    assert [e["action"] for e in events] == ["created", "submitted", "approved", "unapproved", "approved",
                                             "delivery", "delivery"]

    # отменить нельзя, если оплата прошла
    client.post(f"/api/payment-requests/{pay['id']}/pay", headers=env["h"], json={"paid_on": TODAY.isoformat(),
                                                                                    "operation_number": None})
    assert act(client, env["h"], req, "cancel", reason="передумали").status_code == 422


def test_leasing_and_reject(client, env, db, make_user, headers_for):  # noqa: F811
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    url = f"{env['o']}/asset-requests"
    req = client.post(url, headers=aho, json={"name": "Печь конвекционная"}).json()
    req = act(client, aho, req, "submit").json()
    assert act(client, aho, req, "reject", reason="нет").status_code == 403
    req = act(client, env["h"], req, "reject", reason="дорого").json()
    assert (req["status"], req["reject_reason"]) == ("rejected", "дорого")
    assert client.put(f"/api/asset-requests/{req['id']}", headers=aho,
                      json={"name": "Печь попроще"}).json()["status"] == "draft"
    req = act(client, aho, req, "submit").json()

    first = TODAY.replace(day=1) + dt.timedelta(days=40)
    decision = {"legal_entity_id": env["le"].id, "counterparty_id": env["cp"]["id"], "purchase_kind": "leasing",
                "pay_date": first.replace(day=15).isoformat(), "lease_payment": "25000", "lease_months": 24}
    assert act(client, env["h"], req, "approve", **{**decision, "lease_months": None}).status_code == 422
    req = act(client, env["h"], req, "approve", **decision).json()
    assert req["total"] == "600000.00"  # цена с удорожанием
    periodic = client.get(f"{env['o']}/periodic-payments", headers=env["h"]).json()
    [terms] = [t for t in periodic if t["name"] == "Лизинг: Печь попроще"]
    assert (terms["amount"], terms["day"], terms["frequency"]) == ("25000.00", 15, "month")
    assert terms["valid_to"].startswith(f"{first.year + 2 if first.month > 1 else first.year + 1}")
    assert len(req["payments"]) >= 1 and all(p["amount"] == "25000.00" for p in req["payments"])
    # отмена — платежи лизинга и условия уходят
    req = act(client, env["h"], req, "cancel", reason="нашли б/у").json()
    assert (req["status"], req["payments"]) == ("cancelled", [])
    assert not [t for t in client.get(f"{env['o']}/periodic-payments", headers=env["h"]).json()
                if t["name"] == "Лизинг: Печь попроще"]
    # черновик можно удалить, отменённую — нет
    assert client.delete(f"/api/asset-requests/{req['id']}", headers=env["h"]).status_code == 422
    draft = client.post(url, headers=aho, json={"name": "Стол"}).json()
    assert client.delete(f"/api/asset-requests/{draft['id']}", headers=aho).status_code == 204


def test_receipt(client, env, db, make_user, headers_for):  # noqa: F811
    """Приёмка (шаг 19.3): частями, единицы с местом, МОЛ и ценой из заявки; акт; отмена приёмки."""
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    mol_user, mol = member(db, make_user, headers_for, env, "mol", "mol")
    person = env["post"]("employees", {"full_name": "Матвеев Олег", "user_id": mol_user.id})
    cats = {c["name"]: c["id"] for c in client.get(f"{env['o']}/asset-categories", headers=aho).json()}
    req = client.post(f"{env['o']}/asset-requests", headers=aho, json={
        "name": "Стеллаж", "quantity": 3, "category_id": cats["Мебель"], "cfo_id": env["shop"]["id"]}).json()
    req = act(client, aho, req, "submit").json()
    assert req["can_receive"] is False  # до решения ФД не принимают
    assert act(client, aho, req, "receive", received_on=TODAY.isoformat(), quantity=1).status_code == 422
    req = act(client, env["h"], req, "approve", model_name="Стеллаж СТ-200", legal_entity_id=env["le"].id,
              counterparty_id=env["cp"]["id"], employee_id=person["id"], purchase_kind="deferral", price="5000",
              pay_date=(TODAY + dt.timedelta(days=14)).isoformat()).json()
    assert req["can_receive"] is True
    mol_view = client.get(f"/api/asset-requests/{req['id']}", headers=mol).json()
    assert mol_view["can_receive"] is True  # ответственный принимает сам

    body = {"received_on": TODAY.isoformat(), "quantity": 2, "employee_id": person["id"], "cfo_id": env["shop"]["id"],
            "mol_id": person["id"], "status": "in_use", "discrepancy": "одна коробка мятая"}
    assert act(client, aho, req, "receive", **{**body, "quantity": 4}).status_code == 422  # больше заказанного
    resp = act(client, mol, req, "receive", **body)
    assert resp.status_code == 200, resp.text
    req = resp.json()
    assert (req["status"], req["received_quantity"]) == ("delivered", 2)
    [receipt] = req["receipts"]
    assert (receipt["quantity"], receipt["numbers"], receipt["discrepancy"]) == \
        (2, ["ИН-000001", "ИН-000002"], "одна коробка мятая")
    items = client.get(f"{env['o']}/asset-items", headers=aho).json()
    assert [(i["name"], i["status"], i["purchase_price"], i["employee_name"], i["category_name"]) for i in items] == \
        [("Стеллаж СТ-200", "in_use", "5000.00", "Матвеев Олег", "Мебель")] * 2
    events = client.get(f"/api/asset-items/{items[0]['id']}/events", headers=aho).json()
    assert events[0]["action"] == "received"

    # акт приёмки
    url = f"/api/asset-receipts/{receipt['id']}/files"
    resp = client.post(url, headers={**aho, "Content-Type": "application/octet-stream"},
                       params={"filename": "акт.pdf"}, content=b"%PDF")
    assert resp.status_code == 201, resp.text
    assert client.get(f"/api/asset-receipt-files/{resp.json()['id']}", headers=mol).content == b"%PDF"

    # вторая приёмка — всё принято
    req = act(client, aho, req, "receive", **{**body, "quantity": 1, "discrepancy": ""}).json()
    assert (req["status"], req["status_title"], req["received_quantity"]) == ("received", "Принято", 3)
    assert req["can_receive"] is False
    # отмена второй приёмки — заявка снова «доставлено», единица удалена
    second = req["receipts"][1]
    assert client.delete(f"/api/asset-receipts/{second['id']}", headers=mol).status_code == 403
    resp = client.delete(f"/api/asset-receipts/{second['id']}", headers=aho)
    assert resp.status_code == 200 and resp.json()["status"] == "delivered"
    assert len(client.get(f"{env['o']}/asset-items", headers=aho).json()) == 2

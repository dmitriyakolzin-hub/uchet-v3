"""Роль «Сотрудник», шаг 22.2: видит и правит только то, что завёл сам — операции журнала (черновики), заявки на
оплату и на имущество; правка — до утверждения; без отчётов, денег и расчётных счетов (docs/TZ.md, раздел 30,
«Детали 22.2»). Данные вымышленные."""

import datetime as dt

from tests.test_asset_requests import opex
from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_closing import operation, rent
from tests.test_payment_requests import create
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()


def test_employee_sees_only_own(client, env, db, make_user, headers_for):  # noqa: F811
    _, emp = member(db, make_user, headers_for, env, "emp", "employee")
    o = env["o"]
    org = [x for x in client.get("/api/organizations", headers=emp).json() if x["id"] == env["org"].id][0]
    assert (org["own_only"], org["restricted"], org["role"]) == (True, True, "employee")
    assert client.get(f"{o}/reports/osv", headers=emp,
                      params={"date_from": "2026-01-01", "date_to": "2026-01-31"}).status_code == 403

    # журнал: только свои операции; правит свой черновик, провести не может
    other = operation(client, env, "2026-01-10", post=False)
    resp = client.post(f"{o}/operations", headers=emp, json={
        "legal_entity_id": env["le"].id, "op_date": "2026-01-12", "description": "Моя", "postings": [rent(env)]})
    assert resp.status_code == 201, resp.text
    mine = resp.json()
    listed = client.get(f"{o}/operations", headers=emp, params={"date_from": "2026-01-01"}).json()["items"]
    assert [x["id"] for x in listed] == [mine["id"]]
    assert client.get(f"/api/operations/{other['id']}", headers=emp).status_code == 404
    assert client.get(f"/api/operations/{mine['id']}", headers=emp).status_code == 200
    resp = client.put(f"/api/operations/{mine['id']}", headers=emp, json={
        "legal_entity_id": env["le"].id, "op_date": "2026-01-13", "description": "Моя, поправил",
        "postings": [rent(env)]})
    assert resp.status_code == 200, resp.text
    assert client.post(f"/api/operations/{mine['id']}/post", headers=emp).status_code == 403
    bulk = client.post(f"{o}/operations/bulk", headers=emp, json={"ids": [other["id"]], "action": "delete"}).json()
    assert bulk["results"][0]["error"] == "Операция не найдена"

    # заявки на оплату: только свои; расчётный счёт не выбирает; маршрут — цепочка ЦФО (руководитель офиса)
    others = create(client, env).json()
    assert create(client, env, h=emp, bank_account_id=env["bank_acc"]["id"]).status_code == 422
    req = create(client, env, h=emp).json()
    rows = client.get(f"{o}/payment-requests", headers=emp).json()["rows"]
    assert [r["id"] for r in rows] == [req["id"]]
    assert client.get(f"/api/payment-requests/{others['id']}", headers=emp).status_code == 404
    req = client.post(f"/api/payment-requests/{req['id']}/submit", headers=emp).json()
    assert req["route"] == [f"cfo:{env['office']['id']}", "fd"]

    # заявки на имущество: только свои, принимать имущество не может
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    body = {"name": "Стул", "cfo_id": env["office"]["id"], "estimated_price": "1000", "needed_on": TODAY.isoformat(),
            **opex(env)}
    foreign = client.post(f"{o}/asset-requests", headers=aho, json=body).json()
    own = client.post(f"{o}/asset-requests", headers=emp, json={**body, "name": "Лампа"})
    assert own.status_code == 201, own.text
    assert [r["name"] for r in client.get(f"{o}/asset-requests", headers=emp).json()] == ["Лампа"]
    assert client.get(f"/api/asset-requests/{foreign['id']}", headers=emp).status_code == 404
    assert own.json()["can_receive"] is False

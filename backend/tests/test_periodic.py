"""Периодические платежи по договорам (docs/TZ.md, раздел 24, «Детали 14.3»)."""

import datetime as dt

from app.services import periodic
from tests.test_approval import env  # noqa: F401
from tests.test_users import admin  # noqa: F401


def test_pay_day_moves_to_working_day_before():
    assert periodic.pay_day(dt.date(2026, 11, 1), 7) == dt.date(2026, 11, 6)  # суббота → пятница
    assert periodic.pay_day(dt.date(2026, 11, 1), 4) == dt.date(2026, 11, 3)  # 4 ноября — праздник
    assert periodic.pay_day(dt.date(2027, 2, 1), 31) == dt.date(2027, 2, 26)  # нет 31-го; 28-е — воскресенье
    assert periodic.pay_day(dt.date(2027, 1, 1), 1) == dt.date(2027, 1, 11)  # раньше рабочих нет — первый после


def test_periodic_requests(client, env):
    o, h = env["o"], env["h"]
    rub = client.get("/api/currencies", headers=h["fd"]).json()[0]["id"]
    cp = client.post(f"{o}/counterparties", headers=h["fd"], json={"name": "Арендодатель", "kind": "legal",
                                                                   "inn": "7702222222"}).json()
    contract = client.post(f"{o}/contracts", headers=h["fd"], json={
        "counterparty_id": cp["id"], "legal_entity_id": env["le"].id, "currency_id": rub, "kind": "supplier",
        "number": "А-7"}).json()
    url = f"/api/contracts/{contract['id']}/payments"
    body = {"valid_from": "2026-01-01", "day": 10, "amount": "50000", "article_id": env["rent"],
            "cfo_id": env["shop"]["id"], "executor_id": env["dept"]["id"], "description": "аренда склада"}
    assert client.post(url, headers=h["cfo"], json=body).status_code == 403  # условия — финансовая служба
    first = client.post(url, headers=h["fm"], json=body)
    assert first.status_code == 201, first.text

    months = periodic.months_ahead(dt.date.today())
    rows = client.get(f"{o}/periodic-payments", headers=h["fd"]).json()
    assert len(rows) == 1 and rows[0]["counterparty_name"] == "Арендодатель"
    assert [m["period"] for m in rows[0]["months"]] == [m.isoformat() for m in months]
    assert {m["status"] for m in rows[0]["months"]} == {"pending"}
    assert all(dt.date.fromisoformat(m["pay_date"]).weekday() < 5 for m in rows[0]["months"])
    # заявки: источник «периодический», маршрут — только финансовый директор
    reqs = [r for r in client.get(f"{o}/payment-requests", headers=h["fd"]).json()["rows"] if r["source"] == "periodic"]
    assert len(reqs) == 3 and {tuple(r["route"]) for r in reqs} == {("fd",)}
    assert "аренда склада" in reqs[0]["description"]
    first_req = min(reqs, key=lambda r: r["pay_date"])
    assert client.post(f"/api/payment-requests/{first_req['id']}/approve", headers=h["fm"]).status_code == 403
    assert client.post(f"/api/payment-requests/{first_req['id']}/approve", headers=h["fd"]).json()["status"] == "approved"

    # новая сумма со следующего месяца: неутверждённые заявки подстраиваются, согласованная — нет
    resp = client.post(url, headers=h["fm"], json={**body, "valid_from": months[1].isoformat(), "amount": "60000"})
    assert resp.status_code == 201, resp.text
    history = client.get(url, headers=h["fd"]).json()
    assert [t["amount"] for t in history] == ["60000.00", "50000.00"]
    assert history[1]["valid_to"] == (months[1] - dt.timedelta(days=1)).isoformat()
    by_period = {m["period"]: m for m in client.get(f"{o}/periodic-payments", headers=h["fd"]).json()[1]["months"]}
    assert by_period[months[1].isoformat()]["amount"] == "60000.00"
    assert client.get(f"/api/payment-requests/{first_req['id']}", headers=h["fd"]).json()["amount"] == "50000.00"
    # раньше уже заведённых условий — нельзя; удалить условия с согласованной заявкой — нельзя
    assert client.post(url, headers=h["fm"], json={**body, "valid_from": "2025-01-01"}).status_code == 422
    assert client.delete(f"/api/contract-payments/{history[1]['id']}", headers=h["fm"]).status_code == 422

    # кто видит: руководитель ЦФУ своего отдела и руководитель ЦФО ветки — да; другой ЦФУ — нет
    assert len(client.get(f"{o}/periodic-payments", headers=h["exec"]).json()) == 2
    assert len(client.get(f"{o}/periodic-payments", headers=h["cfo"]).json()) == 2
    assert client.get(f"{o}/periodic-payments", headers=h["other_exec"]).json() == []

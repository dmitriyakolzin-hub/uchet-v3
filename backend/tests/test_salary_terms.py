"""Условия оплаты сотрудников: работодатель, статья затрат, история окладов (docs/TZ.md, раздел 14)."""

import datetime as dt
from decimal import Decimal

import pytest


@pytest.fixture
def env(client, headers_for, make_user, make_org, db):
    from app.models import LegalEntity

    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    other = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo")])
    make_org("Клиент Б", members=[(other, "finance_director")])
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    db.add(le)
    db.commit()
    h = headers_for(fd)
    o = f"/api/organizations/{org.id}"
    art = {a["name"]: a["id"] for a in client.get(f"/api/organizations/{org.id}/articles", headers=h).json()}
    return {"o": o, "h": h, "le": le, "art": art, "ceo": headers_for(ceo), "other": headers_for(other)}


def test_employee_employer_and_cost_article(client, env):
    o, h = env["o"], env["h"]
    resp = client.post(f"{o}/employees", headers=h, json={
        "full_name": "Иванов И.И.", "legal_entity_id": env["le"].id,
        "pl_item_id": env["art"]["Оплата труда — коммерческий персонал"]})
    assert resp.status_code == 201, resp.text
    emp = resp.json()
    assert (emp["legal_entity_id"], emp["pl_item_id"]) == (env["le"].id, env["art"]["Оплата труда — коммерческий персонал"])
    # статья затрат — только статья ОПиУ
    bad = client.patch(f"/api/employees/{emp['id']}", headers=h, json={"pl_item_id": env["art"]["Оплата поставщикам"]})
    assert bad.status_code == 422
    cleared = client.patch(f"/api/employees/{emp['id']}", headers=h, json={"pl_item_id": None})
    assert cleared.json()["pl_item_id"] is None


def test_salary_history(client, env, db):
    from app.services.payroll import salary_on

    emp = client.post(f"{env['o']}/employees", headers=env["h"], json={"full_name": "Петров П.П."}).json()
    url = f"/api/employees/{emp['id']}/salaries"
    assert client.post(url, headers=env["h"], json={"valid_from": "2026-01-01", "amount": "80000"}).status_code == 201
    raise_ = client.post(url, headers=env["h"], json={"valid_from": "2026-07-01", "amount": "95000.50", "note": "повышение"})
    assert raise_.status_code == 201
    assert client.post(url, headers=env["h"], json={"valid_from": "2026-07-01", "amount": "1"}).status_code == 409
    assert client.post(url, headers=env["h"], json={"valid_from": "2026-08-01", "amount": "-1"}).status_code == 422
    history = client.get(url, headers=env["h"]).json()
    assert [(s["valid_from"], s["amount"]) for s in history] == [("2026-07-01", "95000.50"), ("2026-01-01", "80000.00")]

    assert salary_on(db, emp["id"], dt.date(2025, 12, 31)) is None
    assert salary_on(db, emp["id"], dt.date(2026, 3, 15)) == Decimal("80000.00")
    assert salary_on(db, emp["id"], dt.date(2026, 7, 1)) == Decimal("95000.50")

    assert client.delete(f"/api/employee-salaries/{raise_.json()['id']}", headers=env["h"]).status_code == 204
    assert len(client.get(url, headers=env["h"]).json()) == 1


def test_salary_access(client, env):
    emp = client.post(f"{env['o']}/employees", headers=env["h"], json={"full_name": "Сидоров С.С."}).json()
    url = f"/api/employees/{emp['id']}/salaries"
    salary = client.post(url, headers=env["h"], json={"valid_from": "2026-01-01", "amount": "50000"}).json()
    assert client.get(url, headers=env["ceo"]).status_code == 403  # CEO окладов не видит
    assert client.post(url, headers=env["ceo"], json={"valid_from": "2026-02-01", "amount": "1"}).status_code == 403
    assert client.get(url, headers=env["other"]).status_code == 404
    assert client.delete(f"/api/employee-salaries/{salary['id']}", headers=env["other"]).status_code == 404

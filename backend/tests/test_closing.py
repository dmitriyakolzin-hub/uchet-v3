"""Закрытие периода и регламентные операции (docs/TZ.md, раздел 14, «Детали 4.5»)."""

import pytest

from tests.test_fixed_assets import card, commission, depreciate, purchase


@pytest.fixture
def env(client, headers_for, make_user, make_org, db):
    from app.models import Currency, LegalEntity

    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    head = make_user(login="head")
    other = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo"), (head, "cfo_head")])
    make_org("Клиент Б", members=[(other, "finance_director")])
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    db.add(le)
    db.commit()
    h = headers_for(fd)
    o = f"/api/organizations/{org.id}"
    rub = db.query(Currency).filter_by(code="RUB").one()

    def post(path, body):
        resp = client.post(f"{o}/{path}", headers=h, json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    cp = post("counterparties", {"name": "Арендодатель", "kind": "legal", "inn": "7702222222"})
    contract = post("contracts", {"counterparty_id": cp["id"], "legal_entity_id": le.id, "currency_id": rub.id,
                                  "kind": "supplier", "number": "1"})
    office = post("cfos", {"name": "Офис"})
    acc = {a["code"]: a["id"] for a in client.get("/api/accounts", headers=h).json()}
    art = {a["name"]: a["id"] for a in client.get("/api/articles", headers=h).json()}
    return {"o": o, "h": h, "org": org, "le": le, "cp": cp, "contract": contract, "office": office, "acc": acc,
            "art": art, "post": post, "ceo": headers_for(ceo), "head": headers_for(head), "other": headers_for(other)}


def rent(env, amount="50000"):
    return {"debit_account_id": env["acc"]["26"], "credit_account_id": env["acc"]["60.01"], "amount": amount,
            "debit": {"cfo": env["office"]["id"], "pl_item": env["art"]["Аренда"]},
            "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}


def operation(client, env, op_date, post=True, description="Аренда офиса"):
    resp = client.post(f"{env['o']}/operations{'?post=true' if post else ''}", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": op_date, "article_id": env["art"]["Аренда"],
        "description": description, "postings": [rent(env)]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def close(client, env, month, year=2026):
    return client.post(f"{env['o']}/closing/close", headers=env["h"], json={"year": year, "month": month})


def reopen(client, env, month=1):
    return client.post(f"{env['o']}/closing/reopen", headers=env["h"], json={"year": 2026, "month": month})


def state(client, env, month, year=2026):
    resp = client.get(f"{env['o']}/closing", headers=env["h"], params={"year": year, "month": month})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    data["by_code"] = {c["code"]: c for c in data["checks"]}
    return data


def test_closed_period_blocks_changes(client, env):
    jan = operation(client, env, "2026-01-10")
    feb = operation(client, env, "2026-02-05")
    resp = close(client, env, 1)
    assert resp.status_code == 200, resp.text
    assert resp.json()["closed_through"] == "2026-01-31"

    for action in ("unpost", "cancel"):
        resp = client.post(f"/api/operations/{jan['id']}/{action}", headers=env["h"])
        assert resp.status_code == 422 and "период закрыт по 31.01.2026" in resp.json()["detail"]
    resp = client.post(f"{env['o']}/operations", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2026-01-20", "postings": [rent(env)]})
    assert resp.status_code == 422 and "закрыт" in resp.json()["detail"]

    # операцию открытого месяца нельзя перенести в закрытый
    assert client.post(f"/api/operations/{feb['id']}/unpost", headers=env["h"]).status_code == 200
    body = {"legal_entity_id": env["le"].id, "op_date": "2026-01-15", "postings": [rent(env)]}
    resp = client.put(f"/api/operations/{feb['id']}", headers=env["h"], json=body)
    assert resp.status_code == 422 and "закрыт" in resp.json()["detail"]
    assert client.get(f"/api/operations/{feb['id']}", headers=env["h"]).json()["op_date"] == "2026-02-05"

    # массовые действия — ошибка по строке
    bulk = client.post(f"{env['o']}/operations/bulk", headers=env["h"],
                       json={"action": "unpost", "ids": [jan["id"]]}).json()
    assert bulk["failed"] == 1 and "закрыт" in bulk["results"][0]["error"]

    # закрытый период нельзя менять и из других разделов: отмена амортизации
    reopen(client, env)
    asset = card(env, cost="1200", life=12)
    operation(client, env, "2026-01-05", description="—")  # чтобы было что закрывать
    client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2026-01-05", "postings": [purchase(env, asset, "1200")]})
    assert commission(client, env, asset, on="2026-01-10").status_code == 200
    [run] = depreciate(client, env, 2).json()
    assert client.post(f"/api/operations/{feb['id']}/post", headers=env["h"]).status_code == 200
    assert close(client, env, 1).status_code == 200
    assert close(client, env, 2).status_code == 200, close(client, env, 2).text
    resp = client.delete(f"/api/depreciation-runs/{run['id']}", headers=env["h"])
    assert resp.status_code == 422 and "закрыт по 28.02.2026" in resp.json()["detail"]


def test_checks_and_order(client, env):
    draft = operation(client, env, "2026-01-10", post=False)
    data = state(client, env, 1)
    assert data["by_code"]["drafts"]["ok"] is False and not data["can_close"]
    resp = close(client, env, 1)
    assert resp.status_code == 422 and "черновик" in resp.json()["detail"].lower()
    assert client.delete(f"/api/operations/{draft['id']}", headers=env["h"]).status_code == 204
    assert state(client, env, 1)["can_close"]
    assert close(client, env, 1).status_code == 200

    # закрытый месяц и пропуск месяца
    assert state(client, env, 1)["by_code"]["order"]["ok"] is False
    resp = close(client, env, 3)
    assert resp.status_code == 422 and "февраль 2026" in resp.json()["detail"]

    # невыполненная регламентная операция и неначисленная амортизация не дают закрыть месяц
    source = operation(client, env, "2026-02-03")
    client.post(f"/api/operations/{source['id']}/routine", headers=env["h"],
                json={"name": "Аренда", "day": 3, "valid_from": "2026-03-01"})
    asset = card(env, cost="1200", life=12)
    client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2026-02-05", "postings": [purchase(env, asset, "1200")]})
    commission(client, env, asset, on="2026-02-10")
    assert close(client, env, 2).status_code == 200
    data = state(client, env, 3)
    assert data["by_code"]["routines"]["details"] == ["Не выполнено: Аренда"]
    assert data["by_code"]["depreciation"]["details"] == ["Не начислена: А"]
    assert data["by_code"]["payroll"]["ok"] and not data["can_close"]
    client.post(f"{env['o']}/routine-runs", headers=env["h"], json={"year": 2026, "month": 3})
    depreciate(client, env, 3)
    assert state(client, env, 3)["can_close"]


def test_warnings_do_not_block(client, env, db):
    from app.models import Employee

    db.add(Employee(organization_id=env["org"].id, full_name="Иванов", legal_entity_id=env["le"].id))
    db.commit()
    data = state(client, env, 1)
    assert data["by_code"]["payroll"] == {"code": "payroll", "title": "Зарплата начислена", "ok": False,
                                          "blocking": False, "details": ["Нет проведённого начисления: А"]}
    assert data["can_close"]
    assert close(client, env, 1).status_code == 200


def test_reopen(client, env):
    assert close(client, env, 1).status_code == 200
    assert close(client, env, 2).status_code == 200
    resp = reopen(client, env)
    assert resp.status_code == 200 and resp.json()["closed_through"] == "2026-01-31"
    resp = reopen(client, env)
    assert resp.json()["closed_through"] is None  # до первого закрытия период не был закрыт
    assert reopen(client, env).status_code == 422
    log = resp.json()["log"]
    assert [(r["action"], r["closed_through"]) for r in log] == [
        ("reopen", None), ("reopen", "2026-01-31"), ("close", "2026-02-28"), ("close", "2026-01-31")]
    # дата закрытия видна в организации всем, кто её видит
    orgs = client.get("/api/organizations", headers=env["ceo"]).json()
    assert orgs[0]["closed_through"] is None


def test_routines(client, env):
    source = operation(client, env, "2026-01-31")
    resp = client.post(f"/api/operations/{source['id']}/routine", headers=env["h"],
                       json={"name": "Аренда офиса", "day": 31, "valid_from": "2026-02-15"})
    assert resp.status_code == 201, resp.text
    rule = resp.json()
    assert (rule["valid_from"], rule["total"], rule["postings"], rule["post"]) == ("2026-02-01", "50000.00", 1, True)

    rows = client.get(f"{env['o']}/routine-runs", headers=env["h"], params={"year": 2026, "month": 1}).json()
    assert rows == []  # правило действует с февраля
    [row] = client.get(f"{env['o']}/routine-runs", headers=env["h"], params={"year": 2026, "month": 2}).json()
    assert (row["op_date"], row["run_id"]) == ("2026-02-28", None)  # 31-е → последний день февраля

    [row] = client.post(f"{env['o']}/routine-runs", headers=env["h"], json={"year": 2026, "month": 2}).json()
    assert (row["operation_status"], row["total"], row["error"]) == ("posted", "50000.00", None)
    op = client.get(f"/api/operations/{row['operation_id']}", headers=env["h"]).json()
    assert op["op_date"] == "2026-02-28" and op["description"] == "Аренда офиса"
    assert op["postings"][0]["debit"] == source["postings"][0]["debit"]
    # повторно — не создаётся
    [again] = client.post(f"{env['o']}/routine-runs", headers=env["h"], json={"year": 2026, "month": 2}).json()
    assert again["operation_id"] == row["operation_id"]

    # выполнявшееся правило не удаляется; удалили операцию — месяц снова не выполнен
    assert client.delete(f"/api/routines/{rule['id']}", headers=env["h"]).status_code == 409
    client.post(f"/api/operations/{row['operation_id']}/unpost", headers=env["h"])
    assert client.delete(f"/api/operations/{row['operation_id']}", headers=env["h"]).status_code == 204
    [row] = client.get(f"{env['o']}/routine-runs", headers=env["h"], params={"year": 2026, "month": 2}).json()
    assert row["run_id"] is None

    # «не проводить сразу» — черновик; архивное правило не выполняется
    resp = client.patch(f"/api/routines/{rule['id']}", headers=env["h"], json={"post": False, "day": 10})
    assert resp.status_code == 200
    [row] = client.post(f"{env['o']}/routine-runs", headers=env["h"], json={"year": 2026, "month": 3}).json()
    assert (row["operation_status"], row["op_date"]) == ("draft", "2026-03-10")
    client.patch(f"/api/routines/{rule['id']}", headers=env["h"], json={"is_active": False})
    assert client.get(f"{env['o']}/routine-runs", headers=env["h"], params={"year": 2026, "month": 4}).json() == []

    # правило, которое ни разу не выполнялось, удаляется
    other = client.post(f"/api/operations/{source['id']}/routine", headers=env["h"],
                        json={"name": "Ещё", "day": 1, "valid_from": "2027-01-01"}).json()
    assert client.delete(f"/api/routines/{other['id']}", headers=env["h"]).status_code == 204


def test_routine_in_closed_month_reports_error(client, env):
    source = operation(client, env, "2026-01-10")
    assert close(client, env, 1).status_code == 200
    client.post(f"/api/operations/{source['id']}/routine", headers=env["h"],
                json={"name": "Аренда", "day": 10, "valid_from": "2026-01-01"})
    [row] = client.post(f"{env['o']}/routine-runs", headers=env["h"], json={"year": 2026, "month": 1}).json()
    assert row["run_id"] is None and "закрыт" in row["error"]
    [row] = client.post(f"{env['o']}/routine-runs", headers=env["h"], json={"year": 2026, "month": 2}).json()
    assert row["operation_status"] == "posted"


def test_access(client, env):
    source = operation(client, env, "2026-01-10")
    for h in (env["ceo"], env["head"]):
        assert client.get(f"{env['o']}/closing", headers=h, params={"year": 2026, "month": 1}).status_code == 403
        assert client.post(f"{env['o']}/closing/close", headers=h, json={"year": 2026, "month": 1}).status_code == 403
        assert client.post(f"/api/operations/{source['id']}/routine", headers=h,
                           json={"name": "X", "day": 1, "valid_from": "2026-02-01"}).status_code == 403
    assert client.get(f"{env['o']}/closing", headers=env["other"],
                      params={"year": 2026, "month": 1}).status_code == 404
    assert client.get(f"{env['o']}/routines", headers=env["other"]).status_code == 404

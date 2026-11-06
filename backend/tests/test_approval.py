"""Маршрут согласования заявок: встречная сторона → фин. менеджер → CEO по правилу → фин. директор
(docs/TZ.md, раздел 24, «Детали 14.1»). Имена вымышленные."""

import pytest

from tests.test_users import admin  # noqa: F401


@pytest.fixture
def env(client, headers_for, make_user, make_org, db, admin):  # noqa: F811
    from app.models import LegalEntity

    users = {name: make_user(login=name) for name in ("fd", "fm", "ceo", "cfo", "exec", "other_exec")}
    org = make_org("Группа А", members=[(users["fd"], "finance_director"), (users["fm"], "finance_manager"),
                                         (users["ceo"], "ceo")])
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    db.add(le)
    db.commit()
    h = {k: headers_for(u) for k, u in users.items()}
    o = f"/api/organizations/{org.id}"

    def post(path, body):
        resp = client.post(f"{o}/{path}", headers=h["fd"], json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    company = post("cfos", {"name": "Компания", "legal_entity_id": le.id})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"]})
    shop = post("cfos", {"name": "Магазин", "parent_id": trade["id"]})
    dept = post("executors", {"name": "Хозотдел"})
    member = post("executors", {"name": "Завхоз", "parent_id": dept["id"]})
    it = post("executors", {"name": "ИТ-отдел"})
    ah = headers_for(admin)
    for name, role, extra in (("cfo", "cfo_head", {"cfo_id": trade["id"]}), ("exec", "executor_head", {"executor_id": dept["id"]}),
                              ("other_exec", "executor_head", {"executor_id": it["id"]})):
        resp = client.put(f"/api/users/{users[name].id}/memberships", headers=ah, json={
            "memberships": [{"organization_id": org.id, "role": role, **extra}]})
        assert resp.status_code == 200, resp.text
    arts = client.get(f"{o}/articles", headers=h["fd"]).json()
    by_name = {a["name"]: a["id"] for a in arts}
    investing = next(a["id"] for a in arts if a["cash_flow_activity"] == "investing" and a["cash_flow_direction"] == "outflow"
                     and not any(x["parent_id"] == a["id"] for x in arts))
    return {"o": o, "h": h, "le": le, "shop": shop, "trade": trade, "dept": dept, "member": member, "it": it,
            "rent": by_name["Оплата поставщикам"], "investing": investing}


def create(client, env, who, **body):
    data = {"pay_date": "2026-12-10", "legal_entity_id": env["le"].id, "article_id": env["rent"],
            "cfo_id": env["shop"]["id"], "amount": "1000", **body}
    resp = client.post(f"{env['o']}/payment-requests", headers=env["h"][who], json=data)
    assert resp.status_code == 201, resp.text
    return resp.json()


def act(client, env, who, req, action, **body):
    return client.post(f"/api/payment-requests/{req['id']}/{action}", headers=env["h"][who], json=body or None)


def test_route_from_cfo(client, env):
    req = create(client, env, "cfo", executor_id=env["member"]["id"])
    assert req["initiator"] == "cfo"
    req = act(client, env, "cfo", req, "submit").json()
    assert req["route"] == ["executor", "fm", "fd"] and req["step_title"] == "Руководитель ЦФУ"
    # не его шаг — нельзя; руководитель отдела согласует заявку исполнителя
    assert act(client, env, "fm", req, "approve").status_code == 403
    assert act(client, env, "other_exec", req, "approve").status_code == 404  # чужой ЦФУ — заявки не видит
    mine = client.get(f"{env['o']}/payment-requests", headers=env["h"]["exec"], params={"to_approve": True}).json()
    assert [r["id"] for r in mine["rows"]] == [req["id"]] and mine["rows"][0]["can_act"] is True
    req = act(client, env, "exec", req, "approve", comment="нужно").json()
    assert req["step_title"] == "Финансовый менеджер"
    req = act(client, env, "fm", req, "approve").json()
    assert (req["status"], req["step_title"]) == ("pending", "Финансовый директор")
    req = act(client, env, "fd", req, "approve").json()
    assert req["status"] == "approved" and req["approved_by_name"]
    events = client.get(f"/api/payment-requests/{req['id']}/events", headers=env["h"]["cfo"]).json()
    assert [(e["action"], e["step"]) for e in events] == [
        ("created", ""), ("submitted", ""), ("approved", "executor"), ("approved", "fm"), ("approved", "fd")]
    assert events[2]["comment"] == "нужно"


def test_route_from_executor_with_ceo_and_revision(client, env):
    req = create(client, env, "exec", amount="2000000")  # ЦФУ — его отдел по умолчанию
    assert (req["executor_id"], req["initiator"]) == (env["dept"]["id"], "executor")
    # руководитель ЦФУ не выбирает расчётный счёт и не видит чужих заявок / журнал
    other = create(client, env, "fd", executor_id=env["it"]["id"])
    listed = client.get(f"{env['o']}/payment-requests", headers=env["h"]["exec"]).json()["rows"]
    assert [r["id"] for r in listed] == [req["id"]]
    assert client.get(f"{env['o']}/operations", headers=env["h"]["exec"]).status_code == 403
    assert client.get(f"/api/payment-requests/{other['id']}", headers=env["h"]["exec"]).status_code == 404

    req = act(client, env, "exec", req, "submit").json()
    assert req["route"] == ["cfo", "fm", "ceo", "fd"]  # больше 1 000 000 — CEO
    # руководитель ЦФО возвращает на доработку → автор правит и подаёт снова, маршрут сначала
    assert act(client, env, "cfo", req, "revise").status_code == 422  # причина обязательна
    req = act(client, env, "cfo", req, "revise", reason="приложите счёт").json()
    assert (req["status"], req["reject_reason"]) == ("revision", "приложите счёт")
    resp = client.put(f"/api/payment-requests/{req['id']}", headers=env["h"]["exec"], json={
        "pay_date": "2026-12-11", "legal_entity_id": env["le"].id, "article_id": env["rent"],
        "cfo_id": env["shop"]["id"], "executor_id": env["dept"]["id"], "amount": "1500000"})
    assert resp.status_code == 200 and resp.json()["status"] == "draft"
    req = act(client, env, "exec", req, "submit").json()
    assert (req["step"], req["step_title"]) == (0, "Руководитель ЦФО")
    for who in ("cfo", "fm", "ceo"):
        req = act(client, env, who, req, "approve").json()
    assert req["step_title"] == "Финансовый директор"
    assert act(client, env, "fd", req, "reject", reason="нет бюджета").json()["status"] == "rejected"


def test_ceo_rule_and_finance_initiator(client, env):
    # инвестиционная статья — CEO при любой сумме; заявка финансовой службы — без встречной стороны
    req = act(client, env, "fd", create(client, env, "fd", article_id=env["investing"], amount="10",
                                        executor_id=env["member"]["id"]), "submit").json()
    assert req["route"] == ["fm", "ceo", "fd"]
    small = act(client, env, "fm", create(client, env, "fm"), "submit").json()
    assert small["route"] == ["fm", "fd"]
    # CEO подавать заявки не может (только согласует); финансовый менеджер не возвращает согласованную
    assert client.post(f"{env['o']}/payment-requests", headers=env["h"]["ceo"], json={
        "pay_date": "2026-12-10", "legal_entity_id": env["le"].id, "amount": "1"}).status_code == 403
    for who in ("fm", "fd"):
        small = act(client, env, who, small, "approve").json()
    assert act(client, env, "fm", small, "unapprove").status_code == 403
    back = act(client, env, "fd", small, "unapprove").json()
    assert (back["status"], back["step_title"]) == ("pending", "Финансовый директор")


def test_request_files_and_filters(client, env):
    """Шаг 14.2: файл счёта к заявке; «мои» заявки; кассовые заявки (docs/TZ.md, раздел 24)."""
    req = create(client, env, "exec", description="Картриджи")
    url = f"/api/payment-requests/{req['id']}/files"
    pdf = b"%PDF-1.4 test"
    resp = client.post(url, params={"filename": "C:\\\\Счета\\\\счёт 15.pdf"}, content=pdf,
                       headers={**env["h"]["exec"], "Content-Type": "application/octet-stream"})
    assert resp.status_code == 201, resp.text
    file = resp.json()
    assert (file["filename"], file["size"]) == ("счёт 15.pdf", len(pdf))
    assert client.post(url, params={"filename": "вирус.exe"}, content=b"x", headers=env["h"]["exec"]).status_code == 422
    assert client.post(url, params={"filename": "big.pdf"}, content=b"x" * (10 * 1024 * 1024 + 1),
                       headers=env["h"]["exec"]).status_code == 413
    got = client.get(f"/api/payment-requests/{req['id']}", headers=env["h"]["fd"]).json()
    assert [f["filename"] for f in got["files"]] == ["счёт 15.pdf"]
    down = client.get(f"/api/payment-request-files/{file['id']}", headers=env["h"]["fd"])
    assert down.status_code == 200 and down.content == pdf and down.headers["content-type"] == "application/pdf"
    assert "UTF-8''" in down.headers["content-disposition"]
    assert client.get(f"/api/payment-request-files/{file['id']}", headers=env["h"]["other_exec"]).status_code == 404
    assert client.delete(f"/api/payment-request-files/{file['id']}", headers=env["h"]["ceo"]).status_code == 403
    assert client.delete(f"/api/payment-request-files/{file['id']}", headers=env["h"]["exec"]).status_code == 204

    # «мои» и кассовые: заявка из кассы ЦФО
    cash = client.post(f"{env['o']}/cash-desks", headers=env["h"]["fd"], json={"cfo_id": env["shop"]["id"]})
    assert cash.status_code == 204, cash.text
    create(client, env, "fd", cash_cfo_id=env["shop"]["id"], description="Из кассы")
    rows = client.get(f"{env['o']}/payment-requests", headers=env["h"]["fd"], params={"channel": "cash"}).json()["rows"]
    assert [r["description"] for r in rows] == ["Из кассы"]
    mine = client.get(f"{env['o']}/payment-requests", headers=env["h"]["fd"], params={"mine": True}).json()["rows"]
    assert [r["description"] for r in mine] == ["Из кассы"]


def test_finance_manager_adds_counterparty_and_contract(client, env, db):
    """Финансовый менеджер добавляет контрагента и договор прямо из операции, но не правит справочник
    (docs/TZ.md, раздел 25)."""
    from app.models import Currency

    rub = db.query(Currency).filter_by(code="RUB").one()
    cp = client.post(f"{env['o']}/counterparties", headers=env["h"]["fm"],
                     json={"name": "Новый поставщик", "kind": "legal", "inn": "7705555555"})
    assert cp.status_code == 201, cp.text
    contract = client.post(f"{env['o']}/contracts", headers=env["h"]["fm"], json={
        "counterparty_id": cp.json()["id"], "legal_entity_id": env["le"].id, "currency_id": rub.id,
        "kind": "supplier", "number": "НП-1"})
    assert contract.status_code == 201, contract.text
    assert client.patch(f"/api/counterparties/{cp.json()['id']}", headers=env["h"]["fm"],
                        json={"name": "Другое"}).status_code == 403
    assert client.post(f"{env['o']}/cfos", headers=env["h"]["fm"], json={"name": "Склад"}).status_code == 403
    assert client.post(f"{env['o']}/counterparties", headers=env["h"]["ceo"],
                       json={"name": "Х", "kind": "legal", "inn": "7705555556"}).status_code == 403


def test_fd_edits_approved_request_and_steps(client, env):
    """Кто согласовал — в заявке; согласованную правит финансовый директор, статус прежний (раздел 26, 16.3)."""
    req = create(client, env, "cfo", executor_id=env["member"]["id"])
    assert req["approved_steps"] == []
    req = act(client, env, "cfo", req, "submit").json()
    req = act(client, env, "exec", req, "approve").json()
    assert req["approved_steps"] == ["executor"]
    for who in ("fm", "fd"):
        req = act(client, env, who, req, "approve").json()
    assert (req["status"], req["approved_steps"]) == ("approved", ["executor", "fm", "fd"])
    body = {"pay_date": "2026-12-15", "legal_entity_id": env["le"].id, "article_id": env["rent"],
            "cfo_id": env["shop"]["id"], "executor_id": env["member"]["id"], "amount": "1200", "description": "уточнено"}
    assert client.put(f"/api/payment-requests/{req['id']}", headers=env["h"]["fm"], json=body).status_code == 403
    assert client.put(f"/api/payment-requests/{req['id']}", headers=env["h"]["cfo"], json=body).status_code == 403
    resp = client.put(f"/api/payment-requests/{req['id']}", headers=env["h"]["fd"], json=body)
    assert resp.status_code == 200, resp.text
    edited = resp.json()
    assert (edited["status"], edited["amount"], edited["pay_date"]) == ("approved", "1200.00", "2026-12-15")
    events = client.get(f"/api/payment-requests/{req['id']}/events", headers=env["h"]["fd"]).json()
    assert (events[-1]["action"], events[-1]["comment"]) == ("edited", "дата оплаты, сумма, назначение")
    # повторная подача сбрасывает список согласовавших
    act(client, env, "fd", edited, "unapprove")
    back = client.get(f"/api/payment-requests/{req['id']}", headers=env["h"]["fd"]).json()
    assert back["status"] == "pending" and back["approved_steps"] == ["executor", "fm", "fd"]

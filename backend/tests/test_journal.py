"""Журнал операций: черновики, проверки при проведении, статусы, права, массовые действия."""

from decimal import Decimal

import pytest

from app.services.ledger import select_lines


@pytest.fixture
def env(client, headers_for, make_user, make_org, db):
    from app.models import Bank, Currency, LegalEntity

    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    head = make_user(login="head")
    head2 = make_user(login="head2")
    other_fd = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo"), (head, "cfo_head"), (head2, "cfo_head")])
    other = make_org("Клиент Б", kind="client", members=[(other_fd, "finance_director")])
    le = LegalEntity(organization_id=org.id, full_name="ООО А", short_name="А", inn="7701234567")
    le2 = LegalEntity(organization_id=org.id, full_name="ООО А2", short_name="А2", inn="7701234568")
    bank = Bank(bic="044525225", name="Сбер")
    db.add_all([le, le2, bank])
    db.commit()
    h = headers_for(fd)
    o = f"/api/organizations/{org.id}"
    rub = db.query(Currency).filter_by(code="RUB").one()

    def post(path, body):
        resp = client.post(f"{o}/{path}", headers=h, json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    cp = post("counterparties", {"name": "Поставщик", "kind": "legal", "inn": "7702222222", "is_supplier": True})
    cp2 = post("counterparties", {"name": "Другой", "kind": "legal", "inn": "7703333333"})
    contract = post("contracts", {"counterparty_id": cp["id"], "legal_entity_id": le.id, "currency_id": rub.id,
                                  "kind": "supplier", "number": "1"})
    contract_le2 = post("contracts", {"counterparty_id": cp["id"], "legal_entity_id": le2.id, "currency_id": rub.id,
                                      "kind": "supplier", "number": "2"})
    bank_acc = post("bank-accounts", {"legal_entity_id": le.id, "bank_id": bank.id, "currency_id": rub.id,
                                      "number": "40702810900000012345", "name": "Сбер"})
    company = post("cfos", {"name": "АВМВ"})
    from app.models import Membership

    db.query(Membership).filter(Membership.role == "cfo_head").update({"cfo_id": company["id"]})
    db.commit()
    accounts = {a["code"]: a["id"] for a in client.get("/api/accounts", headers=h).json()}
    articles = {a["name"]: a["id"] for a in client.get(f"/api/organizations/{org.id}/articles", headers=h).json()}
    return {
        "org": org, "other": other, "le": le, "le2": le2, "rub": rub, "cp": cp, "cp2": cp2,
        "contract": contract, "contract_le2": contract_le2, "bank_acc": bank_acc, "cfo": company,
        "acc": accounts, "art": articles, "h": h, "o": o, "headers_for": headers_for,
        "fd": fd, "ceo": ceo, "head": head, "head2": head2, "other_fd": other_fd,
    }


def payment(env, amount="1000.00", **over):
    """Оплата поставщику: Дт 60.01 (контрагент, договор) — Кт 51 (расчётный счёт, статья ДДС)."""
    posting = {
        "debit_account_id": env["acc"]["60.01"], "credit_account_id": env["acc"]["51"], "amount": amount,
        "debit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]},
        "credit": {"bank_account": env["bank_acc"]["id"], "cash_flow_item": env["art"]["Оплата поставщикам"]},
    }
    posting.update(over.pop("posting", {}))
    body = {"legal_entity_id": env["le"].id, "op_date": "2026-10-05", "article_id": env["art"]["Оплата поставщикам"],
            "description": "Оплата по счёту 15", "postings": [posting]}
    body.update(over)
    return body


def cost(env, amount="700.00"):
    """Расход ЦФО: Дт 26 (ЦФО, статья ОПиУ) — Кт 60.01 — такие черновики вводит руководитель ЦФО."""
    return {"legal_entity_id": env["le"].id, "op_date": "2026-10-05", "description": "Связь", "postings": [{
        "debit_account_id": env["acc"]["26"], "credit_account_id": env["acc"]["60.01"], "amount": amount,
        "debit": {"cfo": env["cfo"]["id"], "pl_item": env["art"]["Связь и IT"]},
        "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}]}


def create(client, env, body, who=None, post=False):
    h = env["headers_for"](env[who]) if who else env["h"]
    return client.post(f"{env['o']}/operations{'?post=true' if post else ''}", headers=h, json=body)


def act(client, env, op_id, action, who=None):
    h = env["headers_for"](env[who]) if who else env["h"]
    return client.post(f"/api/operations/{op_id}/{action}", headers=h)


# --- Черновик и проведение ---


def test_create_draft_and_post(client, env):
    resp = create(client, env, payment(env))
    assert resp.status_code == 201, resp.text
    op = resp.json()
    assert op["status"] == "draft" and op["number"] == 1
    assert op["total_rub"] == "1000.00"
    p = op["postings"][0]
    assert p["amount_rub"] == "1000.00" and p["currency_id"] == env["rub"].id
    assert p["debit"] == {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}

    resp = act(client, env, op["id"], "post")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "posted" and resp.json()["posted_by"] == env["fd"].id


def test_numbers_are_sequential_per_organization(client, env):
    numbers = [create(client, env, payment(env)).json()["number"] for _ in range(3)]
    assert numbers == [1, 2, 3]
    other = client.post(f"/api/organizations/{env['other'].id}/operations", headers=env["headers_for"](env["other_fd"]),
                        json={"legal_entity_id": env["le"].id, "op_date": "2026-10-05"})
    assert other.status_code == 422  # юрлицо чужой организации


def test_create_and_post_at_once(client, env):
    resp = create(client, env, payment(env), post=True)
    assert resp.status_code == 201 and resp.json()["status"] == "posted"


def test_post_at_once_failure_saves_nothing(client, env):
    body = payment(env, posting={"debit": {"counterparty": env["cp"]["id"]}})  # нет договора
    resp = create(client, env, body, post=True)
    assert resp.status_code == 422 and "Договор" in resp.json()["detail"]
    assert client.get(f"{env['o']}/operations", headers=env["h"]).json()["total"] == 0


def test_incomplete_draft_is_saved_but_not_posted(client, env):
    body = {"legal_entity_id": env["le"].id, "op_date": "2026-10-05",
            "postings": [{"debit_account_id": env["acc"]["60.01"]}]}
    op = create(client, env, body).json()
    resp = act(client, env, op["id"], "post")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    for text in ("не указана сумма", "кредит: не указан счёт", "«Контрагент»", "«Договор»"):
        assert text in detail, detail


def test_no_postings_cannot_be_posted(client, env):
    op = create(client, env, {"legal_entity_id": env["le"].id, "op_date": "2026-10-05"}).json()
    assert act(client, env, op["id"], "post").json()["detail"] == "Нет ни одной проводки"


@pytest.mark.parametrize("posting,expected", [
    ({"debit_account_id": "acc:60"}, "проводить нельзя — выберите субсчёт"),
    ({"credit": {"bank_account": "bank_acc", "cash_flow_item": "art:Оплата поставщикам", "cfo": "cfo"}},
     "«ЦФО» на этом счёте не ведётся"),
    ({"credit": {"bank_account": "bank_acc"}}, "не заполнено «Статья ДДС»"),
    ({"quantity": "5"}, "количество указывается только на количественных счетах"),
    ({"debit": {"counterparty": "cp2", "contract": "contract"}}, "договор заключён с другим контрагентом"),
    ({"debit": {"counterparty": "cp", "contract": "contract_le2"}}, "договор заключён другим юрлицом"),
])
def test_posting_checks(client, env, posting, expected):
    def resolve(value):
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, str) and value.startswith("acc:"):
            return env["acc"][value[4:]]
        if isinstance(value, str) and value.startswith("art:"):
            return env["art"][value[4:]]
        if isinstance(value, str) and value in env and isinstance(env[value], dict):
            return env[value]["id"]
        return value

    op = create(client, env, payment(env, posting=resolve(posting))).json()
    resp = act(client, env, op["id"], "post")
    assert resp.status_code == 422
    assert expected in resp.json()["detail"], resp.json()["detail"]


def test_bank_account_of_other_legal_entity(client, env):
    op = create(client, env, payment(env, legal_entity_id=env["le2"].id,
                                     posting={"debit": {"counterparty": env["cp"]["id"],
                                                        "contract": env["contract_le2"]["id"]}})).json()
    detail = act(client, env, op["id"], "post").json()["detail"]
    assert "расчётный счёт принадлежит другому юрлицу" in detail


def test_cash_flow_item_must_be_cash_flow_article(client, env):
    body = payment(env, posting={"credit": {"bank_account": env["bank_acc"]["id"],
                                            "cash_flow_item": env["art"]["Выручка от продаж"]}})
    resp = create(client, env, body)
    assert resp.status_code == 422 and "не является статьёй ДДС" in resp.json()["detail"]


def test_foreign_counterparty_rejected_even_in_draft(client, env):
    foreign = client.post(f"/api/organizations/{env['other'].id}/counterparties",
                          headers=env["headers_for"](env["other_fd"]),
                          json={"name": "Чужой", "kind": "legal", "inn": "7709999999"}).json()
    resp = create(client, env, payment(env, posting={"debit": {"counterparty": foreign["id"]}}))
    assert resp.status_code == 422 and "не найден" in resp.json()["detail"]


def test_quantity_required_on_goods(client, env):
    nom = client.post(f"{env['o']}/nomenclature", headers=env["h"], json={
        "name": "Кофе", "unit_id": client.get("/api/units", headers=env["h"]).json()[0]["id"], "kind": "goods"}).json()
    wh = client.post(f"{env['o']}/warehouses", headers=env["h"], json={"name": "Склад"}).json()
    posting = {"debit_account_id": env["acc"]["41"], "credit_account_id": env["acc"]["60.01"], "amount": "500",
               "debit": {"nomenclature": nom["id"], "warehouse": wh["id"]},
               "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}
    body = {"legal_entity_id": env["le"].id, "op_date": "2026-10-05", "postings": [posting]}
    op = create(client, env, body).json()
    assert "нужно указать количество" in act(client, env, op["id"], "post").json()["detail"]
    body["postings"][0]["quantity"] = "10.5"
    resp = client.put(f"/api/operations/{op['id']}", headers=env["h"], json=body)
    assert resp.status_code == 200 and resp.json()["postings"][0]["quantity"] == "10.500"
    assert act(client, env, op["id"], "post").status_code == 200


# --- Валюта ---


def test_currency_amount_converted_by_rate(client, env, make_user, headers_for):
    admin = make_user(login="admin", is_admin=True)
    ha = headers_for(admin)
    usd = client.post("/api/currencies", headers=ha, json={"code": "USD", "numeric_code": "840", "name": "Доллар"}).json()
    body = payment(env, amount="100.00", posting={"currency_id": usd["id"]})
    op = create(client, env, body).json()
    assert op["postings"][0]["amount_rub"] is None  # курса ещё нет — черновик сохраняется
    assert "нет курса USD на 05.10.2026" in act(client, env, op["id"], "post").json()["detail"]

    client.post(f"/api/currencies/{usd['id']}/rates", headers=ha, json={"rate_date": "2026-10-01", "rate": "81.5432"})
    op = client.put(f"/api/operations/{op['id']}", headers=env["h"], json=body).json()
    assert op["postings"][0]["amount_rub"] == "8154.32"  # 100 × 81,5432
    assert op["postings"][0]["amount_rub_manual"] is False

    body["postings"][0]["amount_rub"] = "8200.00"  # банк списал по своему курсу
    op = client.put(f"/api/operations/{op['id']}", headers=env["h"], json=body).json()
    assert op["postings"][0]["amount_rub"] == "8200.00" and op["postings"][0]["amount_rub_manual"] is True


@pytest.mark.parametrize("amount", ["0", "-5", "10.001", 10.5])
def test_amount_validation(client, env, amount):
    resp = create(client, env, payment(env, amount=amount))
    if amount == 10.5:
        assert resp.status_code == 201 and resp.json()["postings"][0]["amount"] == "10.50"
    else:
        assert resp.status_code == 422


# --- Статусы ---


def test_status_lifecycle_and_history(client, env):
    op = create(client, env, payment(env)).json()
    assert act(client, env, op["id"], "unpost").status_code == 422  # черновик нельзя «вернуть в черновик»
    assert act(client, env, op["id"], "post").status_code == 200
    # проведённую нельзя изменить или удалить
    assert client.put(f"/api/operations/{op['id']}", headers=env["h"], json=payment(env)).status_code == 422
    assert client.delete(f"/api/operations/{op['id']}", headers=env["h"]).status_code == 422
    assert act(client, env, op["id"], "unpost").json()["status"] == "draft"
    assert client.put(f"/api/operations/{op['id']}", headers=env["h"], json=payment(env, amount="999")).status_code == 200
    assert act(client, env, op["id"], "cancel").json()["status"] == "cancelled"
    assert act(client, env, op["id"], "post").status_code == 422
    assert act(client, env, op["id"], "restore").json()["status"] == "draft"
    history = client.get(f"/api/operations/{op['id']}/history", headers=env["h"]).json()
    assert [e["action"] for e in history] == ["created", "posted", "unposted", "updated", "cancelled", "restored"]
    assert history[0]["user_id"] == env["fd"].id


def test_delete_draft(client, env):
    op = create(client, env, payment(env)).json()
    assert client.delete(f"/api/operations/{op['id']}", headers=env["h"]).status_code == 204
    assert client.get(f"/api/operations/{op['id']}", headers=env["h"]).status_code == 404


# --- Права ---


def test_cfo_head_drafts_only_own(client, env):
    op = create(client, env, cost(env), who="head")
    assert op.status_code == 201
    op = op.json()
    assert act(client, env, op["id"], "post", who="head").status_code == 403
    assert create(client, env, cost(env), who="head", post=True).status_code == 403
    h2 = env["headers_for"](env["head2"])
    assert client.put(f"/api/operations/{op['id']}", headers=h2, json=cost(env)).status_code == 403
    assert client.delete(f"/api/operations/{op['id']}", headers=h2).status_code == 403
    h1 = env["headers_for"](env["head"])
    assert client.put(f"/api/operations/{op['id']}", headers=h1, json=cost(env, amount="5")).status_code == 200
    # финансовый директор может провести черновик руководителя ЦФО
    assert act(client, env, op["id"], "post").status_code == 200


def test_ceo_only_views(client, env):
    op = create(client, env, payment(env)).json()
    h = env["headers_for"](env["ceo"])
    assert client.get(f"/api/operations/{op['id']}", headers=h).status_code == 200
    assert create(client, env, payment(env), who="ceo").status_code == 403
    assert act(client, env, op["id"], "post", who="ceo").status_code == 403
    assert client.delete(f"/api/operations/{op['id']}", headers=h).status_code == 403


def test_other_org_sees_nothing(client, env):
    op = create(client, env, payment(env)).json()
    h = env["headers_for"](env["other_fd"])
    assert client.get(f"/api/operations/{op['id']}", headers=h).status_code == 404
    assert act(client, env, op["id"], "post", who="other_fd").status_code == 404
    assert client.get(f"{env['o']}/operations", headers=h).status_code == 404


# --- Массовые действия ---


def test_bulk_post_reports_each_failure(client, env):
    good = [create(client, env, payment(env)).json()["id"] for _ in range(2)]
    bad = create(client, env, payment(env, posting={"debit": {"counterparty": env["cp"]["id"]}})).json()["id"]
    resp = client.post(f"{env['o']}/operations/bulk", headers=env["h"],
                       json={"action": "post", "ids": [*good, bad, 999999]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["done"] == 2 and body["failed"] == 2
    by_id = {r["id"]: r for r in body["results"]}
    assert "Договор" in by_id[bad]["error"]
    assert by_id[999999]["error"] == "Операция не найдена"
    statuses = {o["id"]: o["status"] for o in client.get(f"{env['o']}/operations", headers=env["h"]).json()["items"]}
    assert statuses[good[0]] == statuses[good[1]] == "posted" and statuses[bad] == "draft"


def test_bulk_delete_only_drafts(client, env):
    draft = create(client, env, payment(env)).json()["id"]
    posted = create(client, env, payment(env), post=True).json()["id"]
    body = client.post(f"{env['o']}/operations/bulk", headers=env["h"],
                       json={"action": "delete", "ids": [draft, posted]}).json()
    assert body["done"] == 1
    assert [r["error"] for r in body["results"] if not r["ok"]] == ["Удалить можно только черновик"]


def test_bulk_by_ceo_fails_each(client, env):
    op = create(client, env, payment(env)).json()["id"]
    body = client.post(f"{env['o']}/operations/bulk", headers=env["headers_for"](env["ceo"]),
                       json={"action": "post", "ids": [op]}).json()
    assert body["failed"] == 1 and body["results"][0]["error"] == "Недостаточно прав"


def test_bulk_ignores_other_org_ids(client, env):
    op = create(client, env, payment(env)).json()["id"]
    body = client.post(f"/api/organizations/{env['other'].id}/operations/bulk",
                       headers=env["headers_for"](env["other_fd"]), json={"action": "delete", "ids": [op]}).json()
    assert body["results"][0]["error"] == "Операция не найдена"
    assert client.get(f"/api/operations/{op}", headers=env["h"]).status_code == 200


# --- Список и единый отбор проводок ---


def test_list_filters(client, env):
    a = create(client, env, payment(env), post=True).json()
    b = create(client, env, payment(env, op_date="2026-11-01", description="Ноябрь")).json()
    url = f"{env['o']}/operations"

    def ids(**params):
        return [o["id"] for o in client.get(url, headers=env["h"], params=params).json()["items"]]

    assert ids() == [b["id"], a["id"]]  # сначала новые
    assert ids(date_to="2026-10-31") == [a["id"]]
    assert ids(status="posted") == [a["id"]]
    assert ids(q="ноябр") == [b["id"]]
    assert ids(account_id=env["acc"]["60"]) == [b["id"], a["id"]]  # счёт 60 включает 60.01
    assert ids(counterparty_id=env["cp2"]["id"]) == []
    assert ids(article_id=env["art"]["Оплата поставщикам"]) == [b["id"], a["id"]]


def test_ledger_lines_posted_only_and_subaccounts(client, env, db):
    posted = create(client, env, payment(env, amount="300"), post=True).json()
    create(client, env, payment(env, amount="700"))  # черновик — не в отчётах
    lines = db.execute(select_lines(env["org"].id, account_id=env["acc"]["60"])).mappings().all()
    assert [(l["operation_id"], l["side"], l["amount_rub"]) for l in lines] == [(posted["id"], "debit", Decimal("300.00"))]
    credit = db.execute(select_lines(env["org"].id, account_id=env["acc"]["51"])).mappings().one()
    assert credit["side"] == "credit" and credit["corr_account_id"] == env["acc"]["60.01"]
    assert credit["cash_flow_item"] == env["art"]["Оплата поставщикам"]
    by_cp = db.execute(select_lines(env["org"].id, analytics={"counterparty": env["cp"]["id"]})).all()
    assert len(by_cp) == 1

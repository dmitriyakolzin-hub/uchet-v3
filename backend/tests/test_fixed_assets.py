"""Основные средства: карточка, ввод в эксплуатацию, амортизация (docs/TZ.md, раздел 14, «Детали 4.4»)."""

import pytest


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

    cp = post("counterparties", {"name": "Поставщик", "kind": "legal", "inn": "7702222222"})
    contract = post("contracts", {"counterparty_id": cp["id"], "legal_entity_id": le.id, "currency_id": rub.id,
                                  "kind": "supplier", "number": "1"})
    office = post("cfos", {"name": "Офис"})
    acc = {a["code"]: a["id"] for a in client.get("/api/accounts", headers=h).json()}
    art = {a["name"]: a["id"] for a in client.get("/api/articles", headers=h).json()}
    return {"o": o, "h": h, "org": org, "le": le, "cp": cp, "contract": contract, "office": office, "acc": acc,
            "art": art, "post": post, "ceo": headers_for(ceo), "head": headers_for(head), "other": headers_for(other)}


def card(env, name="Ноутбук", cost="120000", life=36, **fields):
    return env["post"]("fixed-assets", {"name": name, "legal_entity_id": env["le"].id, "cost": cost,
                                        "useful_life_months": life, "cfo_id": env["office"]["id"], **fields})


def operation(client, env, postings, op_date="2026-01-10", **extra):
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"],
                       json={"legal_entity_id": env["le"].id, "op_date": op_date, "postings": postings, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def purchase(env, asset, amount):
    """Поступление: Дт 08 (ОС) — Кт 60.01 (поставщик)."""
    return {"debit_account_id": env["acc"]["08"], "credit_account_id": env["acc"]["60.01"], "amount": amount,
            "debit": {"fixed_asset": asset["id"]},
            "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}


def register(client, env, on=None):
    rows = client.get(f"{env['o']}/fixed-assets/register", headers=env["h"], params={"on": on} if on else {})
    assert rows.status_code == 200, rows.text
    return {r["id"]: r for r in rows.json()}


def commission(client, env, asset, on="2026-01-15"):
    return client.post(f"/api/fixed-assets/{asset['id']}/commission", headers=env["h"], json={"commissioned_on": on})


def depreciate(client, env, month, year=2026):
    return client.post(f"{env['o']}/depreciation-runs", headers=env["h"], json={"year": year, "month": month})


def test_card_and_commissioning(client, env):
    asset = card(env)
    assert asset["pl_item_id"] == env["art"]["Амортизация основных средств"]  # статья по умолчанию
    assert asset["commissioned_on"] is None

    # на 08 нет вложений — вводить нечего
    resp = commission(client, env, asset)
    assert resp.status_code == 422 and "счёте 08" in resp.json()["detail"]

    # 08 требует аналитику «Основное средство»
    no_asset = {**purchase(env, asset, "120000"), "debit": {}}
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2026-01-10", "postings": [no_asset]})
    assert resp.status_code == 422 and "Основное средство" in resp.text

    operation(client, env, [purchase(env, asset, "120000")])
    assert register(client, env)[asset["id"]]["investment"] == "120000.00"
    resp = commission(client, env, asset)
    assert resp.status_code == 200, resp.text
    asset = resp.json()
    assert asset["commissioned_on"] == "2026-01-15" and asset["commission_operation_id"]
    row = register(client, env)[asset["id"]]
    assert (row["cost"], row["investment"], row["residual"], row["monthly"]) == (
        "120000.00", "0.00", "120000.00", "3333.33")
    assert register(client, env, on="2026-01-14")[asset["id"]]["cost"] == "0.00"

    # после ввода стоимость и срок не меняются, наименование и ЦФО — можно
    resp = client.patch(f"/api/fixed-assets/{asset['id']}", headers=env["h"], json={"cost": "100000"})
    assert resp.status_code == 422 and "стоимость" in resp.json()["detail"]
    resp = client.patch(f"/api/fixed-assets/{asset['id']}", headers=env["h"],
                        json={"name": "Ноутбук бухгалтера", "cost": "120000.00", "useful_life_months": 36})
    assert resp.status_code == 200, resp.text
    assert commission(client, env, asset).status_code == 422  # повторно — нельзя

    # отмена ввода удаляет операцию; стоимость снова на 08
    resp = client.post(f"/api/fixed-assets/{asset['id']}/uncommission", headers=env["h"])
    assert resp.status_code == 200 and resp.json()["commissioned_on"] is None
    assert client.get(f"/api/operations/{asset['commission_operation_id']}", headers=env["h"]).status_code == 404
    assert register(client, env)[asset["id"]]["investment"] == "120000.00"


def test_depreciation_by_months(client, env):
    asset = card(env)
    operation(client, env, [purchase(env, asset, "120000")])
    assert commission(client, env, asset).status_code == 200

    # в месяце ввода амортизации нет
    resp = depreciate(client, env, 1)
    assert resp.status_code == 422 and "Начислять нечего" in resp.json()["detail"]
    # месяцы — по порядку
    resp = depreciate(client, env, 3)
    assert resp.status_code == 422 and "февраль" in resp.json()["detail"]

    preview = client.get(f"{env['o']}/depreciation/preview", headers=env["h"], params={"year": 2026, "month": 2}).json()
    assert [(p["asset_name"], p["amount"]) for p in preview] == [("Ноутбук", "3333.33")]
    resp = depreciate(client, env, 2)
    assert resp.status_code == 201, resp.text
    [feb] = resp.json()
    assert (feb["legal_entity_name"], feb["total"], feb["month"]) == ("А", "3333.33", 2)
    assert depreciate(client, env, 2).status_code == 422  # повторно — нечего
    assert client.get(f"{env['o']}/depreciation/preview", headers=env["h"],
                      params={"year": 2026, "month": 2}).json() == []

    # операция: Дт 26 (ЦФО, статья) — Кт 02 (ОС)
    op = client.get(f"/api/operations/{feb['operation_id']}", headers=env["h"]).json()
    assert op["status"] == "posted" and op["op_date"] == "2026-02-28"
    [p] = op["postings"]
    assert (p["debit_account_id"], p["credit_account_id"]) == (env["acc"]["26"], env["acc"]["02"])
    assert p["debit"] == {"cfo": env["office"]["id"], "pl_item": env["art"]["Амортизация основных средств"]}
    assert p["credit"] == {"fixed_asset": asset["id"]}

    row = register(client, env, on="2026-02-28")[asset["id"]]
    assert (row["depreciation"], row["residual"]) == ("3333.33", "116666.67")

    # расход — в ОПиУ, управленческие расходы
    pl = client.get(f"{env['o']}/reports/pl", headers=env["h"],
                    params={"date_from": "2026-02-01", "date_to": "2026-02-28"}).json()
    rows = {r["key"]: r for r in pl["rows"]}
    assert rows[f"art:{env['art']['Амортизация основных средств']}"]["values"]["total"] == "-3333.33"

    [mar] = depreciate(client, env, 3).json()
    # отменить ввод, когда есть амортизация, нельзя; отменять начисления — с последнего месяца
    resp = client.post(f"/api/fixed-assets/{asset['id']}/uncommission", headers=env["h"])
    assert resp.status_code == 422 and "амортизация" in resp.json()["detail"]
    resp = client.delete(f"/api/depreciation-runs/{feb['id']}", headers=env["h"])
    assert resp.status_code == 422 and "следующих" in resp.json()["detail"]
    assert client.delete(f"/api/depreciation-runs/{mar['id']}", headers=env["h"]).status_code == 204
    assert client.delete(f"/api/depreciation-runs/{feb['id']}", headers=env["h"]).status_code == 204
    assert client.get(f"/api/operations/{feb['operation_id']}", headers=env["h"]).status_code == 404
    assert client.get(f"{env['o']}/depreciation-runs", headers=env["h"]).json() == []


def test_last_month_takes_remainder(client, env):
    asset = card(env, name="Стеллаж", cost="100", life=3)
    operation(client, env, [purchase(env, asset, "100")])
    commission(client, env, asset)
    totals = [depreciate(client, env, m).json()[0]["total"] for m in (2, 3, 4)]
    assert totals == ["33.33", "33.33", "33.34"]
    assert register(client, env)[asset["id"]]["residual"] == "0.00"
    resp = depreciate(client, env, 5)
    assert resp.status_code == 422 and "Начислять нечего" in resp.json()["detail"]


def test_asset_from_opening_balances(client, env):
    """ОС, введённое до даты остатков: операции ввода нет, стоимость и амортизация — вводом остатков."""
    client.patch(env["o"], headers=env["h"], json={"opening_balance_date": "2025-12-31"})
    asset = card(env, name="Станок", cost="36000", life=36)
    resp = commission(client, env, asset, on="2025-06-01")
    assert resp.status_code == 200 and resp.json()["commission_operation_id"] is None
    an = {"fixed_asset": asset["id"]}
    operation(client, env, [
        {"debit_account_id": env["acc"]["01"], "credit_account_id": env["acc"]["00"], "amount": "36000", "debit": an},
        {"debit_account_id": env["acc"]["00"], "credit_account_id": env["acc"]["02"], "amount": "6000", "credit": an},
    ], is_opening=True)
    row = register(client, env)[asset["id"]]
    assert (row["cost"], row["depreciation"], row["residual"]) == ("36000.00", "6000.00", "30000.00")
    # декабрь — до даты остатков, начинать можно с января
    [jan] = depreciate(client, env, 1).json()
    assert jan["total"] == "1000.00"


def test_access(client, env):
    asset = card(env)
    # CEO видит карточки и ведомость, но не вводит и не начисляет
    assert client.get(f"{env['o']}/fixed-assets", headers=env["ceo"]).status_code == 200
    assert client.get(f"{env['o']}/fixed-assets/register", headers=env["ceo"]).status_code == 200
    assert client.post(f"/api/fixed-assets/{asset['id']}/commission", headers=env["ceo"],
                       json={"commissioned_on": "2026-01-15"}).status_code == 403
    assert client.post(f"{env['o']}/depreciation-runs", headers=env["ceo"],
                       json={"year": 2026, "month": 2}).status_code == 403
    assert client.post(f"{env['o']}/fixed-assets", headers=env["ceo"], json={
        "name": "X", "legal_entity_id": env["le"].id, "cost": "1", "useful_life_months": 1}).status_code == 403
    # руководителю ЦФО раздел недоступен
    assert client.get(f"{env['o']}/fixed-assets/register", headers=env["head"]).status_code == 403
    # чужая организация — «не найдено»
    assert client.get(f"{env['o']}/fixed-assets/register", headers=env["other"]).status_code == 404
    assert client.post(f"/api/fixed-assets/{asset['id']}/commission", headers=env["other"],
                       json={"commissioned_on": "2026-01-15"}).status_code == 404


def test_card_checks(client, env):
    resp = client.post(f"{env['o']}/fixed-assets", headers=env["h"], json={
        "name": "X", "legal_entity_id": env["le"].id, "cost": "1", "useful_life_months": 1,
        "pl_item_id": env["art"]["Оплата поставщикам"]})
    assert resp.status_code == 422 and "статья ОПиУ" in resp.json()["detail"]
    card(env, inventory_number="ОС-1")
    resp = client.post(f"{env['o']}/fixed-assets", headers=env["h"], json={
        "name": "Y", "inventory_number": "ОС-1", "legal_entity_id": env["le"].id, "cost": "1", "useful_life_months": 1})
    assert resp.status_code == 409
    # без ЦФО ввести нельзя: амортизации некуда относиться
    asset = card(env, name="Без ЦФО", cfo_id=None)
    operation(client, env, [purchase(env, asset, "120000")])
    resp = commission(client, env, asset)
    assert resp.status_code == 422 and "ЦФО" in resp.json()["detail"]


def test_excel_import_with_fixed_asset(client, env):
    from tests.test_excel_import import XLSX, xlsx

    asset = card(env, inventory_number="ОС-7")
    header = ["Дата*", "Счёт Дт*", "Счёт Кт*", "Сумма*", "Контрагент (ИНН или наименование)", "Договор №",
              "Основное средство (инв. № или наименование)"]
    raw = xlsx(header, [["10.01.2026", "08", "60.01", "120000", "7702222222", "1", "ОС-7"]])
    resp = client.post(f"{env['o']}/import/operations", headers={**env["h"], "Content-Type": XLSX}, content=raw)
    assert resp.status_code == 200 and resp.json()["incomplete"] == [], resp.text
    [created] = client.get(f"{env['o']}/operations", headers=env["h"]).json()["items"]
    assert created["postings"][0]["debit"] == {"fixed_asset": asset["id"]}


def sale_setup(client, env):
    asset = card(env, cost="120000", life=12)
    operation(client, env, [purchase(env, asset, "120000")])
    commission(client, env, asset)  # январь
    for month in (2, 3):
        assert depreciate(client, env, month).status_code == 201  # по 10 000
    return asset


def dispose(client, env, asset, **body):
    return client.post(f"/api/fixed-assets/{asset['id']}/dispose", headers=env["h"], json=body)


def test_sale(client, env):
    asset = sale_setup(client, env)
    # амортизация за март начислена — продать в феврале нельзя
    resp = dispose(client, env, asset, disposed_on="2026-02-20", kind="writeoff")
    assert resp.status_code == 422 and "отмените" in resp.json()["detail"]
    # за апрель не начислено — в мае нельзя
    resp = dispose(client, env, asset, disposed_on="2026-05-10", kind="writeoff")
    assert resp.status_code == 422 and "апрель" in resp.json()["detail"]
    assert dispose(client, env, asset, disposed_on="2026-04-10", kind="sale").status_code == 422  # нет покупателя

    resp = dispose(client, env, asset, disposed_on="2026-04-10", kind="sale", counterparty_id=env["cp"]["id"],
                   contract_id=env["contract"]["id"], price="120000", vat_rate=20)
    assert resp.status_code == 200, resp.text
    asset = resp.json()
    assert (asset["disposed_on"], asset["disposal_kind"]) == ("2026-04-10", "sale")
    op = client.get(f"/api/operations/{asset['disposal_operation_id']}", headers=env["h"]).json()
    acc = env["acc"]
    moves = [(p["debit_account_id"], p["credit_account_id"], p["amount"]) for p in op["postings"]]
    assert moves == [(acc["02"], acc["01"], "20000.00"), (acc["91.02"], acc["01"], "100000.00"),
                     (acc["62.01"], acc["91.01"], "120000.00"), (acc["91.02"], acc["68.02"], "20000.00")]
    row = register(client, env)[asset["id"]]
    assert (row["cost"], row["depreciation"]) == ("0.00", "0.00")

    # в месяце выбытия и позже амортизации нет
    resp = depreciate(client, env, 4)
    assert resp.status_code == 422 and "Начислять нечего" in resp.json()["detail"]
    # ОПиУ: доход без НДС 100 000, списание остаточной стоимости −100 000
    pl = client.get(f"{env['o']}/reports/pl", headers=env["h"],
                    params={"date_from": "2026-04-01", "date_to": "2026-04-30"}).json()
    rows = {r["key"]: r["values"]["total"] for r in pl["rows"]}
    assert rows[f"art:{env['art']['Доход от продажи основных средств']}"] == "100000.00"
    assert rows[f"art:{env['art']['Списание основных средств']}"] == "-100000.00"

    assert client.post(f"/api/fixed-assets/{asset['id']}/uncommission", headers=env["h"]).status_code == 422
    resp = client.post(f"/api/fixed-assets/{asset['id']}/undispose", headers=env["h"])
    assert resp.status_code == 200 and resp.json()["disposed_on"] is None
    assert register(client, env)[asset["id"]]["residual"] == "100000.00"


def test_writeoff(client, env):
    asset = sale_setup(client, env)
    resp = dispose(client, env, asset, disposed_on="2026-04-01", kind="writeoff")
    assert resp.status_code == 200, resp.text
    op = client.get(f"/api/operations/{resp.json()['disposal_operation_id']}", headers=env["h"]).json()
    assert [p["amount"] for p in op["postings"]] == ["20000.00", "100000.00"]
    assert client.post(f"/api/fixed-assets/{asset['id']}/dispose", headers=env["ceo"],
                       json={"disposed_on": "2026-04-01", "kind": "writeoff"}).status_code == 403

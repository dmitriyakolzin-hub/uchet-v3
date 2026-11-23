"""Заявка на имущество и бюджеты, шаг 21.3: инвестиционная — план вложений проекта на месяц и лимит проекта,
операционная — план ОПиУ ветки ЦФО по статье расходов минус факт; «Вид затрат…» у финансового директора,
«В заявках» в отчёте «Инвестиции», карточка ОС в проекте (docs/TZ.md, раздел 28, «Детали 21.3»). Данные вымышленные."""

import datetime as dt

from tests.test_asset_requests import act, opex, to_fd
from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()
TARGET = (TODAY.replace(day=1) + dt.timedelta(days=40)).replace(day=10)  # следующий месяц


def request(client, env, h, name, price, **fields):
    resp = client.post(f"{env['o']}/asset-requests", headers=h, json={
        "name": name, "quantity": 1, "cfo_id": env["office"]["id"], "estimated_price": price,
        "needed_on": TARGET.isoformat(), **fields})
    assert resp.status_code == 201, resp.text
    resp = act(client, h, resp.json(), "submit")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_invest_and_opex(client, env, db, make_user, headers_for):  # noqa: F811
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    o, h = env["o"], env["h"]
    project = client.post(f"{o}/investment-projects", headers=h, json={
        "name": "Новая пекарня", "cfo_id": env["office"]["id"], "limit_amount": "300000", "status": "active"}).json()
    options = client.get(f"{o}/asset-requests/budget-options", headers=aho).json()
    assert [p["name"] for p in options["projects"]] == ["Новая пекарня"]
    assert any("Прочие управленческие расходы" in a["name"] for a in options["opex_articles"])
    assert not any("Выручка" in a["name"] for a in options["opex_articles"])

    # один утверждённый бюджет: план вложений проекта и план расходов офиса на следующий месяц
    budget = client.post(f"{o}/budgets", headers=h, json={"kind": "year", "year": TARGET.year}).json()
    version = budget["versions"][0]
    equipment = env["art"]["Инвестиции: оборудование"]
    resp = client.put(f"/api/budget-versions/{version['id']}/cells", headers=h, json={
        "kind": "inv", "project_id": project["id"],
        "cells": [{"article_id": equipment, "month": TARGET.month, "amount": "200000"}]})
    assert resp.status_code == 200, resp.text
    article = env["art"]["Прочие управленческие расходы"]
    resp = client.put(f"/api/budget-versions/{version['id']}/cells", headers=h, json={
        "kind": "pl", "cfo_id": env["office"]["id"],
        "cells": [{"article_id": article, "month": TARGET.month, "amount": "100000"}]})
    assert resp.status_code == 200, resp.text
    assert client.post(f"/api/budget-versions/{version['id']}/approve", headers=h).status_code == 200

    invest = {"cost_kind": "invest", "project_id": project["id"], "budget_article_id": equipment}
    assert client.post(f"{o}/asset-requests", headers=aho, json={
        "name": "Печь", "cfo_id": env["office"]["id"], "estimated_price": "1", "needed_on": TARGET.isoformat(),
        "cost_kind": "invest"}).status_code == 201  # черновик можно без проекта ...
    draft = client.get(f"{o}/asset-requests", headers=aho).json()[0]
    assert act(client, aho, draft, "submit").status_code == 422  # ... а подать — нет

    # инвестиционная: план 200 000 на месяц, лимит проекта 300 000
    oven = request(client, env, aho, "Печь", "150000", **invest)
    assert (oven["budget_mark"], oven["budget_plan"], oven["budget_fact"], oven["budget_used"],
            oven["budget_limit_left"], oven["cost_kind_title"], oven["project_name"]) == \
        ("within", "200000.00", "0.00", "0.00", "300000.00", "Инвестиционная (капвложение)", "Новая пекарня")
    mixer = request(client, env, aho, "Тестомес", "100000", **invest)
    assert (mixer["budget_mark"], mixer["budget_remaining"], mixer["budget_limit_left"]) == \
        ("over", "50000.00", "150000.00")  # остаток месяца 50 000 < 100 000
    later = request(client, env, aho, "Шкаф", "10000", **{**invest, "budget_article_id": None},
                    needed_on=(TARGET + dt.timedelta(days=40)).isoformat())
    assert (later["budget_mark"], later["budget_plan"]) == ("over", None)  # плана на этот месяц нет — сверх (2 б)

    # операционная: план 100 000, факт 30 000 по журналу
    acc = env["acc"]
    resp = client.post(f"{o}/operations?post=true", headers=h, json={
        "legal_entity_id": env["le"].id, "op_date": TARGET.isoformat(), "postings": [{
            "debit_account_id": acc["26"], "credit_account_id": acc["60.01"], "amount": "30000",
            "debit": {"cfo": env["office"]["id"], "pl_item": article},
            "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}]})
    assert resp.status_code == 201, resp.text
    chairs = request(client, env, aho, "Стулья", "50000", **opex(env))
    assert (chairs["budget_mark"], chairs["budget_plan"], chairs["budget_fact"], chairs["budget_remaining"],
            chairs["budget_article_name"]) == ("within", "100000.00", "30000.00", "70000.00",
                                                "Прочие управленческие расходы")
    tables = request(client, env, aho, "Столы", "30000", **opex(env))
    assert (tables["budget_mark"], tables["budget_used"], tables["budget_remaining"]) == ("over", "50000.00", "20000.00")

    # финансовый директор переводит заявку в инвестиционную — бюджет пересчитан
    assert act(client, aho, chairs, "cost", **invest).status_code == 403
    chairs = act(client, h, chairs, "cost", **invest).json()
    assert (chairs["cost_kind"], chairs["budget_mark"], chairs["budget_used"]) == ("invest", "over", "250000.00")
    events = [e["action"] for e in client.get(f"/api/asset-requests/{chairs['id']}/events", headers=h).json()]
    assert events[-1] == "cost"

    # «В заявках на имущество» в отчёте «Инвестиции»
    report = client.get(f"{o}/reports/investments", headers=h, params={
        "date_from": TARGET.replace(day=1).isoformat(), "date_to": TARGET.isoformat()}).json()
    [row] = report["rows"]
    assert row["requested"] == "310000.00"  # печь + тестомес + шкаф + стулья

    # принятая по инвестиционной заявке печь становится ОС проекта
    oven = to_fd(client, env, oven)
    oven = act(client, h, oven, "approve", legal_entity_id=env["le"].id, counterparty_id=env["cp"]["id"],
               purchase_kind="deferral", price="150000", pay_date=TARGET.isoformat()).json()
    act(client, aho, oven, "receive", received_on=TODAY.isoformat(), quantity=1, cfo_id=env["office"]["id"])
    [item] = [i for i in client.get(f"{o}/asset-items", headers=h).json() if i["name"] == "Печь"]
    assert item["source_cost_kind"] == "invest"
    resp = client.post(f"{o}/asset-items/account", headers=h, json={
        "ids": [item["id"]], "kind": "fixed_asset", "on_date": TODAY.isoformat(), "useful_life_months": 60})
    assert resp.status_code == 200, resp.text
    [fa] = client.get(f"{o}/fixed-assets", headers=h).json()
    assert fa["project_id"] == project["id"]

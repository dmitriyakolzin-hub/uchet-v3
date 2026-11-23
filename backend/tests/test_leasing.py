"""Договоры лизинга, шаг 21.2: решение «лизинг» без договора, заполнение позже, один договор — несколько заявок,
всё имущество по договору, привязка имеющегося имущества (docs/TZ.md, раздел 28, «Детали 21.2»). Данные вымышленные."""

import datetime as dt

from tests.test_asset_requests import act, opex, to_fd
from tests.test_assets import member
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401

TODAY = dt.date.today()


def leased(client, env, aho, name, quantity):
    req = client.post(f"{env['o']}/asset-requests", headers=aho, json={
        "name": name, "quantity": quantity, "cfo_id": env["office"]["id"], "estimated_price": "100000",
        "needed_on": TODAY.isoformat(), **opex(env)}).json()
    req = to_fd(client, env, act(client, aho, req, "submit").json())
    resp = act(client, env["h"], req, "approve", legal_entity_id=env["le"].id, purchase_kind="leasing")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_lease_contract(client, env, db, make_user, headers_for):  # noqa: F811
    _, aho = member(db, make_user, headers_for, env, "aho", "aho_operator")
    first = (TODAY.replace(day=1) + dt.timedelta(days=40)).replace(day=5)
    oven = leased(client, env, aho, "Печь", 2)
    fridge = leased(client, env, aho, "Холодильник", 1)
    assert oven["lease_pending"] and fridge["lease_pending"]
    # приёмка до договора — можно
    resp = act(client, aho, oven, "receive", received_on=TODAY.isoformat(), quantity=2, cfo_id=env["office"]["id"])
    assert resp.status_code == 200, resp.text

    # договор заводится при первой заявке; вторая присоединяется — срок и даты общие, платёж складывается
    oven = act(client, env["h"], oven, "lease", counterparty_id=env["cp"]["id"], number="ЛД-15",
               contract_date=TODAY.isoformat(), lease_payment="20000", lease_months=36,
               pay_date=first.isoformat()).json()
    contracts = client.get(f"{env['o']}/contracts", headers=env["h"]).json()
    [contract] = [c for c in contracts if c["number"] == "ЛД-15"]
    assert contract["kind"] == "leasing"
    assert act(client, env["h"], fridge, "lease", contract_id=env["contract"]["id"],
               lease_payment="5000").status_code == 422  # не договор лизинга
    fridge = act(client, env["h"], fridge, "lease", contract_id=contract["id"], lease_payment="5000").json()
    assert (fridge["lease_months"], fridge["pay_date"], fridge["total"]) == (36, first.isoformat(), "180000.00")
    assert act(client, env["h"], fridge, "lease", contract_id=contract["id"],
               lease_payment="5000").status_code == 422  # уже заполнен

    # имеющееся имущество (заведено раньше) — привязать к договору
    [old] = client.post(f"{env['o']}/asset-items", headers=aho, json={"name": "Витрина б/у"}).json()
    resp = client.post(f"{env['o']}/asset-items/bulk", headers=aho,
                       json={"ids": [old["id"]], "lease_contract_id": contract["id"]})
    assert resp.status_code == 200, resp.text
    assert (resp.json()[0]["ownership"], resp.json()[0]["lease_contract_name"]) == \
        ("leasing", f"№ ЛД-15 от {TODAY:%d.%m.%Y}")
    by_contract = client.get(f"{env['o']}/asset-items", headers=aho,
                             params={"lease_contract_id": contract["id"]}).json()
    assert sorted(i["name"] for i in by_contract) == ["Витрина б/у", "Печь", "Печь"]
    assert [i["upgraded_price"] for i in by_contract if i["name"] == "Печь"] == ["360000.00"] * 2  # 20 000 × 36 / 2

    # обзор договоров лизинга
    assert client.get(f"{env['o']}/leasing", headers=aho).status_code == 403  # АХО — без права просмотра учёта
    assert client.get(f"{env['o']}/leasing", headers=env["head"]).status_code == 403  # руководитель ЦФО
    [view] = client.get(f"{env['o']}/leasing", headers=env["h"]).json()
    assert (view["counterparty_name"], view["payment"], view["months"], view["total"], view["paid"]) == \
        (env["cp"]["name"], "25000.00", 36, "900000.00", "0.00")
    assert [r["number"] for r in view["requests"]] == [oven["number"], fridge["number"]]
    assert len(view["items"]) == 3 and {i["request_number"] for i in view["items"]} == {oven["number"], None}
    assert view["payments"] and all(p["amount"] == "25000.00" for p in view["payments"])

    # снять лизинг со второй заявки — платёж договора снова 20 000; заявка снова ждёт договор
    fridge = client.delete(f"/api/asset-requests/{fridge['id']}/lease", headers=env["h"]).json()
    assert (fridge["lease_pending"], fridge["contract_id"]) == (True, None)
    [view] = client.get(f"{env['o']}/leasing", headers=env["h"]).json()
    assert view["payment"] == "20000.00"
    events = [e["action"] for e in client.get(f"/api/asset-requests/{fridge['id']}/events", headers=env["h"]).json()]
    assert events[-2:] == ["lease", "lease_removed"]

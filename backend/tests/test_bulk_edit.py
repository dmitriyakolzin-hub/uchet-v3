"""Массовое изменение операций журнала (docs/TZ.md, раздел 20, «Детали 10.1»)."""

from tests.test_closing import close, env, operation  # noqa: F401


def bulk(client, env, ids, changes, dry_run=False, h=None):
    resp = client.post(f"{env['o']}/operations/bulk-edit", headers=h or env["h"],
                       json={"ids": ids, "changes": changes, "dry_run": dry_run})
    return resp


def get(client, env, op):
    return client.get(f"/api/operations/{op['id']}", headers=env["h"]).json()


def test_bulk_edit(client, env):
    posted = [operation(client, env, "2026-03-05"), operation(client, env, "2026-03-06")]
    draft = operation(client, env, "2026-03-07", post=False)
    cancelled = operation(client, env, "2026-03-08")
    client.post(f"/api/operations/{cancelled['id']}/cancel", headers=env["h"])
    ids = [o["id"] for o in (*posted, draft, cancelled)]
    cp2 = env["post"]("counterparties", {"name": "Новый арендодатель", "kind": "legal", "inn": "7703333333"})
    contract2 = env["post"]("contracts", {"counterparty_id": cp2["id"], "legal_entity_id": env["le"].id,
                                          "currency_id": get(client, env, draft)["postings"][0]["currency_id"],
                                          "kind": "supplier", "number": "2"})
    utilities = env["art"]["Коммунальные услуги"]

    # предпросмотр: ничего не меняет
    resp = bulk(client, env, ids, [{"field": "article", "new": utilities}], dry_run=True)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert (data["changed"], data["failed"]) == (3, 1) and "Аннулированную" in data["results"][3]["error"]
    assert get(client, env, posted[0])["article_id"] == env["art"]["Аренда"]

    # смена только контрагента ломает проведённые (договор чужой) — они не меняются, черновик меняется
    data = bulk(client, env, ids, [{"field": "counterparty", "new": cp2["id"], "old": env["cp"]["id"]}]).json()
    by_id = {r["id"]: r for r in data["results"]}
    assert not by_id[posted[0]["id"]]["ok"] and "не проходит проверку" in by_id[posted[0]["id"]]["error"]
    assert by_id[draft["id"]]["changed"]
    assert get(client, env, posted[0])["postings"][0]["credit"]["counterparty"] == env["cp"]["id"]
    assert get(client, env, draft)["postings"][0]["credit"]["counterparty"] == cp2["id"]

    # контрагент и договор вместе, статья, описание, счёт — проведённые остаются проведёнными
    data = bulk(client, env, [o["id"] for o in posted], [
        {"field": "counterparty", "new": cp2["id"], "side": "credit"},
        {"field": "contract", "new": contract2["id"], "side": "credit"},
        {"field": "article", "new": utilities},
        {"field": "description", "new": "(перенесено)", "mode": "append"},
        {"field": "account", "old": env["acc"]["26"], "new": env["acc"]["44"], "side": "debit"},
    ]).json()
    assert (data["changed"], data["failed"]) == (2, 0), data
    op = get(client, env, posted[0])
    assert (op["status"], op["article_id"], op["description"]) == ("posted", utilities, "Аренда офиса (перенесено)")
    p = op["postings"][0]
    assert (p["debit_account_id"], p["credit"]["counterparty"], p["credit"]["contract"]) == \
        (env["acc"]["44"], cp2["id"], contract2["id"])
    assert p["debit"] == {"cfo": env["office"]["id"], "pl_item": env["art"]["Аренда"]}  # аналитика дебета не тронута

    # история: что было → что стало
    history = client.get(f"/api/operations/{posted[0]['id']}/history", headers=env["h"]).json()
    last = history[-1]
    assert last["action"] == "bulk_edited" and "Арендодатель → Новый арендодатель" in last["details"]
    assert "Статья операции: Аренда → Коммунальные услуги" in last["details"]

    # повтор — без изменений; статья ОПиУ в проводках: только где стоит «Аренда»
    data = bulk(client, env, [o["id"] for o in posted], [{"field": "article", "new": utilities}]).json()
    assert (data["changed"], data["unchanged"]) == (0, 2)
    data = bulk(client, env, ids[:3], [{"field": "pl_item", "new": utilities, "old": env["art"]["Аренда"]}]).json()
    assert data["changed"] == 3
    assert get(client, env, posted[1])["postings"][0]["debit"]["pl_item"] == utilities

    # дата: переносится, но не в закрытый период
    data = bulk(client, env, [draft["id"]], [{"field": "op_date", "new": "2026-04-02"}]).json()
    assert data["changed"] == 1 and get(client, env, draft)["op_date"] == "2026-04-02"
    assert close(client, env, 1).status_code == 200
    data = bulk(client, env, [draft["id"]], [{"field": "op_date", "new": "2026-01-15"}]).json()
    assert data["failed"] == 1 and "закрыт" in data["results"][0]["error"].lower()

    # ошибки запроса и права
    assert bulk(client, env, ids, [{"field": "account", "new": env["acc"]["44"]}]).status_code == 422  # без «какой»
    assert bulk(client, env, ids, [{"field": "article", "new": 999999}]).status_code == 422
    assert bulk(client, env, ids, [{"field": "article", "new": utilities}], h=env["ceo"]).status_code == 403
    assert bulk(client, env, ids, [{"field": "article", "new": utilities}], h=env["other"]).status_code == 404

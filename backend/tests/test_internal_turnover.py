"""Бюджет ЦФО и бюджет CEO, шаг 20.1: внутренние обороты между направлениями, ОПиУ по направлениям,
переключатель «ЦФО / CEO» (docs/TZ.md, раздел 29, «Детали 20.1»)."""

import datetime as dt
from decimal import Decimal

from tests.test_closing import env  # noqa: F401

DAY = dt.date.today().replace(day=1).isoformat()


def test_internal_turnover(client, env, db):  # noqa: F811
    from app.models import Article, LegalEntity

    post, acc, art = env["post"], env["acc"], env["art"]
    company = post("cfos", {"name": "Компания", "legal_entity_id": env["le"].id})
    prod = post("cfos", {"name": "Производство", "parent_id": company["id"]})
    logistics = post("cfos", {"name": "Логистика", "parent_id": company["id"]})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"]})
    online = post("cfos", {"name": "Онлайн", "parent_id": trade["id"]})
    assert {"Внутренняя выручка", "Внутренняя закупка"} <= set(art)  # системные статьи у каждой организации

    def operation(postings, article="Выручка от продаж"):
        resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
            "legal_entity_id": env["le"].id, "op_date": DAY, "article_id": art[article], "description": "x",
            "postings": postings})
        assert resp.status_code == 201, resp.text

    def sale(cfo, amount):
        operation([{"debit_account_id": acc["62.01"], "credit_account_id": acc["90.01"], "amount": amount,
                    "debit": {"counterparty": env["cp"]["id"]},
                    "credit": {"cfo": cfo["id"], "pl_item": art["Выручка от продаж"]}}])

    sale(trade, "200")
    sale(online, "50")
    operation([{"debit_account_id": acc["26"], "credit_account_id": acc["60.01"], "amount": "40",
                "debit": {"cfo": prod["id"], "pl_item": art["Аренда"]},
                "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}], "Аренда")

    url = f"{env['o']}/internal-transfers"
    body = {"on_date": DAY, "from_cfo_id": prod["id"], "to_cfo_id": logistics["id"], "amount": "100"}
    assert client.post(url, headers=env["ceo"], json=body).status_code == 403
    assert client.post(url, headers=env["h"], json={**body, "to_cfo_id": prod["id"]}).status_code == 422
    resp = client.post(url, headers=env["h"], json=body)
    assert resp.status_code == 201, resp.text
    client.post(url, headers=env["h"], json={**body, "from_cfo_id": logistics["id"], "to_cfo_id": trade["id"],
                                             "amount": "130"})
    # другое юрлицо (молочное направление) — это продажа внутри группы, а не внутренняя передача
    le2 = LegalEntity(organization_id=env["org"].id, full_name="ООО Молоко", short_name="Молоко", inn="7703333333")
    db.add(le2)
    db.commit()
    milk = post("cfos", {"name": "Молочное", "legal_entity_id": le2.id})
    resp = client.post(url, headers=env["h"], json={**body, "to_cfo_id": milk["id"]})
    assert resp.status_code == 422 and "разным юрлицам" in resp.json()["detail"]

    period = {"date_from": DAY, "date_to": dt.date.today().isoformat(), "cfo_id": company["id"]}

    def pl(**params):
        resp = client.get(f"{env['o']}/reports/pl", headers=env["h"], params={**period, **params})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        return [c["key"] for c in data["columns"]], {r["key"]: r["values"] for r in data["rows"]}, data

    keys, rows, data = pl(by="cfo")
    p, lg, t = f"cfo:{prod['id']}", f"cfo:{logistics['id']}", f"cfo:{trade['id']}"
    assert keys == [p, lg, t, "total"]  # онлайн — внутри торговли
    assert data["columns"][0]["drill"] == {"cfo_id": prod["id"]}
    money = lambda d: {k: Decimal(v) for k, v in d.items()}  # noqa: E731
    assert money(rows["revenue"]) == {p: 0, lg: 0, t: 250, "total": 250}  # выручка компании — только внешняя
    assert money(rows["internal"]) == {p: 100, lg: 30, t: -130, "total": 0}
    assert money(rows["net"]) == {p: 60, lg: 30, t: 120, "total": 210}

    # бюджет CEO: внутренних оборотов нет, прибыль компании та же
    keys, rows, _ = pl(by="cfo", view="ceo")
    assert "internal" not in rows
    assert money(rows["net"]) == {p: -40, lg: 0, t: 250, "total": 210}
    _, rows, _ = pl(view="ceo")
    assert "internal" not in rows and Decimal(rows["net"]["total"]) == 210
    _, rows, _ = pl()
    assert Decimal(rows["internal"]["total"]) == 0

    # торговля: колонки «Онлайн» и «своё»
    keys, rows, data = pl(by="cfo", cfo_id=trade["id"])
    assert keys == [f"cfo:{online['id']}", "own", "total"]
    assert data["columns"][1]["drill"] == {"a": [f"cfo:{trade['id']}"]}
    assert money(rows["revenue"]) == {f"cfo:{online['id']}": 50, "own": 200, "total": 250}
    assert client.get(f"{env['o']}/reports/pl", headers=env["h"],
                      params={**period, "by": "cfo", "compare": "avg6"}).status_code == 422
    assert client.get(f"{env['o']}/reports/pl", headers=env["h"],
                      params={**period, "by": "cfo", "compare": "year"}).status_code == 200

    # расшифровка — те же условия: в бюджете CEO внутренних проводок нет
    lines = f"{env['o']}/reports/lines"
    a = {"a": f"pl_item:{art['Внутренняя выручка']}", **period, "cfo_id": logistics["id"]}
    assert client.get(lines, headers=env["h"], params=a).json()["count"] == 1
    assert client.get(lines, headers=env["h"], params={**a, "view": "ceo"}).json()["count"] == 0

    # план внутренней выручки — только на своей стороне (иначе у ЦФО выручка и закупка погасились бы)
    from app.services import plan

    revenue = db.get(Article, art["Внутренняя выручка"])
    purchase = db.get(Article, art["Внутренняя закупка"])
    posting = plan._posting(db, "pl", revenue, prod["id"], Decimal("10"))
    assert (posting.credit.pl_item, posting.credit.cfo, posting.debit.pl_item) == (revenue.id, prod["id"], None)
    posting = plan._posting(db, "pl", purchase, logistics["id"], Decimal("10"))
    assert (posting.debit.pl_item, posting.debit.cfo, posting.credit.pl_item) == (purchase.id, logistics["id"], None)


def test_intragroup_sales(client, env, db):  # noqa: F811
    """Шаг 20.2: продажа молочного юрлица торговле (юрлицу группы) — в бюджете CEO исключается из выручки
    и той же суммой из себестоимости; в бюджете ЦФО — как есть (docs/TZ.md, раздел 29, «Детали 20.2»)."""
    from app.models import LegalEntity

    post, acc, art = env["post"], env["acc"], env["art"]
    milk_le = LegalEntity(organization_id=env["org"].id, full_name="ООО Молоко", short_name="Молоко", inn="7703333333")
    db.add(milk_le)
    db.commit()
    company = post("cfos", {"name": "Компания", "legal_entity_id": env["le"].id})
    trade = post("cfos", {"name": "Торговля", "parent_id": company["id"]})
    milk = post("cfos", {"name": "Молочное", "legal_entity_id": milk_le.id})
    shop_buyer = post("counterparties", {"name": "ООО А (торговля)", "kind": "legal", "inn": env["le"].inn})
    client_buyer = post("counterparties", {"name": "Покупатель", "kind": "legal", "inn": "7709999999"})

    def operation(le_id, postings, article):
        resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
            "legal_entity_id": le_id, "op_date": DAY, "article_id": art[article], "description": "x",
            "postings": postings})
        assert resp.status_code == 201, resp.text

    def sale(le_id, cfo, buyer, amount):
        operation(le_id, [{"debit_account_id": acc["62.01"], "credit_account_id": acc["90.01"], "amount": amount,
                           "debit": {"counterparty": buyer["id"]},
                           "credit": {"cfo": cfo["id"], "pl_item": art["Выручка от продаж"]}}], "Выручка от продаж")

    sale(milk_le.id, milk, shop_buyer, "300")  # молочка → торговля (внутри группы)
    sale(env["le"].id, trade, client_buyer, "500")  # торговля → клиент
    operation(env["le"].id, [{"debit_account_id": acc["90.02"], "credit_account_id": acc["60.01"], "amount": "300",
                              "debit": {"cfo": trade["id"], "pl_item": art["Себестоимость продаж"]},
                              "credit": {"counterparty": env["cp"]["id"]}}],
              "Себестоимость продаж")

    period = {"date_from": DAY, "date_to": dt.date.today().isoformat()}

    def pl(**params):
        resp = client.get(f"{env['o']}/reports/pl", headers=env["h"], params={**period, **params})
        assert resp.status_code == 200, resp.text
        return {r["key"]: {k: Decimal(v) for k, v in r["values"].items()} for r in resp.json()["rows"]}

    cfo_view = pl()
    assert cfo_view["revenue"]["total"] == 800 and cfo_view["cost"]["total"] == -300
    assert "intragroup:revenue" not in cfo_view
    ceo = pl(view="ceo")
    assert ceo["revenue"]["total"] == 500 and ceo["intragroup:revenue"]["total"] == -300
    assert ceo["cost"]["total"] == 0 and ceo["intragroup:cost"]["total"] == 300
    assert ceo["net"]["total"] == cfo_view["net"]["total"] == 500
    by_cfo = pl(view="ceo", by="cfo")
    assert by_cfo["intragroup:revenue"][f"cfo:{milk['id']}"] == -300  # исключение — у продавца

    # расшифровка строки исключения — продажа молочки торговле
    lines = client.get(f"{env['o']}/reports/lines", headers=env["h"], params={
        **period, "view": "ceo", "intragroup": "true", "side": "credit"}).json()
    assert lines["count"] == 1 and Decimal(lines["credit"]) == 300

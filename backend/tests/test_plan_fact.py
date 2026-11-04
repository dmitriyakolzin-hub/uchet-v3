"""План/факт: актуальный утверждённый бюджет по месяцам, выбранная версия, расшифровка (docs/TZ.md, раздел 13)."""

import io

import pytest
from openpyxl import load_workbook

from tests.test_plan_grid import env as grid_env  # noqa: F401
from tests.test_plan_grid import put
from tests.test_users import admin  # noqa: F401

PERIOD = {"date_from": "2027-01-01", "date_to": "2027-02-28"}


@pytest.fixture
def env(client, grid_env, db):  # noqa: F811
    env = grid_env
    h, o = env["h"], env["o"]
    rub = client.get("/api/currencies", headers=h).json()[0]["id"]
    cp = client.post(f"{o}/counterparties", headers=h, json={"name": "Арендодатель", "kind": "legal",
                                                            "inn": "7702222222"}).json()
    contract = client.post(f"{o}/contracts", headers=h, json={"counterparty_id": cp["id"], "legal_entity_id": env["le"].id,
                                                             "currency_id": rub, "kind": "supplier", "number": "А-1"}).json()
    env.update(cp=cp, contract=contract)
    # годовой бюджет: аренда магазина 1000 в январе и феврале, оплата поставщикам 500 в январе
    rent, pay = env["art"]["Аренда"], env["art"]["Оплата поставщикам"]
    put(client, env, [{"article_id": rent, "month": 1, "amount": "1000"}, {"article_id": rent, "month": 2, "amount": "1000"}],
        cfo="shop")
    put(client, env, [{"article_id": pay, "month": 1, "amount": "500"}], kind="cf")
    assert client.post(f"/api/budget-versions/{env['version']['id']}/approve", headers=h).status_code == 200
    # уточнённый бюджет на январь: аренда 1200
    jan = client.post(f"{o}/budgets", headers=h, json={"kind": "month", "year": 2027, "period": 1}).json()
    env["jan_version"] = jan["versions"][0]
    put(client, env, [{"article_id": rent, "month": 1, "amount": "1200"}], cfo="shop", version=env["jan_version"])
    assert client.post(f"/api/budget-versions/{env['jan_version']['id']}/approve", headers=h).status_code == 200
    # факт: аренда 900 в январе и 1100 в феврале
    acc = {v: k for k, v in env["acc"].items()}
    for date, amount in (("2027-01-15", "900"), ("2027-02-10", "1100")):
        resp = client.post(f"{o}/operations?post=true", headers=h, json={
            "legal_entity_id": env["le"].id, "op_date": date, "postings": [{
                "debit_account_id": acc["44"], "credit_account_id": acc["60.01"], "amount": amount,
                "debit": {"cfo": env["shop"]["id"], "pl_item": rent},
                "credit": {"counterparty": cp["id"], "contract": contract["id"]}}]})
        assert resp.status_code == 201, resp.text
    return env


def report(client, env, h=None, **params):
    resp = client.get(f"{env['o']}/reports/plan-fact", headers=h or env["h"], params={**PERIOD, **params})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    data["by_key"] = {r["key"]: r for r in data["rows"]}
    return data


def test_actual_budget_by_month(client, env):
    data = report(client, env)
    cols = {c["key"]: c for c in data["columns"]}
    assert cols["2027-01"]["version_id"] == env["jan_version"]["id"]  # месячный бюджет важнее годового
    assert cols["2027-02"]["version_id"] == env["version"]["id"]
    assert cols["2027-01"]["version_title"] == "Бюджет январь 2027: Версия 1"
    rent = data["by_key"][f"art:{env['art']['Аренда']}"]["values"]
    assert rent["2027-01"] == {"plan": "-1200.00", "fact": "-900.00", "dev": "300.00", "pct": "75.0"}
    assert rent["2027-02"] == {"plan": "-1000.00", "fact": "-1100.00", "dev": "-100.00", "pct": "110.0"}
    assert rent["total"] == {"plan": "-2200.00", "fact": "-2000.00", "dev": "200.00", "pct": "90.9"}
    assert data["by_key"]["net"]["values"]["total"]["plan"] == "-2200.00"
    # статьи без плана и факта не показываются, разделы ОПиУ — всегда
    assert f"art:{env['art']['Связь и IT']}" not in data["by_key"] and "revenue" in data["by_key"]


def test_selected_version(client, env):
    data = report(client, env, version_id=env["version"]["id"])
    rent = data["by_key"][f"art:{env['art']['Аренда']}"]["values"]
    assert rent["2027-01"]["plan"] == "-1000.00"
    month_only = report(client, env, version_id=env["jan_version"]["id"])
    assert month_only["by_key"][f"art:{env['art']['Аренда']}"]["values"]["2027-02"]["plan"] == "0.00"
    assert {c["key"]: c["version_id"] for c in month_only["columns"]}["2027-02"] is None


def test_plan_drill_matches_cell(client, env):
    row = report(client, env)["by_key"][f"art:{env['art']['Аренда']}"]
    params = {"date_from": "2027-01-01", "date_to": "2027-01-31", "period": "turnover", "a": row["drill"]["a"]}
    plan = client.get(f"{env['o']}/reports/lines", headers=env["h"],
                      params={**params, "plan_version_id": env["jan_version"]["id"]}).json()
    assert (plan["debit"], plan["count"]) == ("1200.00", 1)
    fact = client.get(f"{env['o']}/reports/lines", headers=env["h"], params=params).json()
    assert fact["debit"] == "900.00"


def test_cash_plan_fact(client, env):
    data = report(client, env, kind="cf")
    keys = [r["key"] for r in data["rows"]]
    assert "opening" not in keys and "closing" not in keys
    pay = data["by_key"][f"art:{env['art']['Оплата поставщикам']}"]["values"]
    assert pay["2027-01"] == {"plan": "-500.00", "fact": "0.00", "dev": "500.00", "pct": "0.0"}
    assert "title:investing" not in keys  # пустые виды деятельности убраны


def test_plan_fact_access_and_excel(client, env, make_org, headers_for, make_user):
    resp = client.get(f"{env['o']}/reports/plan-fact.xlsx", headers=env["h"], params=PERIOD)
    ws = load_workbook(io.BytesIO(resp.content)).active
    table = {row[0]: row[1:] for row in ws.iter_rows(min_row=4, values_only=True)}
    assert table["Аренда"][:3] == (-1200, -900, 300)
    assert table["Аренда"][-1] == 90.9

    assert report(client, env, h=env["ceo"])["by_key"]["net"]["values"]["total"]["fact"] == "-2000.00"
    # руководитель ЦФО — план и факт только своей ветки (магазин — в его ветке «Торговля»)
    head = report(client, env, h=env["head"])
    assert head["by_key"]["net"]["values"]["total"]["plan"] == "-2200.00"
    other = make_user(login="stranger")
    foreign = make_org("Клиент Б", members=[(other, "finance_director")])
    resp = client.get(f"/api/organizations/{foreign.id}/reports/plan-fact", headers=headers_for(other),
                      params={**PERIOD, "version_id": env["version"]["id"]})
    assert resp.status_code == 404


def cells(client, env, version, h=None, **params):
    return client.get(f"/api/budget-versions/{version['id']}/cells", headers=h or env["h"],
                      params={"kind": "pl", "article_id": env["art"]["Аренда"], "month": ["2027-01", "2027-02"], **params})


def test_plan_cells_window(client, env):
    """Шаг 12.1: окно «Плановые операции» из отчёта — те же суммы, правка в черновике (docs/TZ.md, раздел 22)."""
    year = env["version"]
    data = cells(client, env, year).json()
    # версия утверждена: фин. директор правит с историей (шаг 12.5), руководитель ЦФО — нет
    assert (data["months"], data["total"], data["editable"], data["audited"]) == ([1, 2], "2000.00", True, True)
    head = cells(client, env, year, h=env["head"]).json()
    assert head["editable"] is False and not any(c["editable"] for c in head["cells"])
    assert [(c["cfo_id"], c["month"], c["amount"]) for c in data["cells"]] == [
        (env["shop"]["id"], 1, "1000.00"), (env["shop"]["id"], 2, "1000.00")]
    assert [a["id"] for a in data["articles"]] == [env["art"]["Аренда"]]
    # итог окна = цифра плана в отчёте за выбранную версию
    row = report(client, env, version_id=year["id"])["by_key"][f"art:{env['art']['Аренда']}"]
    assert row["values"]["total"]["plan"] == "-" + data["total"]
    # фильтры отчёта: ЦФО вне ветки — пусто; месяцы вне бюджета игнорируются
    other = client.post(f"{env['o']}/cfos", headers=env["h"], json={"name": "Другая компания"}).json()
    assert cells(client, env, year, cfo_id=other["id"]).json()["cells"] == []
    assert cells(client, env, year, month=["2026-12"]).json()["months"] == []

    # новая версия-черновик: окно можно править через ту же запись ячеек
    draft = client.post(f"/api/budgets/{year['budget_id']}/versions", headers=env["h"],
                        json={"name": "Пересмотр", "copy_from_id": year["id"]}).json()
    data = cells(client, env, draft).json()
    assert data["editable"] is True and data["total"] == "2000.00", data
    put(client, env, [{"article_id": env["art"]["Аренда"], "month": 2, "amount": "1500"}], cfo="shop", version=draft)
    assert cells(client, env, draft).json()["total"] == "2500.00"
    # CEO смотрит, но не правит; статья не этой организации — не найдено
    ceo = cells(client, env, draft, h=env["ceo"]).json()
    assert ceo["editable"] is False and ceo["total"] == "2500.00"
    assert cells(client, env, draft, article_id=10**9).status_code == 404


def test_top_down_distribution(client, env):
    """Шаг 12.3: цель компании делится на нижние ЦФО по факту 6 месяцев; факта нет — поровну (раздел 22)."""
    h, o = env["h"], env["o"]
    acc = {v: k for k, v in env["acc"].items()}
    rent, it = env["art"]["Аренда"], env["art"]["Связь и IT"]
    # факт аренды в октябре 2026 (база для плана 2027): магазин 900, логистика 300
    for cfo, amount in (("shop", "900"), ("logistics", "300")):
        resp = client.post(f"{o}/operations?post=true", headers=h, json={
            "legal_entity_id": env["le"].id, "op_date": "2026-10-15", "postings": [{
                "debit_account_id": acc["44"], "credit_account_id": acc["60.01"], "amount": amount,
                "debit": {"cfo": env[cfo]["id"], "pl_item": rent},
                "credit": {"counterparty": env["cp"]["id"], "contract": env["contract"]["id"]}}]})
        assert resp.status_code == 201, resp.text
    year = env["version"]
    draft = client.post(f"/api/budgets/{year['budget_id']}/versions", headers=h, json={"name": "Сверху вниз"}).json()
    url = f"/api/budget-versions/{draft['id']}/targets"
    company = env["company"]["id"]
    resp = client.post(f"{url}/distribute", headers=h, json={
        "cfo_id": company, "article_id": rent, "targets": [{"month": 1, "amount": "1200"}, {"month": 2, "amount": "1000.01"}]})
    assert resp.status_code == 200, resp.text
    months = {m["month"]: m for m in resp.json()["months"]}
    shop, logistics = str(env["shop"]["id"]), str(env["logistics"]["id"])
    assert months[1]["by_fact"] is True and months[1]["parts"] == {shop: "900.00", logistics: "300.00"}
    assert months[2]["parts"] == {shop: "750.01", logistics: "250.00"}  # копейка — самой крупной доле
    assert months[1]["base_to"] < "2027-01-01"
    # нижние ЦФО получили план; цель и «по низу» совпадают
    targets = {t["month"]: t for t in resp.json()["targets"]}
    assert (targets[1]["target"], targets[1]["bottom"], targets[1]["deviation"]) == ("1200.00", "1200.00", "0.00")
    # правка внизу вручную — видно отклонение от цели
    put(client, env, [{"article_id": rent, "month": 1, "amount": "1000"}], cfo="shop", version=draft)
    jan = next(t for t in client.get(url, headers=h, params={"cfo_id": company}).json() if t["month"] == 1)
    assert (jan["bottom"], jan["deviation"]) == ("1300.00", "100.00")
    # нет факта — поровну
    months = client.post(f"{url}/distribute", headers=h, json={
        "cfo_id": company, "article_id": it, "targets": [{"month": 3, "amount": "100.01"}]}).json()["months"]
    assert months[0]["by_fact"] is False and sorted(months[0]["parts"].values()) == ["50.00", "50.01"]
    # пустая цель — снимается, план внизу остаётся
    client.post(f"{url}/distribute", headers=h, json={"cfo_id": company, "article_id": it,
                                                      "targets": [{"month": 3, "amount": None}]})
    assert [t["month"] for t in client.get(url, headers=h, params={"cfo_id": company}).json() if t["article_id"] == it] == []
    # права: CEO — нельзя; утверждённая версия — руководителю ЦФО нельзя
    assert client.post(f"{url}/distribute", headers=env["ceo"], json={
        "cfo_id": company, "article_id": rent, "targets": [{"month": 1, "amount": "1"}]}).status_code == 403
    assert client.post(f"/api/budget-versions/{year['id']}/targets/distribute", headers=env["head"], json={
        "cfo_id": env["trade"]["id"], "article_id": rent, "targets": [{"month": 1, "amount": "1"}]}).status_code == 422


def test_retrospective_average(client, env, db):
    """Шаг 12.4: пустые ячейки — среднее за N месяцев перед каждым месяцем; прошедшие — факт, будущие — план;
    заполненные ячейки не меняются (docs/TZ.md, раздел 22). Сегодня — 10.03.2027 (факт аренды: январь 900, февраль 1100)."""
    import datetime as dt

    from app.models import BudgetVersion, Cfo, User
    from app.services import plan_methods

    h, rent = env["h"], env["art"]["Аренда"]
    year = env["version"]
    draft = client.post(f"/api/budgets/{year['budget_id']}/versions", headers=h, json={"name": "По среднему"}).json()
    put(client, env, [{"article_id": rent, "month": 5, "amount": "5"}], cfo="shop", version=draft)
    version, shop = db.get(BudgetVersion, draft["id"]), db.get(Cfo, env["shop"]["id"])
    user = db.query(User).filter_by(login="fd").one()
    result = plan_methods.retrospective(db, version, "pl", shop, False, 3, user, today=dt.date(2027, 3, 10))
    db.commit()
    assert result.kept == 1 and result.filled > 0
    grid = client.get(f"/api/budget-versions/{draft['id']}/grid", headers=h,
                      params={"kind": "pl", "cfo_id": env["shop"]["id"]}).json()
    values = next(r for r in grid["rows"] if r["article_id"] == rent)["values"]
    assert "2027-01" not in values  # база окт–дек 2026 — факта нет
    assert (values["2027-02"], values["2027-03"]) == ("300.00", "666.67")  # (0 + 0 + 900) / 3; (0 + 900 + 1100) / 3
    assert values["2027-04"] == "888.89"  # март — текущий: берётся план марта
    assert values["2027-05"] == "5.00"  # заполненное не тронуто
    assert values["2027-06"] == "520.19"  # (666,67 + 888,89 + 5) / 3

    # через API: ДДС без ЦФО; база не из списка — ошибка; CEO — нельзя
    url = f"/api/budget-versions/{draft['id']}/retrospective"
    resp = client.post(url, headers=h, json={"kind": "cf", "base": 6})
    assert resp.status_code == 200 and resp.json()["cfos"] == 1, resp.text
    assert client.post(url, headers=h, json={"kind": "pl", "cfo_id": env["company"]["id"], "branch": True,
                                             "base": 12}).json()["cfos"] == 4
    assert client.post(url, headers=h, json={"kind": "pl", "cfo_id": env["shop"]["id"], "base": 2}).status_code == 422
    assert client.post(url, headers=env["ceo"], json={"kind": "pl", "cfo_id": env["shop"]["id"],
                                                      "base": 3}).status_code == 403
    assert client.post(url, headers=env["head"], json={"kind": "cf", "base": 3}).status_code == 404

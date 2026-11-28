"""План по методам на месяц, шаги 23.2–24.4 (docs/TZ.md, разделы 31–32). Данные вымышленные."""

import datetime as dt

from tests.test_plan_grid import env, put  # noqa: F401
from tests.test_users import admin  # noqa: F401

MONTH = dt.date(2027, 3, 1)


def setup(client, env, db):
    """Статьи с методами, форма отчёта, сотрудник с окладом, регламентная операция, факт и план."""
    from app.models import ReportFormRow, RoutineRule

    o, h, art = env["o"], env["h"], env["art"]
    wages, tax = art["Оплата труда — управленческий персонал"], art["Страховые взносы с оплаты труда"]
    rent, utilities, revenue = art["Аренда"], art["Коммунальные услуги"], art["Реклама и маркетинг"]
    sales, it = art["Выручка от продаж"], art["Связь и IT"]

    def method(article_id, value, months=None):
        resp = client.put(f"/api/articles/{article_id}/plan-method", headers=h,
                          json={"plan_method": value, "plan_avg_months": months})
        assert resp.status_code == 200, resp.text

    method(wages, "norm")
    method(tax, "calc")
    method(rent, "fact")
    method(utilities, "avg", 3)
    method(revenue, "executor")
    method(it, "top")
    # sales — без метода: «Снизу ЦФО»

    for code, name, article_id in (("1.0", "Выручка", sales), ("2.1", "Оплата труда", wages),
                                   ("2.2", "Взносы", tax), ("2.3", "Аренда", rent), ("2.4", "Коммунальные", utilities),
                                   ("2.5", "Реклама", revenue), ("2.6", "Связь", it)):
        db.add(ReportFormRow(organization_id=env["org"].id, position=len(code) * 10 + int(code[-1]), code=code,
                             name=name, kind="article", article_id=article_id))
    db.add(ReportFormRow(organization_id=env["org"].id, position=100, code="3.0", name="Прибыль", kind="indicator",
                         formula="1.0 - 2.1 - 2.2 - 2.3 - 2.4 - 2.5 - 2.6"))
    db.commit()

    # нормативный: оклад сотрудника магазина со статьёй затрат «Оплата труда — управленческий персонал»
    person = client.post(f"{o}/employees", headers=h, json={
        "full_name": "Иванов И.", "cfo_id": env["shop"]["id"], "legal_entity_id": env["le"].id, "pl_item_id": wages,
        "hired_on": "2026-01-10"}).json()
    resp = client.post(f"/api/employees/{person['id']}/salaries", headers=h,
                       json={"valid_from": "2026-01-01", "amount": "100000"})
    assert resp.status_code in (200, 201), resp.text
    # расчётный: 30,2 % от строки 2.1
    assert client.post(f"{o}/plan-norms", headers=h, json={"article_id": tax, "kind": "calc", "valid_from": "2027-01-01",
                                                           "base_form_code": "2.1", "percent": "30.2"}).status_code == 201
    # фактический: регламентная операция 40 000 (магазин) + 10 000 вручную
    accounts = {code: i for i, code in env["acc"].items()}
    db.add(RoutineRule(organization_id=env["org"].id, name="Аренда магазина", legal_entity_id=env["le"].id, day=5,
                       valid_from=dt.date(2026, 1, 1), template={"article_id": rent, "description": "Аренда", "postings": [
                           {"debit_account_id": accounts["44"], "credit_account_id": accounts["60.01"], "amount": "40000",
                            "debit": {"cfo": env["shop"]["id"], "pl_item": rent}, "credit": {}}]}))
    db.commit()
    assert client.post(f"{o}/plan-norms", headers=h, json={"article_id": rent, "kind": "fact", "valid_from": "2027-01-01",
                                                           "amount": "10000", "cfo_id": env["shop"]["id"]}).status_code == 201
    # средний за 3 месяца: факт 9 000 в декабре и 3 000 в феврале (январь пустой)
    cp = client.post(f"{o}/counterparties", headers=h, json={"name": "Энергосбыт", "kind": "legal",
                                                            "inn": "7703333333"}).json()
    for day, amount in (("2026-12-10", "9000"), ("2027-02-10", "3000")):
        resp = client.post(f"{o}/operations?post=true", headers=h, json={
            "legal_entity_id": env["le"].id, "op_date": day, "article_id": utilities, "description": "Свет",
            "postings": [{"debit_account_id": accounts["44"], "credit_account_id": accounts["76"], "amount": amount,
                          "debit": {"cfo": env["shop"]["id"], "pl_item": utilities}, "credit": {"counterparty": cp["id"]}}]})
        assert resp.status_code == 201, resp.text
    # факт аренды в феврале (средний 6 мес. = 60 000 / 6) и план «снизу ЦФО» по аренде — колонки независимы
    resp = client.post(f"{o}/operations?post=true", headers=h, json={
        "legal_entity_id": env["le"].id, "op_date": "2027-02-05", "article_id": rent, "description": "Аренда",
        "postings": [{"debit_account_id": accounts["44"], "credit_account_id": accounts["76"], "amount": "60000",
                      "debit": {"cfo": env["shop"]["id"], "pl_item": rent}, "credit": {"counterparty": cp["id"]}}]})
    assert resp.status_code == 201, resp.text
    assert put(client, env, [{"article_id": rent, "month": 3, "amount": "45000"}], cfo="shop").status_code == 200
    # снизу ЦФО — выручка; снизу ЦФУ — реклама с исполнителем
    executor = client.post(f"{o}/executors", headers=h, json={"name": "Маркетинг"}).json()
    assert put(client, env, [{"article_id": sales, "month": 3, "amount": "500000"}], cfo="shop").status_code == 200
    resp = client.put(f"/api/budget-versions/{env['version']['id']}/cells", headers=h, json={
        "kind": "pl", "cfo_id": env["shop"]["id"], "executor_id": executor["id"],
        "cells": [{"article_id": revenue, "month": 3, "amount": "20000"}]})
    assert resp.status_code == 200, resp.text
    return executor


def test_method_plan(client, env, db):  # noqa: F811
    from app.services import method_plan
    from app.services.reports import ReportFilter

    setup(client, env, db)
    f = ReportFilter(organization_id=env["org"].id, date_from=MONTH, date_to=MONTH)
    rows = {r.row.code: r for r in method_plan.build(db, f, env["version"]["id"], MONTH, today=dt.date(2027, 4, 15))}

    D = __import__("decimal").Decimal

    def columns(code, **expected):
        values = rows[code].values
        got = {k: values[k] for k in method_plan.METHOD_KEYS if values[k] is not None}
        assert got == {k: D(v) for k, v in expected.items()}, (code, values)

    # все колонки 1–6 считаются сразу (24.4); пусто — у метода нет данных
    columns("1.0", cfo="500000")
    columns("2.1", norm="100000")
    columns("2.2", calc="30200")  # 30,2 % от ИТОГ 2.1; ИТОГ нормативной статьи — сразу её колонка (24.5)
    assert rows["2.2"].note == "30,2 % от 2.1 (база 100 000,00)"
    columns("2.3", fact="50000", avg="10000", cfo="45000")
    assert rows["2.3"].method == "fact" and rows["2.3"].notes["avg"].startswith("6 мес.")
    columns("2.4", avg="4000")  # (9 000 + 0 + 3 000) / 3; окно — декабрь–февраль
    columns("2.5", executor="20000")
    columns("2.6")
    assert rows["2.6"].values["itog"] == 0 and rows["2.6"].method == "top"
    # ИТОГ не выбран — равен бюджету (решение владельца 1 в)
    # у расчётных и нормативных — сразу своя колонка (24.5)
    assert {c: (rows[c].values["itog"], rows[c].itog_source, rows[c].itog_auto) for c in ("1.0", "2.1", "2.2", "2.3")} \
        == {"1.0": (500000, "budget", False), "2.1": (100000, "norm", True), "2.2": (30200, "calc", True),
            "2.3": (45000, "budget", False)}
    assert (rows["2.1"].values["diff"], rows["3.0"].values["budget"]) == (-100000, 500000 - 45000 - 20000)
    assert rows["3.0"].values["fact"] == -50000 and rows["3.0"].values["cfo"] == 500000 - 45000

    # фильтр ЦФО: другая ветка — у магазина ничего нет
    other = method_plan.build(db, f.__class__(organization_id=env["org"].id, date_from=MONTH, date_to=MONTH,
                                              cfo_id=env["logistics"]["id"]), env["version"]["id"], MONTH,
                              today=dt.date(2027, 4, 15))
    assert {r.row.code: (r.values["norm"], r.values["fact"], r.values["avg"]) for r in other
            if r.row.code in ("2.1", "2.3", "2.4")} == \
        {"2.1": (0, None, None), "2.3": (None, 0, None), "2.4": (None, None, 0)}


def test_method_plan_api(client, env, db):  # noqa: F811
    setup(client, env, db)
    url = f"{env['o']}/reports/method-plan"
    params = {"date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03", "version_id": env["version"]["id"]}
    resp = client.get(url, headers=env["h"], params=params)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [c["key"] for c in data["columns"]][-3:] == ["itog", "budget", "diff"]
    row = next(r for r in data["rows"] if r["code"] == "2.2")
    assert (row["method"], row["values"]["calc"], row["values"]["itog"], row["itog_source"], row["itog_auto"]) == \
        ("calc", "30200.00", "30200.00", "calc", True)
    assert "notes" in row and row["notes"]["calc"].startswith("30,2 %")
    assert client.get(url, headers=env["h"], params=params | {"month": "2028-01"}).status_code == 422
    assert client.get(url, headers=env["h"], params=params | {"month": "март"}).status_code == 422
    assert client.get(f"{url}.xlsx", headers=env["h"], params=params).status_code == 200
    # руководитель ЦФО (ветка «Торговля») — видит свою ветку
    assert client.get(url, headers=env["head"], params=params).status_code == 200


def test_executor_head_enters_own_plan(client, env, db, make_user, headers_for, admin):  # noqa: F811
    """Шаг 23.3: руководитель ЦФУ видит бюджеты, смотрит и вводит план только своего ЦФУ."""
    executor = setup(client, env, db)
    other = client.post(f"{env['o']}/executors", headers=env["h"], json={"name": "Склад"}).json()
    boss = make_user(login="marketing")
    resp = client.put(f"/api/users/{boss.id}/memberships", headers=headers_for(admin), json={"memberships": [
        {"organization_id": env["org"].id, "role": "executor_head", "executor_id": executor["id"]}]})
    assert resp.status_code == 200, resp.text
    h = headers_for(boss)
    version = env["version"]["id"]
    advert = env["art"]["Реклама и маркетинг"]
    assert [b["id"] for b in client.get(f"{env['o']}/budgets", headers=h, params={"year": 2027}).json()] == \
        [env["budget"]["id"]]

    def cells(executor_id, amount="7000"):
        return client.put(f"/api/budget-versions/{version}/cells", headers=h, json={
            "kind": "pl", "cfo_id": env["shop"]["id"], "executor_id": executor_id,
            "cells": [{"article_id": advert, "month": 3, "amount": amount}]})

    assert cells(None).status_code == 403
    assert cells(other["id"]).status_code == 403
    assert cells(executor["id"], "25000").status_code == 200
    data = client.get(f"/api/budget-versions/{version}/cells", headers=h,
                      params={"kind": "pl", "article_id": advert, "month": "2027-03"}).json()
    assert data["editable"] is True and [c["amount"] for c in data["cells"]] == ["25000.00"]
    # чужой ЦФУ в окне не виден; сетка бюджета и инструменты плана — нет
    client.put(f"/api/budget-versions/{version}/cells", headers=env["h"], json={
        "kind": "pl", "cfo_id": env["shop"]["id"], "executor_id": other["id"],
        "cells": [{"article_id": advert, "month": 3, "amount": "1000"}]})
    data = client.get(f"/api/budget-versions/{version}/cells", headers=h,
                      params={"kind": "pl", "article_id": advert, "month": "2027-03"}).json()
    assert [c["executor_id"] for c in data["cells"]] == [executor["id"]]
    assert client.get(f"/api/budget-versions/{version}/grid", headers=h,
                      params={"kind": "pl", "cfo_id": env["shop"]["id"]}).status_code in (403, 404)
    # «План по методам» руководителю ЦФУ — по его ЦФУ
    resp = client.get(f"{env['o']}/reports/method-plan", headers=h, params={
        "date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03", "version_id": version})
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["rows"] if r["code"] == "2.5")
    assert (row["values"]["executor"], row["plan_kind"]) == ("25000.00", "pl")


def test_itog_and_fix(client, env, db):  # noqa: F811
    """Шаги 23.4 и 24.4: ИТОГ — колонка или своя сумма (финансовый директор); фиксация по правилам 24.4."""
    executor = setup(client, env, db)
    version = env["version"]["id"]
    art = env["art"]
    url = f"{env['o']}/reports/method-plan"
    params = {"date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03", "version_id": version}
    itog = f"/api/budget-versions/{version}/itog"

    def rows():
        data = client.get(url, headers=env["h"], params=params).json()
        return data, {r["code"]: r for r in data["rows"]}

    def choose(name, **body):
        return client.put(itog, headers=env["h"], json={"month": "2027-03", "article_id": art[name]} | body)

    body = {"month": "2027-03", "article_id": art["Связь и IT"], "amount": "15000"}
    assert client.put(itog, headers=env["ceo"], json=body).status_code == 403
    assert client.put(itog, headers=env["h"], json=body | {"month": "2028-01"}).status_code == 422
    assert client.put(itog, headers=env["h"], json=body).status_code == 200  # своя сумма
    assert choose("Связь и IT", source="norm").status_code == 422  # в колонке нет значения
    # ИТОГ оплаты труда — из «Нормативного»; расчётные взносы считаются от ИТОГ
    assert choose("Оплата труда — управленческий персонал", source="norm").status_code == 200
    assert choose("Страховые взносы с оплаты труда", source="calc").status_code == 200
    # реклама: и снизу ЦФО (8 000), и снизу ЦФУ (20 000) — ИТОГ не выбран, при фиксации остаётся большая
    assert put(client, env, [{"article_id": art["Реклама и маркетинг"], "month": 3, "amount": "8000"}],
               cfo="shop").status_code == 200
    # выручка: снизу ЦФУ 100 000, ИТОГ — «Снизу ЦФО» — ЦФУ при фиксации удаляется
    resp = client.put(f"/api/budget-versions/{version}/cells", headers=env["h"], json={
        "kind": "pl", "cfo_id": env["shop"]["id"], "executor_id": executor["id"],
        "cells": [{"article_id": art["Выручка от продаж"], "month": 3, "amount": "100000"}]})
    assert resp.status_code == 200, resp.text
    assert choose("Выручка от продаж", source="cfo").status_code == 200
    data, by = rows()
    assert (by["2.6"]["values"]["itog"], by["2.6"]["itog_source"], by["2.6"]["values"]["diff"]) == \
        ("15000.00", "manual", "-15000.00")
    assert (by["2.1"]["values"]["itog"], by["2.1"]["itog_source"]) == ("100000.00", "norm")
    assert (by["2.2"]["values"]["calc"], by["2.2"]["values"]["itog"]) == ("30200.00", "30200.00")
    assert (by["1.0"]["values"]["itog"], by["1.0"]["values"]["budget"]) == ("500000.00", "600000.00")
    # ИТОГ не выбран, заполнены обе «снизу» — большая из них (24.7)
    assert (by["2.5"]["values"]["itog"], by["2.5"]["itog_source"], by["2.5"]["values"]["budget"]) == \
        ("20000.00", "bottom", "28000.00")
    assert data["fixed_at"] is None and data["itog_scope"] is True

    assert client.post(f"{itog}/fix", headers=env["ceo"], json={"month": "2027-03"}).status_code == 403
    resp = client.post(f"{itog}/fix", headers=env["h"], json={"month": "2027-03"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["written"], resp.json()["cleared"]) == (2, 2)
    data, by = rows()
    assert data["fixed_at"] is not None and data["fixed_by"]
    assert (by["2.1"]["values"]["budget"], by["2.2"]["values"]["budget"]) == ("100000.00", "30200.00")
    assert (by["1.0"]["values"]["budget"], by["1.0"]["values"]["executor"]) == ("500000.00", None)
    assert (by["2.5"]["values"]["budget"], by["2.5"]["values"]["cfo"]) == ("20000.00", None)
    assert (by["2.3"]["values"]["budget"], by["2.3"]["values"]["itog"]) == ("45000.00", "45000.00")  # не тронута
    assert (by["2.6"]["values"]["budget"], by["2.6"]["values"]["itog"]) == ("0.00", "15000.00")  # только цель
    assert by["2.1"]["itog_fixed"] is True
    cells = client.get(f"/api/budget-versions/{version}/cells", headers=env["h"], params={
        "kind": "pl", "article_id": art["Страховые взносы с оплаты труда"], "month": "2027-03"}).json()["cells"]
    assert [c["cfo_id"] for c in cells] == [env["company"]["id"]]
    # повторная фиксация ничего не меняет; история
    again = client.post(f"{itog}/fix", headers=env["h"], json={"month": "2027-03"}).json()
    assert (again["written"], again["cleared"]) == (0, 0)
    events = client.get(f"/api/budget-versions/{version}/itog-events", headers=env["ceo"],
                        params={"month": "2027-03"}).json()
    assert [e["action"] for e in events][:2] == ["fix", "fix"] and events[-1]["action"] == "edit"
    # сбросить ИТОГ — снова равен бюджету
    choose("Связь и IT", amount=None)
    _, by = rows()
    assert (by["2.6"]["values"]["itog"], by["2.6"]["itog_source"]) == ("0.00", "budget")
    # с фильтром юрлица ИТОГ не показывается как сохранённый
    data = client.get(url, headers=env["h"], params=params | {"legal_entity_id": env["le"].id}).json()
    assert data["itog_scope"] is False


def test_itog_own_methods(client, env, db):  # noqa: F811
    """24.4: «ИТОГ = основной метод» у статей, где ИТОГ не выбран."""
    setup(client, env, db)
    version = env["version"]["id"]
    resp = client.post(f"/api/budget-versions/{version}/itog/own-methods", headers=env["h"], json={"month": "2027-03"})
    assert resp.status_code == 200, resp.text
    data = client.get(f"{env['o']}/reports/method-plan", headers=env["h"], params={
        "date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03", "version_id": version}).json()
    by = {r["code"]: r for r in data["rows"]}
    assert {c: (by[c]["values"]["itog"], by[c]["itog_source"]) for c in ("2.1", "2.2", "2.3", "2.6")} == {
        "2.1": ("100000.00", "norm"), "2.2": ("30200.00", "calc"), "2.3": ("50000.00", "fact"),
        "2.6": ("0.00", "budget")}
    assert client.post(f"/api/budget-versions/{version}/itog/own-methods", headers=env["ceo"],
                       json={"month": "2027-03"}).status_code == 403


def test_norm_amount_and_breakdown(client, env, db):  # noqa: F811
    """Шаг 24.2: сумма «Нормативный» из «Нормативов» прибавляется к окладам; у значений методов — расшифровка по ЦФО."""
    setup(client, env, db)
    wages = env["art"]["Оплата труда — управленческий персонал"]
    resp = client.post(f"{env['o']}/plan-norms", headers=env["h"], json={
        "article_id": wages, "kind": "norm", "valid_from": "2027-01-01", "amount": "20000",
        "cfo_id": env["logistics"]["id"]})
    assert resp.status_code == 201, resp.text
    assert client.post(f"{env['o']}/plan-norms", headers=env["h"], json={
        "article_id": wages, "kind": "norm", "valid_from": "2027-02-01"}).status_code == 422  # без суммы
    data = client.get(f"{env['o']}/reports/method-plan", headers=env["h"], params={
        "date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03",
        "version_id": env["version"]["id"]}).json()
    by = {r["code"]: r for r in data["rows"]}
    assert by["2.1"]["values"]["norm"] == "120000.00" and "норматив" in by["2.1"]["note"]
    assert by["2.1"]["breakdown"]["norm"] == [{"cfo": "Магазин 1", "amount": "100000.00"},
                                              {"cfo": "Логистика", "amount": "20000.00"}]
    assert [x["cfo"] for x in by["2.3"]["breakdown"]["fact"]] == ["Магазин 1"]
    assert by["2.2"]["breakdown"]["calc"] == [{"cfo": "без ЦФО", "amount": "36240.00"}]  # 30,2 % от 120 000


def test_calc_base_from_column_4(client, env, db):  # noqa: F811
    """24.5: база расчётного — колонка 4 расчётной базовой статьи (даже если её ИТОГ другой), иначе — ИТОГ."""
    setup(client, env, db)
    version = env["version"]["id"]
    it, tax = env["art"]["Связь и IT"], env["art"]["Страховые взносы с оплаты труда"]
    assert client.post(f"{env['o']}/plan-norms", headers=env["h"], json={
        "article_id": it, "kind": "calc", "valid_from": "2027-01-01", "base_article_id": tax,
        "percent": "10"}).status_code == 201
    # ИТОГ взносов — своя сумма; база «Связи» всё равно колонка 4 взносов (30 200)
    assert client.put(f"/api/budget-versions/{version}/itog", headers=env["h"], json={
        "month": "2027-03", "article_id": tax, "source": "manual", "amount": "50000"}).status_code == 200
    data = client.get(f"{env['o']}/reports/method-plan", headers=env["h"], params={
        "date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03", "version_id": version}).json()
    by = {r["code"]: r for r in data["rows"]}
    assert (by["2.2"]["values"]["itog"], by["2.2"]["values"]["calc"]) == ("50000.00", "30200.00")
    assert by["2.6"]["values"]["calc"] == "3020.00"
    assert (by["2.6"]["values"]["itog"], by["2.6"]["itog_auto"]) == ("0.00", False)  # метод «Сверху ИТОГ» — не авто
    # фиксация: нормативная и расчётная статьи без «снизу» пишутся в бюджет сами
    resp = client.post(f"/api/budget-versions/{version}/itog/fix", headers=env["h"], json={"month": "2027-03"})
    assert resp.status_code == 200, resp.text
    data = client.get(f"{env['o']}/reports/method-plan", headers=env["h"], params={
        "date_from": "2027-03-01", "date_to": "2027-03-31", "month": "2027-03", "version_id": version}).json()
    by = {r["code"]: r for r in data["rows"]}
    assert (by["2.1"]["values"]["budget"], by["2.1"]["itog_source"], by["2.1"]["itog_fixed"]) == \
        ("100000.00", "norm", True)
    assert by["2.2"]["values"]["budget"] == "0.00"  # ИТОГ — своя сумма: только цель

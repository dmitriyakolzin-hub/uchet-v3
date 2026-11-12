"""Сравнение отчётов с прошлым годом, прошлым месяцем, средним за 6 месяцев и «косты» — доля к выручке
(docs/TZ.md, раздел 27, «Детали 17.1»). Данные вымышленные."""

import datetime as dt
import io

from openpyxl import load_workbook

from app.services.report_compare import shift
from tests.test_management_report import FORM, money_line, post
from tests.test_article_import import load, preview, xlsx
from tests.test_closing import env as closing_env  # noqa: F401
from tests.test_money_page import env as money_env  # noqa: F401
from tests.test_payment_requests import env  # noqa: F401
from tests.test_reports_pl import month_data, pl


def test_shift():
    assert shift(dt.date(2026, 3, 31), -1) == dt.date(2026, 2, 28)
    assert shift(dt.date(2026, 2, 28), -12) == dt.date(2025, 2, 28)
    assert shift(dt.date(2024, 2, 29), 12) == dt.date(2025, 2, 28)
    assert shift(dt.date(2026, 1, 15), -1) == dt.date(2025, 12, 15)
    assert shift(dt.date(2026, 3, 30), -1) == dt.date(2026, 2, 28)


def test_pl_compare_and_costs(client, money_env):  # noqa: F811
    env = money_env
    month_data(client, env)  # октябрь: выручка 10 000, расходы на продажу 3 000; ноябрь — без выручки
    nov = {"date_from": "2026-11-01", "date_to": "2026-11-30"}

    data = pl(client, env, **nov, compare="month")
    rows = data["by_key"]
    assert (data["compare"], data["compare_title"]) == ("month", "с прошлым месяцем")
    assert rows["revenue"]["compare"] == {"2026-11": "10000.00", "total": "10000.00"}
    assert rows["selling"]["compare"]["2026-11"] == "-3000.00"
    assert rows["revenue"]["better"] == 1

    rows = pl(client, env, **nov, compare="avg6")["by_key"]
    assert rows["revenue"]["compare"] == {"2026-11": "1666.67", "total": "1666.67"}  # 10 000 / 6
    rows = pl(client, env, **nov, compare="year")["by_key"]
    assert rows["revenue"]["compare"]["2026-11"] == "0.00"
    assert pl(client, env, **nov)["by_key"]["revenue"]["compare"] is None  # без переключателя — нет

    # два месяца — каждый со своим предыдущим, «Итого» — со сдвинутым периодом
    rows = pl(client, env, date_from="2026-10-01", date_to="2026-11-30", compare="month")["by_key"]
    assert rows["revenue"]["compare"] == {"2026-10": "0.00", "2026-11": "10000.00", "total": "10000.00"}

    data = pl(client, env, date_from="2026-10-01", date_to="2026-10-31", costs=True)
    assert data["revenue"] == {"2026-10": "10000.00", "total": "10000.00"}
    # ДДС — выручка ОПиУ
    dds = client.get(f"{env['o']}/reports/dds", headers=env["h"],
                     params={"date_from": "2026-10-01", "date_to": "2026-10-31", "costs": True, "compare": "year"})
    assert dds.status_code == 200, dds.text
    assert dds.json()["revenue"]["2026-10"] == "10000.00" and dds.json()["compare"] == "year"

    resp = client.get(f"{env['o']}/reports/pl.xlsx", headers=env["h"],
                      params={**nov, "compare": "month", "costs": True})
    assert resp.status_code == 200
    ws = load_workbook(io.BytesIO(resp.content)).active
    headers = [c.value for row in ws.iter_rows(max_row=6) for c in row if c.value]
    assert "Ноябрь 2026: сравнение" in headers and "Ноябрь 2026: откл. %" in headers
    assert "Ноябрь 2026: % выручки" in headers
    assert client.get(f"{env['o']}/reports/pl", headers=env["h"],
                      params={**nov, "compare": "week"}).status_code == 422


def test_management_compare_and_costs(client, env):  # noqa: F811
    load(client, env, preview(client, env, xlsx(FORM)))
    arts = {a["code"]: a["id"] for a in client.get(f"{env['o']}/articles", headers=env["h"]).json() if a["code"]}
    office, cp, contract = env["office"]["id"], env["cp"]["id"], env["contract"]["id"]
    sale = {"counterparty": cp, "contract": contract}
    post(client, env, money_line(env, "62.01", "90.01", "120000", sale, {"cfo": office, "pl_item": arts["1.1."]}))
    post(client, env, money_line(env, "90.01", "62.01", "20000", {"cfo": office, "pl_item": arts["1.2."]}, sale))
    post(client, env, money_line(env, "26", "60.01", "25000", {"cfo": office, "pl_item": arts["2.3.1."]}, sale))

    april = {"date_from": "2026-04-01", "date_to": "2026-04-30"}
    resp = client.get(f"{env['o']}/reports/management", headers=env["h"],
                      params={**april, "compare": "month", "costs": True})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    rows = {r["code"]: r for r in data["rows"] if r["code"]}
    assert rows["1.0"]["compare"]["2026-04"] == "100000.00"  # выручка со скидками за март
    assert rows["2.3.1"]["compare"]["2026-04"] == "25000.00"
    assert (rows["1.0"]["better"], rows["1.1"]["better"], rows["2.3"]["better"], rows["3.1"]["better"]) == (1, 1, -1, 1)
    assert rows["1.2"]["better"] == -1  # скидки показаны положительным числом — их рост хуже
    assert data["rows"][0]["compare"] is None  # заголовок
    assert data["revenue_title"] == "1.0 Выручка со скидками" and data["revenue"]["2026-04"] == "0.00"

    march = client.get(f"{env['o']}/reports/management", headers=env["h"], params={
        "date_from": "2026-03-01", "date_to": "2026-03-31", "costs": True}).json()
    assert march["revenue"]["2026-03"] == "100000.00"
    # в режиме плана сравнения нет
    plan = client.get(f"{env['o']}/reports/management", headers=env["h"],
                      params={**april, "mode": "plan_fact", "compare": "month"}).json()
    assert plan["compare"] is None
    resp = client.get(f"{env['o']}/reports/management.xlsx", headers=env["h"],
                      params={**april, "compare": "avg6", "costs": True})
    assert resp.status_code == 200

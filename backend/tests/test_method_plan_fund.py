"""Расчётный метод для статьи только ДДС (фонд учредителей) от показателя формы «1.0 = 1.1 − 1.2»
(docs/TZ.md, раздел 32, «Детали 24.3»). Данные вымышленные."""

import datetime as dt
from decimal import Decimal

from tests.test_plan_grid import env, put  # noqa: F401
from tests.test_users import admin  # noqa: F401

MONTH = dt.date(2027, 3, 1)


def test_fund_from_revenue_indicator(client, env, db):  # noqa: F811
    from app.models import ReportFormRow
    from app.services import method_plan
    from app.services.reports import ReportFilter

    o, h, art = env["o"], env["h"], env["art"]
    fund = art["Выплата дивидендов"]  # статья только ДДС, финансовая выплата — как 2.9 «Фонд учредителей»
    sales = art["Выручка от продаж"]
    for position, code, name, kind, article_id, formula in (
            (1, "1.0", "Выручка со скидками", "indicator", None, "1.1 - 1.2"),
            (2, "1.1", "Выручка без скидок", "article", sales, ""),
            (3, "1.2", "Скидки", "article", art["Прочие доходы"], ""),
            (4, "2.9", "Фонд учредителей", "article", fund, "")):
        db.add(ReportFormRow(organization_id=env["org"].id, position=position, code=code, name=name, kind=kind,
                             article_id=article_id, formula=formula, invert=code == "1.2"))
    db.commit()
    assert client.put(f"/api/articles/{fund}/plan-method", headers=h, json={"plan_method": "calc"}).status_code == 200
    resp = client.post(f"{o}/plan-norms", headers=h, json={"article_id": fund, "kind": "calc", "valid_from": "2027-01-01",
                                                            "base_form_code": "1.0", "percent": "5"})
    assert resp.status_code == 201, resp.text
    assert put(client, env, [{"article_id": sales, "month": 3, "amount": "400000"}], cfo="shop").status_code == 200

    f = ReportFilter(organization_id=env["org"].id, date_from=MONTH, date_to=MONTH)
    rows = {r.row.code: r for r in method_plan.build(db, f, env["version"]["id"], MONTH, today=dt.date(2027, 4, 1))}
    assert rows["1.0"].values["itog"] == Decimal("400000.00")
    assert rows["2.9"].values["calc"] == Decimal("20000.00") and rows["2.9"].method == "calc"
    assert rows["2.9"].note == "5 % от 1.0 (база — ИТОГ 400 000,00)"

    # без плана выручки — база пустая, это видно в пояснении
    other = dt.date(2027, 4, 1)
    rows = {r.row.code: r for r in method_plan.build(db, f, env["version"]["id"], other, today=dt.date(2027, 4, 1))}
    assert rows["2.9"].values["calc"] == Decimal("0.00")
    assert rows["2.9"].note.startswith("5 % от 1.0 (база — ИТОГ 0,00) — база пустая")


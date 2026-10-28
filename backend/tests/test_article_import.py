"""Загрузка справочника статей из Excel с совмещением (docs/TZ.md, раздел 19, «Детали 9.2»).
Файл — по образцу справочника «Малины», названия вымышленные."""

import io

from openpyxl import Workbook

from tests.test_articles import _articles, env  # noqa: F401

ROWS = [
    ("ОПЕРАЦИОННАЯ ДЕЯТЕЛЬНОСТЬ", None, None),
    ("Выручка со скидками", "1.0.", None),
    ("Выручка без скидок", "1.1.", None),
    ("Скидки", "1.2.", None),
    ("Общая сырьевая себестоимость", "2.1.", None),
    ("сырьевая с/с", "2.1.1.", None),
    ("Валовая прибыль (маржа)", "3.1.", None),
    ("Аренда", "2.3.", None),
    ("постоянная часть", "2.3.1.", None),
    ("коммунальные расходы", "2.3.2.", None),
    ("Комиссия Банка", "2.5.", None),
    ("Эквайринг комиссия", "2.5.1.", None),
    ("Представительские расходы", "2.8.", None),
    ("представительские", "2.8.1.", "товары"),
    ("Фонд учредителей", "2.9.", None),
    ("Налог на прибыль", "2.12.", None),
    ("ИНВЕСТИЦИОННАЯ ДЕЯТЕЛЬНОСТЬ", None, None),
    ("Инвестиционный фонд на начало периода", "4.0.", None),
    ("Инвестиционные расходы", "4.3.", None),
    ("Оборудование", "4.3.3.", None),
    ("Инвест кредиты (задолженность на начало)", "5.1.", None),
    ("поступление", "5.1.1.", None),
    ("Денежные средства на начало месяца", "6.1.1.", None),
]


def xlsx(rows=ROWS):
    wb = Workbook()
    ws = wb.active
    ws.append(["Наименование показателя", "Статья КБК", None])
    for row in rows:
        ws.append(list(row))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def preview(client, env, raw=None, h=None):
    resp = client.post(f"{env['o']}/import/articles/preview", headers={**(h or env["h"]),
                       "Content-Type": "application/octet-stream"}, content=raw or xlsx())
    assert resp.status_code == 200, resp.text
    return resp.json()


def load(client, env, rows):
    resp = client.post(f"{env['o']}/import/articles", headers=env["h"], json=rows)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_preview(client, env):
    rows = {r["code"] or r["name"]: r for r in preview(client, env)}
    assert rows["ОПЕРАЦИОННАЯ ДЕЯТЕЛЬНОСТЬ"]["kind"] == "header"
    assert [rows[c]["kind"] for c in ("1.0", "3.1", "4.0", "5.1", "6.1.1")] == ["indicator"] * 5
    assert [rows[c]["kind"] for c in ("1.1", "2.3.1", "4.3.3", "5.1.1")] == ["article"] * 4
    assert (rows["2.3.1"]["parent_code"], rows["1.1"]["parent_code"], rows["5.1.1"]["parent_code"]) == ("2.3", None, None)
    arts = _articles(client, env)
    assert (rows["2.3"]["match_id"], rows["2.3"]["match_reason"]) == (arts["Аренда"]["id"], "по названию")
    assert (rows["1.1"]["match_id"], rows["1.1"]["match_reason"]) == (arts["Выручка от продаж"]["id"], "по соответствию")
    assert rows["4.3.3"]["match_id"] == arts["Инвестиции: оборудование"]["id"]
    assert rows["5.1.1"]["match_id"] == arts["Получение кредитов и займов"]["id"]
    # системная статья не становится группой
    assert rows["2.5"]["match_id"] is None and "системная" in rows["2.5"]["warning"]
    assert rows["2.3.2"]["match_id"] == arts["Коммунальные услуги"]["id"]
    assert rows["2.3"]["flags"] == "ДДС опер. выплата, ОПиУ, Дт 44 — Кт 60.01"
    assert rows["2.1"]["flags"] == "ОПиУ, Дт 90.02 — Кт 41"
    # разбор ничего не меняет
    assert _articles(client, env)["Аренда"]["code"] is None
    # права и формат
    assert client.post(f"{env['o']}/import/articles/preview", headers={**env["ceo"], "Content-Type": "application/octet-stream"},
                       content=xlsx()).status_code == 403
    resp = client.post(f"{env['o']}/import/articles/preview", headers={**env["h"], "Content-Type": "application/octet-stream"},
                       content=b"not excel")
    assert resp.status_code == 422


def test_apply_and_reimport(client, env):
    before = _articles(client, env)
    rows = preview(client, env)
    result = load(client, env, rows)
    assert result["errors"] == []
    assert (result["created"], result["merged"], result["skipped"]) == (9, 7, 7)  # 1.1, 2.1, 2.3, 2.3.2, 2.12, 4.3.3, 5.1.1
    arts = {a["code"]: a for a in _articles(client, env).values() if a["code"]}
    rent = arts["2.3."]
    assert rent["id"] == before["Аренда"]["id"] and rent["has_children"]
    assert (rent["is_cash_flow"], rent["cash_flow_direction"], rent["pl_section"]) == (True, "outflow", "selling")
    assert arts["2.3.2."]["id"] == before["Коммунальные услуги"]["id"] and arts["2.3.2."]["name"] == "коммунальные расходы"
    assert arts["2.3.1."]["parent_id"] == rent["id"] and arts["2.3.1."]["level"] == 2
    assert arts["1.1."]["name"] == "Выручка без скидок" and arts["1.1."]["id"] == before["Выручка от продаж"]["id"]
    assert arts["2.5."]["id"] != before["Банковские комиссии"]["id"]  # не совмещена — новая группа
    equipment = arts["4.3.3."]
    assert (equipment["id"], equipment["cash_flow_activity"], equipment["parent_id"]) == \
        (before["Инвестиции: оборудование"]["id"], "investing", arts["4.3."]["id"])
    accounts = {a["id"]: a["code"] for a in client.get("/api/accounts", headers=env["h"]).json()}
    goods = arts["2.8.1."]
    assert (accounts[goods["debit_account_id"]], accounts[goods["credit_account_id"]]) == ("44", "41")
    founders = arts["2.9."]
    assert (founders["is_pl"], founders["cash_flow_activity"]) == (False, "financing")
    assert arts["2.12."]["pl_section"] == "income_tax"
    assert not any(a["code"] in ("1.0.", "3.1.", "4.0.", "6.1.1.") for a in arts.values())

    # повторная загрузка: всё совмещается по коду, новых статей нет
    count = len(_articles(client, env))
    rows = preview(client, env)
    assert all(r["match_reason"] == "по коду" for r in rows if r["kind"] == "article")
    result = load(client, env, rows)
    assert (result["created"], result["merged"], result["updated"], result["errors"]) == (0, 0, 16, [])
    assert len(_articles(client, env)) == count

    # отказ от совмещения и ошибки строк
    result = load(client, env, [{"row": 2, "code": "7.1.", "name": "Аренда", "kind": "article", "match_id": None}])
    assert result["errors"][0]["error"].startswith("для этого кода нет правила")
    result = load(client, env, [{"row": 2, "code": "2.20.", "name": "Аренда", "kind": "article", "match_id": None}])
    assert "уже есть" in result["errors"][0]["error"]  # «Аренда» на верхнем уровне уже есть


def test_merge_blocked_by_postings(client, env, db):
    """Статья, по которой есть проводки как по статье ДДС, не совмещается со строкой без ДДС."""
    import datetime as dt
    from decimal import Decimal

    from app.models import Account, LegalEntity, Operation, Posting, User

    arts = _articles(client, env)
    raw = xlsx([("Прочие выплаты", "2.1.9.", None)])  # раздел 2.1 — только ОПиУ
    assert preview(client, env, raw)[0]["match_id"] == arts["Прочие выплаты"]["id"]  # проводок нет — совмещается

    le = LegalEntity(organization_id=env["org"].id, full_name="ООО А", short_name="А", inn="7701234567")
    db.add(le)
    db.flush()
    accounts = {a.code: a.id for a in db.query(Account)}
    op = Operation(organization_id=env["org"].id, number=1, legal_entity_id=le.id, op_date=dt.date(2026, 3, 1),
                   status="draft", created_by=db.query(User).filter_by(login="fd").one().id)
    op.postings = [Posting(line_no=1, debit_account_id=accounts["76"], credit_account_id=accounts["51"],
                           amount=Decimal("10"), amount_rub=Decimal("10"), currency_id=1,
                           ct_cash_flow_article_id=arts["Прочие выплаты"]["id"])]
    db.add(op)
    db.commit()
    row = preview(client, env, raw)[0]
    assert row["match_id"] is None and "признаки не совпадают" in row["warning"]

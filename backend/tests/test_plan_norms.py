"""Методы планирования статей и нормативы, шаг 23.1 (docs/TZ.md, раздел 31, «Детали 23.1»). Данные вымышленные."""

from tests.test_closing import env  # noqa: F401


def test_plan_method(client, env):  # noqa: F811
    o, h, art = env["o"], env["h"], env["art"]
    rows = {a["name"]: a for a in client.get(f"{o}/articles", headers=h).json()}
    assert rows["Аренда"]["plan_method"] is None and rows["Аренда"]["effective_plan_method"] == "cfo"

    # метод у статьи; «Средний» — по умолчанию 6 месяцев
    resp = client.put(f"/api/articles/{art['Коммунальные услуги']}/plan-method", headers=h, json={"plan_method": "avg"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["plan_method"], resp.json()["plan_avg_months"]) == ("avg", 6)
    resp = client.put(f"/api/articles/{art['Коммунальные услуги']}/plan-method", headers=h,
                      json={"plan_method": "avg", "plan_avg_months": 3})
    assert resp.json()["plan_avg_months"] == 3
    assert client.put(f"/api/articles/{art['Аренда']}/plan-method", headers=h,
                      json={"plan_method": "avg", "plan_avg_months": 5}).status_code == 422
    assert client.put(f"/api/articles/{art['Аренда']}/plan-method", headers=env["ceo"],
                      json={"plan_method": "fact"}).status_code == 403

    # подстатья без метода берёт метод родителя; правка статьи формой без метода метод не сбрасывает
    client.put(f"/api/articles/{art['Аренда']}/plan-method", headers=h, json={"plan_method": "fact"})
    sub = client.post(f"{o}/articles", headers=h, json={"name": "Аренда склада", "parent_id": art["Аренда"]}).json()
    assert (sub["plan_method"], sub["effective_plan_method"]) == (None, "fact")
    full = rows["Аренда"] | {"name": "Аренда помещений"}
    for key in ("plan_method", "plan_avg_months", "id", "organization_id", "system_key", "has_children", "level",
                "effective_plan_method"):
        full.pop(key)
    assert client.put(f"/api/articles/{art['Аренда']}", headers=h, json=full).json()["plan_method"] == "fact"


def test_norms(client, env):  # noqa: F811
    o, h, art = env["o"], env["h"], env["art"]
    tax = art["Страховые взносы с оплаты труда"]
    wages = art["Оплата труда — управленческий персонал"]
    body = {"article_id": tax, "kind": "calc", "valid_from": "2026-01-15", "base_article_id": wages,
            "percent": "30.2"}
    assert client.post(f"{o}/plan-norms", headers=env["ceo"], json=body).status_code == 403
    resp = client.post(f"{o}/plan-norms", headers=h, json=body)
    assert resp.status_code == 201, resp.text
    norm = resp.json()
    assert (norm["valid_from"], norm["percent"], norm["base_title"]) == \
        ("2026-01-01", "30.2000", "Оплата труда — управленческий персонал")
    assert client.post(f"{o}/plan-norms", headers=h, json=body).status_code == 409
    assert client.post(f"{o}/plan-norms", headers=h, json=body | {"valid_from": "2026-02-01", "percent": None}) \
        .status_code == 422
    assert client.post(f"{o}/plan-norms", headers=h, json=body | {"valid_from": "2026-02-01",
                                                                   "base_article_id": tax}).status_code == 422
    # база — строка управленческой формы: формы нет — ошибка
    resp = client.post(f"{o}/plan-norms", headers=h, json=body | {"valid_from": "2026-02-01", "base_article_id": None,
                                                                    "base_form_code": "1.0"})
    assert resp.status_code == 422 and "1.0" in resp.json()["detail"]

    # фактический: сумма вручную на ЦФО
    rent = {"article_id": art["Аренда"], "kind": "fact", "valid_from": "2026-12-01", "amount": "50000",
            "cfo_id": env["office"]["id"]}
    resp = client.post(f"{o}/plan-norms", headers=h, json=rent)
    assert resp.status_code == 201, resp.text
    assert (resp.json()["cfo_name"], resp.json()["amount"], resp.json()["percent"]) == ("Офис", "50000.00", None)
    assert client.post(f"{o}/plan-norms", headers=h, json=rent | {"amount": None, "valid_from": "2027-01-01"}) \
        .status_code == 422
    resp = client.put(f"/api/plan-norms/{resp.json()['id']}", headers=h, json=rent | {"amount": "55000"})
    assert resp.json()["amount"] == "55000.00"

    listed = client.get(f"{o}/plan-norms", headers=env["ceo"]).json()  # смотрят все с просмотром
    assert [n["kind"] for n in listed] == ["calc", "fact"]
    assert client.get(f"{o}/plan-norms", headers=env["other"]).status_code == 404
    assert client.delete(f"/api/plan-norms/{norm['id']}", headers=env["ceo"]).status_code == 403
    assert client.delete(f"/api/plan-norms/{norm['id']}", headers=h).status_code == 204
    assert len(client.get(f"{o}/plan-norms", headers=h).json()) == 1


def test_norms_on(client, env, db):  # noqa: F811
    import datetime as dt

    from app.services.plan_norms import norms_on

    o, h, art = env["o"], env["h"], env["art"]
    for month, amount in (("2026-01-01", "100"), ("2026-06-01", "200")):
        client.post(f"{o}/plan-norms", headers=h, json={"article_id": art["Аренда"], "kind": "fact",
                                                        "valid_from": month, "amount": amount})
    assert norms_on(db, env["org"].id, "fact", dt.date(2025, 12, 1)) == []
    assert [str(n.amount) for n in norms_on(db, env["org"].id, "fact", dt.date(2026, 5, 10))] == ["100.00"]
    assert [str(n.amount) for n in norms_on(db, env["org"].id, "fact", dt.date(2026, 7, 1))] == ["200.00"]

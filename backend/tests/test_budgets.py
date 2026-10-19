"""Бюджеты на год / квартал / месяц и их версии (docs/TZ.md, раздел 13)."""

import pytest


@pytest.fixture
def env(client, headers_for, make_user, make_org):
    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    other = make_user(login="other")
    org = make_org("Группа А", members=[(fd, "finance_director"), (ceo, "ceo")])
    make_org("Клиент Б", kind="client", members=[(other, "finance_director")])
    return {"o": f"/api/organizations/{org.id}", "h": headers_for(fd), "ceo": headers_for(ceo),
            "other": headers_for(other)}


def create(client, env, h=None, **body):
    return client.post(f"{env['o']}/budgets", headers=h or env["h"], json=body)


def test_budgets_for_year_quarter_month(client, env):
    year = create(client, env, kind="year", year=2027)
    assert year.status_code == 201, year.text
    year = year.json()
    assert (year["title"], year["months"]) == ("Бюджет 2027", list(range(1, 13)))
    assert [(v["number"], v["name"], v["status"]) for v in year["versions"]] == [(1, "Версия 1", "draft")]
    q2 = create(client, env, kind="quarter", year=2027, period=2).json()
    assert (q2["title"], q2["months"]) == ("Бюджет II кв. 2027", [4, 5, 6])
    jan = create(client, env, kind="month", year=2027, period=1).json()
    assert (jan["title"], jan["months"]) == ("Бюджет январь 2027", [1])

    assert create(client, env, kind="year", year=2027).status_code == 409  # один бюджет на период
    assert create(client, env, kind="quarter", year=2027, period=5).status_code == 422
    assert create(client, env, kind="month", year=2027, period=13).status_code == 422

    listed = client.get(f"{env['o']}/budgets", headers=env["h"], params={"year": 2027}).json()
    assert [b["title"] for b in listed] == ["Бюджет 2027", "Бюджет II кв. 2027", "Бюджет январь 2027"]


def test_versions_lifecycle(client, env):
    budget = create(client, env, kind="year", year=2027).json()
    v1 = budget["versions"][0]
    approve = client.post(f"/api/budget-versions/{v1['id']}/approve", headers=env["h"])
    assert approve.status_code == 200 and approve.json()["status"] == "approved"
    assert approve.json()["approved_at"] is not None

    # ежемесячный пересмотр: копия утверждённой → черновик → утверждение, прежняя — в архив
    v2 = client.post(f"/api/budgets/{budget['id']}/versions", headers=env["h"],
                     json={"name": "Пересмотр февраля", "copy_from_id": v1["id"]})
    assert v2.status_code == 201
    v2 = v2.json()
    assert (v2["number"], v2["name"], v2["status"], v2["based_on_id"]) == (2, "Пересмотр февраля", "draft", v1["id"])
    assert client.post(f"/api/budget-versions/{v2['id']}/approve", headers=env["h"]).status_code == 200
    statuses = {v["id"]: v["status"] for v in client.get(f"{env['o']}/budgets", headers=env["h"]).json()[0]["versions"]}
    assert statuses == {v1["id"]: "archived", v2["id"]: "approved"}

    # утверждённую и архивную нельзя удалить и повторно утвердить
    assert client.delete(f"/api/budget-versions/{v2['id']}", headers=env["h"]).status_code == 422
    assert client.post(f"/api/budget-versions/{v1['id']}/approve", headers=env["h"]).status_code == 422
    assert client.delete(f"/api/budgets/{budget['id']}", headers=env["h"]).status_code == 409

    v3 = client.post(f"/api/budgets/{budget['id']}/versions", headers=env["h"], json={}).json()
    assert v3["name"] == "Версия 3"
    renamed = client.patch(f"/api/budget-versions/{v3['id']}", headers=env["h"], json={"name": "Оптимистичный"})
    assert renamed.json()["name"] == "Оптимистичный"
    assert client.delete(f"/api/budget-versions/{v3['id']}", headers=env["h"]).status_code == 204
    assert client.post(f"/api/budget-versions/{v2['id']}/archive", headers=env["h"]).json()["status"] == "archived"

    # копировать можно только версию этого же бюджета
    other = create(client, env, kind="month", year=2027, period=3).json()
    resp = client.post(f"/api/budgets/{other['id']}/versions", headers=env["h"], json={"copy_from_id": v1["id"]})
    assert resp.status_code == 404


def test_draft_only_budget_can_be_deleted(client, env):
    budget = create(client, env, kind="month", year=2027, period=2).json()
    assert client.delete(f"/api/budgets/{budget['id']}", headers=env["h"]).status_code == 204
    assert client.get(f"{env['o']}/budgets", headers=env["h"]).json() == []


def test_budget_permissions(client, env):
    budget = create(client, env, kind="year", year=2027).json()
    version = budget["versions"][0]
    assert len(client.get(f"{env['o']}/budgets", headers=env["ceo"]).json()) == 1  # CEO смотрит
    assert create(client, env, h=env["ceo"], kind="year", year=2028).status_code == 403
    assert client.post(f"/api/budget-versions/{version['id']}/approve", headers=env["ceo"]).status_code == 403
    assert client.post(f"/api/budgets/{budget['id']}/versions", headers=env["ceo"], json={}).status_code == 403
    # чужая организация
    assert client.get(f"{env['o']}/budgets", headers=env["other"]).status_code == 404
    assert client.post(f"/api/budget-versions/{version['id']}/approve", headers=env["other"]).status_code == 404
    assert client.delete(f"/api/budgets/{budget['id']}", headers=env["other"]).status_code == 404

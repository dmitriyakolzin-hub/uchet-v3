"""ЦФУ (исполнители): справочник, ЦФУ в проводках и плане, план по ЦФУ, план/факт (docs/TZ.md, раздел 16)."""

import pytest

from tests.test_plan_grid import env, grid, put  # noqa: F401
from tests.test_users import admin  # noqa: F401


@pytest.fixture
def ex(client, env):  # noqa: F811
    def post(body, h=None):
        return client.post(f"{env['o']}/executors", headers=h or env["h"], json=body)

    dept = post({"name": "Инженерный отдел"}).json()
    engineer = post({"name": "Инженер Петров", "parent_id": dept["id"]}).json()
    env.update(dept=dept, engineer=engineer, post_executor=post)
    return env


def test_executor_reference(client, ex):
    env = ex
    resp = env["post_executor"]({"name": "Третий уровень", "parent_id": env["engineer"]["id"]})
    assert resp.status_code == 422 and "два уровня" in resp.json()["detail"]
    resp = client.patch(f"/api/executors/{env['dept']['id']}", headers=env["h"], json={"parent_id": env["engineer"]["id"]})
    assert resp.status_code == 422
    assert env["post_executor"]({"name": "X"}, h=env["ceo"]).status_code == 403
    assert {e["name"] for e in client.get(f"{env['o']}/executors", headers=env["ceo"]).json()} == {
        "Инженер Петров", "Инженерный отдел"}


def test_executor_in_journal(client, ex, db):
    env = ex
    from app.models import Counterparty, Currency

    acc = {v: k for k, v in env["acc"].items()}
    rub = db.query(Currency).filter_by(code="RUB").one()
    cp = client.post(f"{env['o']}/counterparties", headers=env["h"], json={"name": "Подрядчик", "kind": "legal",
                                                                             "inn": "7702222222"}).json()
    contract = client.post(f"{env['o']}/contracts", headers=env["h"], json={
        "counterparty_id": cp["id"], "legal_entity_id": env["le"].id, "currency_id": rub.id, "kind": "supplier",
        "number": "1"}).json()
    posting = {"debit_account_id": acc["26"], "credit_account_id": acc["60.01"], "amount": "7000",
               "debit": {"cfo": env["shop"]["id"], "pl_item": env["art"]["Аренда"], "executor": env["engineer"]["id"]},
               "credit": {"counterparty": cp["id"], "contract": contract["id"]}}
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2027-01-15", "postings": [posting]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["postings"][0]["debit"]["executor"] == env["engineer"]["id"]
    # ЦФУ — только там, где ведётся ЦФО
    bad = {**posting, "credit": {**posting["credit"], "executor": env["engineer"]["id"]}}
    resp = client.post(f"{env['o']}/operations?post=true", headers=env["h"], json={
        "legal_entity_id": env["le"].id, "op_date": "2027-01-15", "postings": [bad]})
    assert resp.status_code == 422 and "ЦФУ" in resp.text
    assert db.query(Counterparty).count() == 1

    # план ЦФУ и план/факт по отделу (отдел = отдел + исполнители)
    rent = env["art"]["Аренда"]
    resp = client.put(f"/api/budget-versions/{env['version']['id']}/cells", headers=env["h"], json={
        "kind": "pl", "cfo_id": env["shop"]["id"], "executor_id": env["engineer"]["id"],
        "cells": [{"article_id": rent, "month": 1, "amount": "10000"}]})
    assert resp.status_code == 200, resp.text
    put(client, env, [{"article_id": rent, "month": 1, "amount": "500"}], cfo="shop")  # без ЦФУ

    rows = [r for r in grid(client, env).json()["rows"] if r["article_id"] == rent]
    assert [(r["executor_name"], r["values"]) for r in rows] == [
        ("", {"2027-01": "500.00"}), ("Инженерный отдел / Инженер Петров", {"2027-01": "10000.00"})]

    data = client.get(f"/api/budget-versions/{env['version']['id']}/grid", headers=env["h"],
                      params={"mode": "executor", "executor_id": env["dept"]["id"]}).json()
    assert (data["mode"], data["editable"]) == ("executor", False)
    row = next(r for r in data["rows"] if r["article_id"] == rent)
    assert row["values"] == {"2027-01": "10000.00"}
    assert [(p["name"], p["values"]) for p in row["parts"]] == [
        ("Компания / Торговля / Магазин 1", {"2027-01": "10000.00"})]

    client.post(f"/api/budget-versions/{env['version']['id']}/approve", headers=env["h"])
    pf = client.get(f"{env['o']}/reports/plan-fact", headers=env["h"], params={
        "date_from": "2027-01-01", "date_to": "2027-01-31", "kind": "pl", "executor_id": env["dept"]["id"]}).json()
    rent_row = next(r for r in pf["rows"] if r.get("key") == f"art:{rent}")
    assert (rent_row["values"]["total"]["plan"], rent_row["values"]["total"]["fact"]) == ("-10000.00", "-7000.00")

    # новая версия «на основе» сохраняет ЦФУ
    copy = client.post(f"/api/budgets/{env['budget']['id']}/versions", headers=env["h"],
                       json={"name": "Копия", "copy_from_id": env["version"]["id"]}).json()
    rows = [r for r in grid(client, env, version=copy).json()["rows"] if r["article_id"] == rent and r["executor_id"]]
    assert rows[0]["values"] == {"2027-01": "10000.00"}

    other = client.get(f"{env['o']}/reports/pl", headers=env["h"], params={
        "date_from": "2027-01-01", "date_to": "2027-01-31", "executor_id": 999999})
    assert other.status_code == 404

import pytest


@pytest.fixture
def admin(make_user):
    return make_user(login="admin", is_admin=True)


@pytest.fixture
def people(make_user, make_org):
    fd = make_user(login="fd")
    ceo = make_user(login="ceo")
    make_org(members=[(fd, "finance_director"), (ceo, "ceo")])
    return {"fd": fd, "ceo": ceo, "nobody": make_user(login="nobody")}


@pytest.fixture
def usd(client, headers_for, admin):
    resp = client.post("/api/currencies", headers=headers_for(admin),
                       json={"code": " usd ", "numeric_code": "840", "name": "Доллар США"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rub(client, h):
    return next(c for c in client.get("/api/currencies", headers=h).json() if c["code"] == "RUB")


def test_rub_exists_from_start(client, headers_for, people):
    currencies = client.get("/api/currencies", headers=headers_for(people["nobody"])).json()
    assert [(c["code"], c["is_base"]) for c in currencies] == [("RUB", True)]


def test_admin_adds_currency(client, headers_for, admin, usd):
    assert usd["code"] == "USD"
    codes = [c["code"] for c in client.get("/api/currencies", headers=headers_for(admin)).json()]
    assert codes == ["RUB", "USD"]


@pytest.mark.parametrize("who", ["fd", "ceo", "nobody"])
def test_only_admin_adds_currency(client, headers_for, people, who):
    resp = client.post("/api/currencies", headers=headers_for(people[who]),
                       json={"code": "EUR", "numeric_code": "978", "name": "Евро"})
    assert resp.status_code == 403


@pytest.mark.parametrize("body", [
    {"code": "US", "numeric_code": "840", "name": "x"},
    {"code": "US1", "numeric_code": "840", "name": "x"},
    {"code": "EUR", "numeric_code": "97", "name": "x"},
    {"code": "EUR", "numeric_code": "978", "name": " "},
])
def test_currency_validation(client, headers_for, admin, body):
    assert client.post("/api/currencies", headers=headers_for(admin), json=body).status_code == 422


def test_duplicate_currency(client, headers_for, admin, usd):
    h = headers_for(admin)
    assert client.post("/api/currencies", headers=h,
                       json={"code": "USD", "numeric_code": "999", "name": "x"}).status_code == 409
    assert client.post("/api/currencies", headers=h,
                       json={"code": "XXX", "numeric_code": "840", "name": "x"}).status_code == 409


def test_base_currency_cannot_be_disabled(client, headers_for, admin):
    h = headers_for(admin)
    rub = _rub(client, h)
    assert client.patch(f"/api/currencies/{rub['id']}", headers=h,
                        json={"is_active": False}).status_code == 409


def test_no_rates_for_rub(client, headers_for, admin):
    h = headers_for(admin)
    rub = _rub(client, h)
    resp = client.post(f"/api/currencies/{rub['id']}/rates", headers=h,
                       json={"rate_date": "2026-10-01", "rate": "1"})
    assert resp.status_code == 422
    eff = client.get(f"/api/currencies/{rub['id']}/rate?on=2026-10-01", headers=h).json()
    assert eff["rate"] == "1" and eff["nominal"] == 1


def test_fd_enters_and_fixes_rates(client, headers_for, people, usd):
    h = headers_for(people["fd"])
    url = f"/api/currencies/{usd['id']}/rates"
    resp = client.post(url, headers=h, json={"rate_date": "2026-10-01", "rate": "81.5432"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["rate"] == "81.543200"  # точное десятичное, не float

    # второй курс на ту же дату — конфликт, правим существующий
    assert client.post(url, headers=h, json={"rate_date": "2026-10-01", "rate": "1"}).status_code == 409
    rate_id = resp.json()["id"]
    fixed = client.put(f"/api/exchange-rates/{rate_id}", headers=h,
                       json={"rate_date": "2026-10-01", "rate": "81.6"})
    assert fixed.status_code == 200 and fixed.json()["rate"] == "81.600000"


@pytest.mark.parametrize("who", ["ceo", "nobody"])
def test_viewers_cannot_change_rates(client, headers_for, admin, people, usd, who):
    url = f"/api/currencies/{usd['id']}/rates"
    created = client.post(url, headers=headers_for(admin), json={"rate_date": "2026-10-01", "rate": "80"}).json()
    h = headers_for(people[who])
    assert client.get(url, headers=h).status_code == 200
    assert client.post(url, headers=h, json={"rate_date": "2026-10-02", "rate": "80"}).status_code == 403
    assert client.put(f"/api/exchange-rates/{created['id']}", headers=h,
                      json={"rate_date": "2026-10-01", "rate": "1"}).status_code == 403
    assert client.delete(f"/api/exchange-rates/{created['id']}", headers=h).status_code == 403


@pytest.mark.parametrize("rate", ["0", "-1", "1.1234567", "1e2x", 81.5])
def test_rate_validation(client, headers_for, admin, usd, rate):
    resp = client.post(f"/api/currencies/{usd['id']}/rates", headers=headers_for(admin),
                       json={"rate_date": "2026-10-01", "rate": rate})
    if rate == 81.5:
        # число в JSON допустимо, но хранится точно: 81.5 → 81.500000
        assert resp.status_code == 201 and resp.json()["rate"] == "81.500000"
    else:
        assert resp.status_code == 422


def test_effective_rate_is_last_on_or_before_date(client, headers_for, admin, usd):
    h = headers_for(admin)
    url = f"/api/currencies/{usd['id']}/rates"
    client.post(url, headers=h, json={"rate_date": "2026-10-01", "rate": "80"})
    client.post(url, headers=h, json={"rate_date": "2026-10-05", "rate": "82", "nominal": 1})

    def on(day):
        return client.get(f"/api/currencies/{usd['id']}/rate?on={day}", headers=h)

    assert on("2026-09-30").status_code == 404
    assert "Нет курса USD на 30.09.2026" in on("2026-09-30").json()["detail"]
    assert on("2026-10-01").json()["rate"] == "80.000000"
    assert on("2026-10-04").json()["rate_date"] == "2026-10-01"
    assert on("2026-10-05").json()["rate"] == "82.000000"
    assert on("2026-12-31").json()["rate_date"] == "2026-10-05"


def test_delete_rate(client, headers_for, admin, usd):
    h = headers_for(admin)
    created = client.post(f"/api/currencies/{usd['id']}/rates", headers=h,
                          json={"rate_date": "2026-10-01", "rate": "80"}).json()
    assert client.delete(f"/api/exchange-rates/{created['id']}", headers=h).status_code == 204
    assert client.get(f"/api/currencies/{usd['id']}/rates", headers=h).json() == []

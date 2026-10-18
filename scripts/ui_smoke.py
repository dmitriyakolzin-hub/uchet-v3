"""Проверка интерфейса в браузере (для разработки): вход, организации, юрлица, пользователи, права CEO.

Запуск на пустой базе с администратором admin / admin-pass-123 и приложением на 127.0.0.1:8011:
    .venv/bin/python scripts/ui_smoke.py <папка для скриншотов>
Нужен пакет playwright и Chromium (в облачной среде: /opt/pw-browsers/chromium).
"""
import os
import sys
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get("UI_BASE", "http://127.0.0.1:8011")
OUT = sys.argv[1]
errors = []

def login(page, user, pwd):
    page.goto(BASE + "/")
    page.fill("input[name=login]", user)
    page.fill("input[name=password]", pwd)
    page.click("#login-form button")
    expect(page.locator("#app-view")).to_be_visible()

def dialog_fill(page, values):
    d = page.locator("dialog[open]")
    for name, value in values.items():
        loc = d.locator(f"[name={name}]")
        if loc.evaluate("e => e.tagName") == "SELECT":
            loc.select_option(value)
        else:
            loc.fill(value)
    return d

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium"))
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))

    login(page, "admin", "admin-pass-123")
    expect(page.locator("#nav a")).to_have_count(3)

    # организация
    page.click("text=Добавить организацию")
    d = dialog_fill(page, {"name": "Группа АВМВ", "kind": "group", "note": "Основная группа"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Группа АВМВ")).to_be_visible()
    page.click("text=Добавить организацию")
    d = dialog_fill(page, {"name": "Клиент <b>Икс</b>", "kind": "client"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Клиент <b>Икс</b>")).to_be_visible()

    # юрлицо: сначала с ошибкой ИНН, потом правильно
    page.locator(".tabulator-row", has_text="Группа АВМВ").locator(".tabulator-cell").first.click()
    expect(page.locator("h2", has_text="Юрлица: Группа АВМВ")).to_be_visible()
    page.click("text=Добавить юрлицо")
    d = dialog_fill(page, {"full_name": "ООО «Ромашка»", "short_name": "Ромашка", "inn": "123"})
    d.locator("button[type=submit]").click()
    expect(d.locator(".error")).to_contain_text("inn")
    dialog_fill(page, {"inn": "7701234567", "kpp": "770101001"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    page.screenshot(path=f"{OUT}/1_organizations.png", full_page=True)

    # пользователь CEO
    page.click("#nav a[data-page=users]")
    page.click("text=Добавить пользователя")
    d = dialog_fill(page, {"login": "ceo", "full_name": "Сидоров С.С.", "password": "ceo-password-1"})
    d.locator("select[aria-label='Роль в Группа АВМВ']").select_option("ceo")
    expect(d).not_to_contain_text("null")
    page.screenshot(path=f"{OUT}/2_user_form.png")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Группа АВМВ: CEO")).to_be_visible()
    page.screenshot(path=f"{OUT}/3_users.png", full_page=True)

    # вход под CEO
    page.click("#logout")
    login(page, "ceo", "ceo-password-1")
    expect(page.locator("#nav a")).to_have_count(2)  # нет «Пользователи»
    expect(page.locator("text=Добавить организацию")).to_have_count(0)
    expect(page.locator(".tabulator").first.locator(".tabulator-row")).to_have_count(1)  # видит только свою
    expect(page.locator("h2", has_text="Юрлица: Группа АВМВ")).to_be_visible()  # выбрана автоматически
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator("text=Добавить юрлицо")).to_be_hidden()
    page.goto(BASE + "/#/users")  # прямой переход в чужой раздел
    expect(page.locator("#nav a.active")).to_have_text("Организации")
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]", has_text="✎")).to_have_count(0)
    page.screenshot(path=f"{OUT}/4_ceo.png", full_page=True)

    # смена пароля
    page.click("#nav a[data-page=password]")
    page.fill("#cur", "ceo-password-1"); page.fill("#new1", "ceo-password-2"); page.fill("#new2", "ceo-password-2")
    page.click("text=Сменить пароль >> nth=-1")
    expect(page.locator(".toast")).to_have_text("Пароль изменён")
    page.reload()
    expect(page.locator("#app-view")).to_be_visible()  # остаётся в системе с новыми токенами
    browser.close()

print("ОШИБКИ В КОНСОЛИ:", errors or "нет")
print("UI-ПРОВЕРКА ПРОЙДЕНА")

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
    expect(page.locator("#nav a")).to_have_count(13)
    expect(page.locator("#nav a.active")).to_have_text("Журнал операций")  # открывается первым
    page.click("#nav a[data-page=organizations]")

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

    # план счетов: дерево, добавление субсчёта, смена аналитики
    page.click("#nav a[data-page=accounts]")
    row60 = page.locator(".tabulator-row", has_text="Расчёты с поставщиками и подрядчиками").first
    expect(row60).to_contain_text("Контрагент, Договор")
    expect(page.locator(".tabulator-row", has_text="Авансы выданные")).to_have_count(0)  # свёрнуто
    row60.locator(".tabulator-data-tree-control").click()
    expect(page.locator(".tabulator-row", has_text="Авансы выданные")).to_be_visible()
    page.click("text=Добавить счёт или субсчёт")
    d = dialog_fill(page, {"code": "76.05", "name": "Расчёты с арендодателями"})
    d.locator("button[type=submit]").click()
    row76 = page.locator(".tabulator-row", has_text="Расчёты с разными дебиторами").first
    row76.locator(".tabulator-data-tree-control").click()
    sub = page.locator(".tabulator-row", has_text="Расчёты с арендодателями")
    expect(sub).to_contain_text("Контрагент, Договор")  # унаследовал от 76
    sub.locator(".tabulator-cell[tabulator-field=_edit]").click()
    d = page.locator("dialog[open]")
    d.locator("label.check", has_text="ЦФО").locator("input").check()
    d.locator("button[type=submit]").click()
    expect(sub).to_contain_text("Контрагент, Договор, ЦФО")
    page.screenshot(path=f"{OUT}/6_accounts.png", full_page=True)

    # статьи: стартовый набор, фильтр ДДС/ОПиУ, новая статья с шаблоном
    page.click("#nav a[data-page=articles]")
    row = page.locator(".tabulator-row", has_text="Оплата поставщикам")
    expect(row).to_contain_text("Операционная · Выплата")
    expect(row).to_contain_text("Дт 60.01 — Кт 51")
    page.click("button.tab:has-text('ОПиУ')")
    expect(page.locator(".tabulator-row", has_text="Оплата поставщикам")).to_have_count(0)
    expect(page.locator(".tabulator-row", has_text="Выручка от продаж")).to_be_visible()
    page.click("text=Добавить статью")
    d = dialog_fill(page, {"name": "Охрана"})
    d.locator("[name=cash_flow_activity]").select_option(label="Операционная")
    d.locator("[name=cash_flow_direction]").select_option(label="Выплата")
    d.locator("[name=debit_account_id]").select_option(label="60.01 Расчёты с поставщиками и подрядчиками")
    d.locator("[name=credit_account_id]").select_option(label="51 Расчётные счета")
    d.locator("button[type=submit]").click()
    page.click("button.tab:has-text('ДДС')")
    expect(page.locator(".tabulator-row", has_text="Охрана")).to_contain_text("Дт 60.01 — Кт 51")
    page.screenshot(path=f"{OUT}/9_articles.png", full_page=True)

    # валюта и курс (с запятой, как привычно)
    page.click("#nav a[data-page=currencies]")
    expect(page.locator(".tabulator-row", has_text="Российский рубль")).to_be_visible()
    page.click("text=Добавить валюту")
    d = dialog_fill(page, {"code": "usd", "numeric_code": "840", "name": "Доллар США"})
    d.locator("button[type=submit]").click()
    expect(page.locator("h2", has_text="Курсы: USD")).to_be_visible()
    page.click("text=Добавить курс")
    d = dialog_fill(page, {"rate_date": "2026-10-01", "rate": "81,5432"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="81,5432")).to_be_visible()
    expect(page.locator(".tabulator-row", has_text="01.10.2026")).to_be_visible()
    page.screenshot(path=f"{OUT}/5_currencies.png", full_page=True)
    page.click("#nav a[data-page=organizations]")

    # банк (общий справочник) и справочники организации
    page.click("#nav a[data-page=common]")
    page.click("button.tab:has-text('Единицы измерения')")
    expect(page.locator(".tabulator-row", has_text="Штука")).to_be_visible()
    page.click("button.tab:has-text('Банки')")
    page.click("text=Добавить банк")
    d = dialog_fill(page, {"bic": "044525225", "name": "ПАО Сбербанк", "corr_account": "30101810400000000225"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="ПАО Сбербанк")).to_be_visible()

    page.click("#nav a[data-page=references]")
    page.locator(".inline-select").select_option(label="Группа АВМВ")
    page.click("button.tab:has-text('Расчётные счета')")
    page.click("text=Добавить расчётный счёт")
    d = page.locator("dialog[open]")
    d.locator("[name=legal_entity_id]").select_option(label="Ромашка")
    d.locator("[name=bank_id]").select_option(label="ПАО Сбербанк (044525225)")
    dialog_fill(page, {"name": "Сбер, основной", "number": "40702810900000012345"})
    d.locator("button[type=submit]").click()
    row = page.locator(".tabulator-row", has_text="Сбер, основной")
    expect(row).to_contain_text("ПАО Сбербанк")
    expect(row).to_contain_text("RUB")  # валюта по умолчанию — рубль

    page.click("button.tab:has-text('Контрагенты')")
    page.click("text=Добавить контрагента")
    d = dialog_fill(page, {"name": "Поставщик Плюс", "inn": "770111111"})  # 9 цифр — ошибка
    d.locator("label.check", has_text="Поставщик").locator("input").check()
    d.locator("button[type=submit]").click()
    expect(d.locator(".error")).not_to_be_empty()
    dialog_fill(page, {"inn": "7701111111"})
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Поставщик Плюс")).to_contain_text("7701111111")

    page.click("button.tab:has-text('Договоры')")
    page.click("text=Добавить договор")
    d = page.locator("dialog[open]")
    d.locator("[name=counterparty_id]").select_option(label="Поставщик Плюс (ИНН 7701111111)")
    d.locator("[name=legal_entity_id]").select_option(label="Ромашка")
    dialog_fill(page, {"number": "15/2026", "contract_date": "2026-01-15"})
    d.locator("button[type=submit]").click()
    row = page.locator(".tabulator-row", has_text="15/2026")
    expect(row).to_contain_text("Поставщик Плюс")
    expect(row).to_contain_text("15.01.2026")
    page.screenshot(path=f"{OUT}/7_contracts.png", full_page=True)

    # дерево ЦФО: компания → направление → филиал, затем сотрудник в филиале
    page.click("button.tab:has-text('ЦФО')")
    page.click("text=Добавить ЦФО уровня «Компания»")
    d = dialog_fill(page, {"name": "АВМВ"})
    d.locator("[name=legal_entity_id]").select_option(label="Ромашка")
    d.locator("button[type=submit]").click()
    page.locator(".tabulator-row", has_text="АВМВ").locator(".tabulator-cell[tabulator-field=_add]").click()
    d = dialog_fill(page, {"name": "Торговля"})
    expect(d.locator("[name=parent_id] option:checked")).to_have_text("АВМВ")  # родитель подставлен
    d.locator("button[type=submit]").click()
    page.locator(".tabulator-row", has_text="Торговля").locator(".tabulator-cell[tabulator-field=_add]").click()
    d = dialog_fill(page, {"name": "Филиал Юг"})
    d.locator("button[type=submit]").click()
    row = page.locator(".tabulator-row", has_text="Филиал Юг")
    expect(row).to_contain_text("Филиал")
    expect(row.locator(".tabulator-cell[tabulator-field=_add]")).to_have_text("+ магазин")
    page.screenshot(path=f"{OUT}/8_cfos.png", full_page=True)

    page.click("button.tab:has-text('Склады')")
    page.click("text=Добавить склад")
    d = dialog_fill(page, {"name": "Склад Юг"})
    d.locator("[name=cfo_id]").select_option(label="АВМВ / Торговля / Филиал Юг")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Склад Юг")).to_contain_text("Филиал Юг")

    page.click("button.tab:has-text('Номенклатура')")
    page.click("text=Добавить номенклатуру")
    d = dialog_fill(page, {"name": "Кофе зерновой", "article": "CF-001"})
    expect(d.locator("[name=unit_id] option:checked")).to_have_text("шт — Штука")  # по умолчанию
    d.locator("[name=unit_id]").select_option(label="кг — Килограмм")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Кофе зерновой")).to_contain_text("кг")

    page.click("button.tab:has-text('Сотрудники')")
    page.click("text=Добавить сотрудника")
    d = dialog_fill(page, {"full_name": "Иванов Иван", "personnel_number": "0001", "position": "Продавец",
                           "hired_on": "2026-01-10"})
    d.locator("[name=cfo_id]").select_option(label="АВМВ / Торговля / Филиал Юг")
    d.locator("button[type=submit]").click()
    expect(page.locator(".tabulator-row", has_text="Иванов Иван")).to_contain_text("АВМВ / Торговля / Филиал Юг")

    # журнал: операция по статье с шаблоном, проведение, неполный черновик, массовое проведение
    page.click("#nav a[data-page=journal]")
    page.locator(".inline-select").select_option(label="Группа АВМВ")
    page.click("text=Новая операция")
    d = page.locator("dialog[open]")
    d.locator("[name=article_id]").select_option(label="Оплата поставщикам")
    expect(d.locator("[name=debit_account] option:checked")).to_have_text("60.01 Расчёты с поставщиками и подрядчиками")
    expect(d.locator("[name=credit_account] option:checked")).to_have_text("51 Расчётные счета")
    d.locator("[name=amount]").fill("1 500,50")
    d.locator("[aria-label='Контрагент (дебет)']").select_option(label="Поставщик Плюс (7701111111)")
    d.locator("[aria-label='Договор (дебет)']").select_option(index=1)
    d.locator("[aria-label='Расчётный счёт (кредит)']").select_option(index=1)
    expect(d.locator("[aria-label='Статья ДДС (кредит)'] option:checked")).to_have_text("Оплата поставщикам")
    d.locator("[name=description]").fill("Оплата по счёту 15")
    page.screenshot(path=f"{OUT}/10_operation_form.png", full_page=True)
    d.locator("button", has_text="Сохранить и провести").click()
    row = page.locator(".tabulator-row", has_text="Оплата по счёту 15")
    expect(row).to_contain_text("Проведена")
    expect(row).to_contain_text("1 500,50")
    expect(row).to_contain_text("60.01 → 51")

    page.click("text=Новая операция")
    d = page.locator("dialog[open]")
    d.locator("[name=debit_account]").select_option(label="60.01 Расчёты с поставщиками и подрядчиками")
    d.locator("[name=description]").fill("Неполный черновик")
    d.locator("button", has_text="Сохранить черновик").click()
    expect(page.locator(".tabulator-row", has_text="Неполный черновик")).to_contain_text("Черновик")

    page.locator(".tabulator-row", has_text="Неполный черновик").locator("input[type=checkbox]").check()
    page.locator(".bulk-bar button", has_text="Провести").click()
    bulk_dialog = page.locator("dialog[open]")
    expect(bulk_dialog).to_contain_text("выполнено 0, не выполнено 1")
    expect(bulk_dialog).to_contain_text("не указана сумма")
    page.screenshot(path=f"{OUT}/11_bulk_errors.png", full_page=True)
    bulk_dialog.locator("button", has_text="Понятно").click()

    page.locator(".tabulator-row", has_text="Оплата по счёту 15").locator(".tabulator-cell[tabulator-field=description]").click()
    d = page.locator("dialog[open]")
    expect(d.locator("h2")).to_contain_text("Проведена")
    expect(d.locator("[name=amount]")).to_be_disabled()
    d.locator("button", has_text="История").click()
    expect(d.locator(".history")).to_contain_text("провёл(а)")
    d.locator("button", has_text="Закрыть").click()
    expect(page.locator(".bulk-bar")).to_be_hidden()  # открытие операции не отмечает строку
    page.screenshot(path=f"{OUT}/12_journal.png", full_page=True)

    # начальные остатки: дата, расхождение, затем сходится
    page.click("#nav a[data-page=opening]")
    page.locator(".inline-select").select_option(label="Группа АВМВ")
    page.locator("[aria-label='Дата начальных остатков']").fill("2026-09-30")
    page.click("text=Сохранить дату")
    expect(page.locator(".check-box")).to_contain_text("ещё не введены")
    page.click("text=Добавить остатки")
    d = page.locator("dialog[open]")
    expect(d.locator("[name=op_date]")).to_have_value("2026-09-30")
    d.locator("[name=debit_account]").select_option(label="51 Расчётные счета")
    d.locator("[name=credit_account]").select_option(label="00 Ввод остатков")
    d.locator("[name=amount]").fill("10000")
    d.locator("[aria-label='Расчётный счёт (дебет)']").select_option(index=1)
    d.locator("[aria-label='Статья ДДС (дебет)']").select_option(label="Прочие поступления")
    d.locator("button", has_text="Сохранить и провести").click()
    expect(page.locator(".check-box")).to_contain_text("Расхождение 10 000,00 ₽: активов введено больше")
    page.click("text=Добавить остатки")
    d = page.locator("dialog[open]")
    d.locator("[name=debit_account]").select_option(label="00 Ввод остатков")
    d.locator("[name=credit_account]").select_option(label="80 Уставный капитал")
    d.locator("[name=amount]").fill("10000")
    d.locator("button", has_text="Сохранить и провести").click()
    expect(page.locator(".check-box")).to_contain_text("сходятся")
    expect(page.locator("[aria-label='Дата начальных остатков']")).to_be_disabled()
    page.screenshot(path=f"{OUT}/13_opening.png", full_page=True)

    # деньги: остаток на начало из начальных остатков, движение, быстрое поступление
    page.click("#nav a[data-page=money]")
    row = page.locator(".tabulator-row", has_text="Сбер, основной")
    expect(row).to_contain_text("10 000,00")   # на начало
    expect(row).to_contain_text("1 500,50")    # списано по журналу
    expect(row).to_contain_text("8 499,50")    # на конец
    row.locator(".tabulator-cell").first.click()
    expect(page.locator("h2", has_text="Движение: Сбер, основной")).to_contain_text("на начало 10 000,00")
    expect(page.locator(".tabulator-row", has_text="Поставщик Плюс")).to_contain_text("8 499,50")
    page.click("button:has-text('Поступление')")
    d = page.locator("dialog[open]")
    expect(d.locator("[name=debit_account] option:checked")).to_have_text("51 Расчётные счета")
    expect(d.locator("[aria-label='Расчётный счёт (дебет)'] option:checked")).to_contain_text("Сбер, основной")
    expect(d.locator("[name=credit_account] option", has_text="00 Ввод остатков")).to_have_count(0)  # 00 только для остатков
    page.screenshot(path=f"{OUT}/14_money.png", full_page=True)
    d.locator("button", has_text="Отмена").click()

    # загрузка выписки: два платежа, повторная загрузка не создаёт дублей
    stmt = "\n".join([
        "1CClientBankExchange", "ВерсияФормата=1.03", "ДатаНачала=01.10.2026", "ДатаКонца=31.10.2026",
        "РасчСчет=40702810900000012345", "СекцияРасчСчет", "ДатаНачала=01.10.2026", "ДатаКонца=31.10.2026",
        "НачальныйОстаток=10000", "РасчСчет=40702810900000012345", "ВсегоСписано=300", "ВсегоПоступило=5000",
        "КонечныйОстаток=14700", "КонецРасчСчет",
        "СекцияДокумент=Платежное поручение", "Номер=7", "Дата=10.10.2026", "Сумма=5000",
        "ПлательщикСчет=40702810000000000077", "Плательщик=ООО Покупатель", "ПлательщикИНН=7709999999",
        "ПолучательСчет=40702810900000012345", "ДатаПоступило=10.10.2026", "Получатель=ООО Ромашка",
        "ПолучательИНН=7701234567", "НазначениеПлатежа=Оплата по счёту 7", "КонецДокумента",
        "СекцияДокумент=Банковский ордер", "Номер=8", "Дата=11.10.2026", "Сумма=300",
        "ПлательщикСчет=40702810900000012345", "ДатаСписано=11.10.2026", "Плательщик=ООО Ромашка",
        "ПлательщикИНН=7701234567", "ПолучательСчет=47423810000000000099", "Получатель=Банк",
        "ПолучательИНН=7707083893", "ВидОплаты=17", "НазначениеПлатежа=Комиссия за обслуживание", "КонецДокумента",
        "КонецФайла",
    ])
    stmt_path = f"{OUT}/kl_to_1c.txt"
    with open(stmt_path, "wb") as fh:
        fh.write(stmt.encode("cp1251"))
    page.locator("input[type=file]").set_input_files(stmt_path)
    d = page.locator("dialog[open]", has_text="Выписка загружена")
    expect(d).to_contain_text("Создано черновиков: 2")
    expect(d).to_contain_text("Новые контрагенты (1): ООО Покупатель")
    expect(d).to_contain_text("Все платежи файла учтены")
    page.screenshot(path=f"{OUT}/15_statement.png", full_page=True)
    d.locator("button", has_text="Понятно").click()
    page.locator("input[type=file]").set_input_files(stmt_path)
    d = page.locator("dialog[open]", has_text="Выписка загружена")
    expect(d).to_contain_text("Создано черновиков: 0")
    expect(d).to_contain_text("уже были загружены раньше: 2")
    d.locator("button", has_text="Понятно").click()

    # загрузка из Excel: шаблон скачивается, контрагенты и операции загружаются, ошибки строк видны
    from openpyxl import Workbook
    page.click("#nav a[data-page=import]")
    with page.expect_download() as dl:
        page.locator(".import-card", has_text="Операции").locator("button", has_text="Скачать шаблон").click()
    assert dl.value.suggested_filename == "operations.xlsx"
    def xlsx(path, rows):
        wb = Workbook()
        for r in rows:
            wb.active.append(r)
        wb.save(path)
    cp_path = f"{OUT}/counterparties.xlsx"
    xlsx(cp_path, [["Наименование*", "ИНН", "Поставщик"], ["ООО Новый", "7708888888", "да"], ["Плохой", "123", ""]])
    page.locator("input[aria-label='Файл: Контрагенты']").set_input_files(cp_path)
    d = page.locator("dialog[open]", has_text="Файл загружен")
    expect(d).to_contain_text("Добавлено контрагентов: 1 — ООО Новый")
    expect(d).to_contain_text("Строка 3:")
    d.locator("button", has_text="Понятно").click()
    op_path = f"{OUT}/operations.xlsx"
    xlsx(op_path, [["Дата*", "Счёт Дт*", "Счёт Кт*", "Сумма*", "Контрагент (ИНН или наименование)", "Описание"],
                   ["20.10.2026", "44", "60.01", 990, "7708888888", "Реклама из Excel"],
                   ["20.10.2026", "99.99", "60.01", 1, "", ""]])
    page.locator("input[aria-label='Файл: Операции']").set_input_files(op_path)
    d = page.locator("dialog[open]", has_text="Файл загружен")
    expect(d).to_contain_text("Создано черновиков: 1")
    expect(d).to_contain_text("неполных")
    expect(d).to_contain_text("Строка 3: «Счёт Дт»: счёта 99.99 нет в плане счетов")
    page.screenshot(path=f"{OUT}/16_excel.png", full_page=True)
    d.locator("button", has_text="Открыть журнал").click()
    expect(page.locator(".tabulator-row", has_text="Реклама из Excel")).to_be_visible()

    # отчёты: ОСВ, расшифровка цифры, разрез по аналитике, выгрузка в Excel
    page.click("#nav a[data-page=reports]")
    row51 = page.locator(".tabulator-row", has=page.locator(".tabulator-cell[tabulator-field=code]", has_text="51")).first
    expect(row51).to_contain_text("10 000,00")
    expect(row51).to_contain_text("8 499,50")
    expect(page.locator(".tabulator-row.row-total")).to_be_visible()
    row51.locator(".tabulator-cell[tabulator-field=credit] .drill").click()
    d = page.locator("dialog[open]", has_text="Расшифровка")
    expect(d).to_contain_text("кредит: 1 500,50")
    expect(d.locator(".tabulator-row", has_text="Сбер, основной")).to_be_visible()
    page.screenshot(path=f"{OUT}/17_drill.png", full_page=True)
    d.locator("button", has_text="Закрыть").click()
    row51.locator(".link-like").click()
    expect(page.locator("h2", has_text="Счёт 51")).to_be_visible()
    expect(page.locator(".tabulator-row", has_text="Сбер, основной")).to_contain_text("8 499,50")
    page.screenshot(path=f"{OUT}/18_osv.png", full_page=True)
    with page.expect_download() as dl:
        page.click("button:has-text('Скачать в Excel')")
    assert dl.value.suggested_filename.startswith("osv_")

    # ДДС: остатки и статья, расшифровка ячейки
    page.click("button.tab:has-text('ДДС')")
    expect(page.locator(".tabulator-row", has_text="Остаток денег на начало")).to_contain_text("10 000,00")
    expect(page.locator(".tabulator-row", has_text="Остаток денег на конец")).to_contain_text("8 499,50")
    supplier_row = page.locator(".tabulator-row", has_text="Оплата поставщикам")
    expect(supplier_row).to_contain_text("-1 500,50")
    supplier_row.locator(".drill").first.click()
    d = page.locator("dialog[open]", has_text="Расшифровка")
    expect(d).to_contain_text("кредит: 1 500,50")
    page.screenshot(path=f"{OUT}/19_dds.png", full_page=True)
    d.locator("button", has_text="Закрыть").click()
    # ОПиУ: в стенде расходов с «Статьёй ОПиУ» нет — отчёт строится, итоговые строки на месте
    page.click("button.tab:has-text('ОПиУ')")
    expect(page.locator(".tabulator-row", has_text="Чистая прибыль")).to_be_visible()
    expect(page.locator(".tabulator-row", has_text="Валовая прибыль")).to_be_visible()
    page.click("button.tab:has-text('ОСВ')")

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
    expect(page.locator("#nav a")).to_have_count(12)  # нет «Пользователи»
    page.click("#nav a[data-page=organizations]")
    expect(page.locator("text=Добавить организацию")).to_have_count(0)
    expect(page.locator(".tabulator").first.locator(".tabulator-row")).to_have_count(1)  # видит только свою
    expect(page.locator("h2", has_text="Юрлица: Группа АВМВ")).to_be_visible()  # выбрана автоматически
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator("text=Добавить юрлицо")).to_be_hidden()
    page.goto(BASE + "/#/users")  # прямой переход в чужой раздел
    expect(page.locator("#nav a.active")).to_have_text("Журнал операций")
    page.click("#nav a[data-page=organizations]")
    expect(page.locator(".tabulator-row", has_text="Ромашка")).to_be_visible()
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]", has_text="✎")).to_have_count(0)
    page.screenshot(path=f"{OUT}/4_ceo.png", full_page=True)

    # CEO видит журнал, но не вводит и не проводит
    page.click("#nav a[data-page=journal]")
    expect(page.locator(".tabulator-row", has_text="Оплата по счёту 15")).to_be_visible()
    expect(page.locator("text=Новая операция")).to_be_hidden()
    page.locator(".tabulator-row", has_text="Оплата по счёту 15").locator("input[type=checkbox]").check()
    expect(page.locator(".bulk-bar")).to_be_hidden()

    # CEO видит отчёты
    page.click("#nav a[data-page=reports]")
    expect(page.locator(".tabulator-row", has_text="Расчётные счета")).to_contain_text("8 499,50")

    # CEO видит деньги без кнопок ввода
    page.click("#nav a[data-page=money]")
    expect(page.locator(".tabulator-row", has_text="Сбер, основной")).to_contain_text("8 499,50")
    expect(page.locator("button:has-text('Поступление')")).to_have_count(0)

    # CEO видит справочники организации без кнопок изменения
    page.click("#nav a[data-page=references]")
    page.click("button.tab:has-text('Контрагенты')")
    expect(page.locator(".tabulator-row", has_text="Поставщик Плюс")).to_be_visible()
    expect(page.locator("text=Добавить контрагента")).to_have_count(0)
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]")).to_have_count(0)

    # CEO видит план счетов, но не может его менять
    page.click("#nav a[data-page=accounts]")
    expect(page.locator(".tabulator-row", has_text="Касса")).to_be_visible()
    expect(page.locator("text=Добавить счёт или субсчёт")).to_have_count(0)
    expect(page.locator(".tabulator-cell[tabulator-field=_edit]")).to_have_count(0)

    # CEO видит валюты и курсы, но не может их менять
    page.click("#nav a[data-page=currencies]")
    page.locator(".tabulator-row", has_text="Доллар США").locator(".tabulator-cell").first.click()
    expect(page.locator(".tabulator-row", has_text="81,5432")).to_be_visible()
    expect(page.locator("text=Добавить валюту")).to_have_count(0)
    expect(page.locator("text=Добавить курс")).to_be_hidden()
    expect(page.locator(".tabulator-cell[tabulator-field=_delete]")).to_have_count(0)

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

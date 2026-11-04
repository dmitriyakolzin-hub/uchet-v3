// «Справочники»: справочники выбранной организации, по вкладкам.
import { api } from "../api.js";
import { articleIndex } from "../articles.js";
import { lookup, optionsOf, referenceTab } from "../reftab.js";
import { money, parseNumber } from "../format.js";
import { el, makeTable, openForm, toast } from "../ui.js";

const ORG_KEY = "uchet3_org";
const TAB_KEY = "uchet3_ref_tab";

const COUNTERPARTY_KINDS = [
  ["legal", "Организация"],
  ["entrepreneur", "ИП"],
  ["person", "Физлицо"],
];
const NOMENCLATURE_KINDS = [
  ["goods", "Товар"],
  ["material", "Материал"],
  ["product", "Продукция"],
  ["service", "Услуга"],
];
const CONTRACT_KINDS = [
  ["supplier", "С поставщиком"],
  ["customer", "С покупателем"],
  ["other", "Прочий"],
];
// История окладов сотрудника: список и добавление нового оклада с даты
// Периодический платёж договора (docs/TZ.md, раздел 24, «Детали 14.3»): условия «действует с — по», история
async function openContractPayments(contract, org, d) {
  const url = `/api/contracts/${contract.id}/payments`;
  const canEdit = org.permissions.includes("post");
  const [banks, articles, cfos, executors] = await Promise.all([
    api.get(`/api/organizations/${org.id}/bank-accounts`), api.get(`/api/organizations/${org.id}/articles`),
    api.get(`/api/organizations/${org.id}/cfos`), api.get(`/api/organizations/${org.id}/executors`)]);
  const index = articleIndex(articles);
  const outflow = index.leaves.filter((a) => a.is_active && a.is_cash_flow && !a.is_transfer && a.cash_flow_direction === "outflow");
  const cfoLabels = cfoPathLabels(cfos);
  const execNames = new Map(executors.map((e) => [e.id, e.name]));
  const execLabel = (e) => (e.parent_id ? `${execNames.get(e.parent_id)} / ${e.name}` : e.name);
  const dateRu = (v) => (v ? v.split("-").reverse().join(".") : "");
  const listBox = el("div");
  const counterparty = d.counterparties.find((c) => c.id === contract.counterparty_id)?.name ?? "";
  const fields = [
    { name: "valid_from", label: "Действует с", type: "date", required: true },
    { name: "valid_to", label: "Действует по (пусто — бессрочно)", type: "date" },
    { name: "day", label: "Число месяца", type: "number", required: true,
      hint: "Выходной или праздник — ближайший рабочий день раньше" },
    { name: "amount", label: "Сумма", required: true, placeholder: "0,00" },
    { name: "bank_account_id", label: "Расчётный счёт", type: "select",
      options: [["", "— выберет финансовая служба —"], ...banks.filter((b) => b.is_active && b.legal_entity_id === contract.legal_entity_id)
        .map((b) => [String(b.id), `${b.name} ··${b.number.slice(-4)}`])] },
    { name: "article_id", label: "Статья ДДС", type: "select",
      options: [["", "— не указана —"], ...outflow.map((a) => [String(a.id), index.label.get(a.id)])] },
    { name: "cfo_id", label: "ЦФО", type: "select",
      options: [["", "— не указан —"], ...cfos.filter((c) => c.is_active).map((c) => [String(c.id), cfoLabels.get(c.id)])] },
    { name: "executor_id", label: "ЦФУ", type: "select",
      options: [["", "— не указан —"], ...executors.filter((e) => e.is_active).map((e) => [String(e.id), execLabel(e)])] },
    { name: "description", label: "Назначение", placeholder: "аренда склада" },
  ];
  const idOrNull = (v) => (v ? Number(v) : null);
  const toBody = (v) => ({ valid_from: v.valid_from, valid_to: v.valid_to || null, day: Number(v.day),
    amount: parseNumber(v.amount), bank_account_id: idOrNull(v.bank_account_id),
    article_id: idOrNull(v.article_id), cfo_id: idOrNull(v.cfo_id), executor_id: idOrNull(v.executor_id),
    description: v.description });
  const toValues = (t) => ({ ...t, valid_to: t.valid_to ?? "", amount: String(t.amount).replace(".", ","),
    bank_account_id: String(t.bank_account_id ?? ""), article_id: String(t.article_id ?? ""), cfo_id: String(t.cfo_id ?? ""),
    executor_id: String(t.executor_id ?? "") });

  async function render() {
    const terms = await api.get(url);
    listBox.replaceChildren(terms.length ? el("table", { class: "plain plan-cells" },
      el("thead", {}, el("tr", {}, ...["Действует", "Число", "Сумма", "Статья", "ЦФО / ЦФУ", "Назначение", ""].map((t) => el("th", {}, t)))),
      el("tbody", {}, ...terms.map((t) => el("tr", {},
        el("td", {}, `с ${dateRu(t.valid_from)}${t.valid_to ? ` по ${dateRu(t.valid_to)}` : ""}`),
        el("td", {}, String(t.day)),
        el("td", { class: "num" }, money(t.amount)),
        el("td", {}, t.article_id ? index.label.get(t.article_id) ?? "" : ""),
        el("td", {}, [cfoLabels.get(t.cfo_id), t.executor_id ? execLabel(executors.find((e) => e.id === t.executor_id) ?? {}) : ""]
          .filter(Boolean).join(" · ")),
        el("td", {}, t.description),
        el("td", {}, canEdit ? el("button", { type: "button", class: "link", onclick: () => openForm({
          title: "Исправить условия", fields, values: toValues(t), submitText: "Сохранить",
          onSubmit: async (v) => { await api.put(`/api/contract-payments/${t.id}`, toBody(v)); await render(); } }) }, "изменить") : "")))))
      : el("p", { class: "muted" }, "Периодического платежа по договору нет."));
  }
  const addButton = el("button", { type: "button", hidden: !canEdit, onclick: () => openForm({
    title: "Новые условия платежа", fields, values: { valid_from: new Date().toISOString().slice(0, 10), day: "10" },
    submitText: "Сохранить",
    onSubmit: async (v) => { await api.post(url, toBody(v)); toast("Условия сохранены — заявки месяцев обновлены"); await render(); },
  }) }, "Новые условия с даты");
  const dialog = el("dialog", { class: "dialog wide" },
    el("div", { class: "page-head" }, el("h2", {}, `Периодический платёж: ${counterparty}, договор № ${contract.number}`), addButton),
    el("p", { class: "muted" }, "Каждый месяц по условиям сама создаётся заявка на оплату — её согласует финансовый директор. "
      + "Сумма изменилась — «Новые условия с даты»: прежние закроются днём раньше, история сохранится."),
    listBox,
    el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")));
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  await render();
  dialog.showModal();
}

async function openSalaries(employee) {
  const url = `/api/employees/${employee.id}/salaries`;
  const tableBox = el("div");
  const addButton = el("button", { type: "button", onclick: () => add() }, "Новый оклад");
  const dialog = el("dialog", { class: "dialog wide" },
    el("div", { class: "page-head" }, el("h2", {}, `Оклады: ${employee.full_name}`), addButton),
    el("p", { class: "muted" }, "На дату действует последний оклад с датой начала не позже этой даты."),
    tableBox,
    el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")));
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  const table = makeTable(tableBox, {
    columns: [
      { title: "С даты", field: "valid_from", width: 130, formatter: (c) => c.getValue().split("-").reverse().join(".") },
      { title: "Оклад в месяц", field: "amount", width: 160, hozAlign: "right", formatter: (c) => money(c.getValue()) },
      { title: "Примечание", field: "note", widthGrow: 2 },
      { title: "", field: "_delete", width: 48, hozAlign: "center", headerSort: false, formatter: () => "✕",
        cellClick: async (_e, cell) => {
          const r = cell.getRow().getData();
          if (!confirm(`Удалить оклад с ${r.valid_from.split("-").reverse().join(".")}?`)) return;
          try { await api.delete(`/api/employee-salaries/${r.id}`); await load(); } catch (err) { toast(err.message, "error"); }
        } },
    ],
  });
  async function load() {
    try {
      await table.setData(await api.get(url));
    } catch (err) {
      toast(err.message, "error");
    }
  }
  function add() {
    openForm({
      title: "Новый оклад",
      fields: [
        { name: "valid_from", label: "Действует с", type: "date", required: true },
        { name: "amount", label: "Оклад в месяц, ₽", required: true, placeholder: "например, 85 000" },
        { name: "note", label: "Примечание", placeholder: "например, повышение" },
      ],
      onSubmit: async (values) => {
        await api.post(url, { ...values, amount: values.amount.replace(/[\s ]/g, "").replace(",", ".") });
        toast("Оклад сохранён");
        await load();
      },
    });
  }
  await table.ready;
  await load();
}

// Сотрудники в Excel: скачать список (по нему же править) и загрузить — создаёт новых и обновляет найденных
function employeesExcel(org, reload) {
  const input = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": "Файл сотрудников" });
  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    input.value = "";
    try {
      const r = await api.upload(`/api/organizations/${org.id}/import/employees`, file);
      const dialog = el("dialog", { class: "dialog wide" },
        el("h2", {}, `Сотрудники загружены: ${file.name}`),
        el("ul", {}, el("li", {}, `Строк в файле: ${r.rows}`), el("li", {}, `Добавлено: ${r.created.length}`),
          el("li", {}, `Обновлено: ${r.updated.length}`)),
        ...(r.errors.length ? [el("h3", {}, `Не загружено строк: ${r.errors.length}`),
          el("ul", { class: "error-list" }, ...r.errors.map((e) => el("li", {}, `Строка ${e.row}: ${e.error}`)))] : []),
        el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Понятно")));
      document.body.append(dialog);
      dialog.addEventListener("close", () => dialog.remove());
      dialog.showModal();
      await reload();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  return [
    el("button", { type: "button", class: "secondary", onclick: () => api.download(
      `/api/organizations/${org.id}/import/employees.xlsx`, "employees.xlsx").catch((err) => toast(err.message, "error")) },
    "Скачать в Excel"),
    el("button", { type: "button", class: "secondary", onclick: () => { input.value = ""; input.click(); } }, "Загрузить из Excel"),
    input,
  ];
}

const PROJECT_STATUSES = [["planned", "План"], ["active", "В работе"], ["done", "Завершён"]];
const CFO_LEVELS = { 1: "Компания", 2: "Направление", 3: "Филиал", 4: "Магазин", 5: "Подразделение учёта" };

// «АВМВ / Торговля / Филиал Юг» — полный путь ЦФО по названиям
function cfoPathLabels(cfos) {
  const names = new Map(cfos.map((c) => [String(c.id), c.name]));
  return new Map(cfos.map((c) => [c.id, c.path.split(".").map((id) => names.get(id)).join(" / ")]));
}

// Поиск в дереве: строка видна, если подходит она сама или кто-то из подчинённых
function treeSearch(title, field, extra = {}) {
  const match = (text, row) => String(row[field] ?? "").toLowerCase().includes(text)
    || (row._children ?? []).some((child) => match(text, child));
  return { title, field, headerSort: false, headerFilter: "input", headerFilterPlaceholder: "поиск",
    headerFilterFunc: (value, _cell, row) => match(String(value).trim().toLowerCase(), row), ...extra };
}

function cfoTree(cfos) {
  const byId = new Map(cfos.map((c) => [c.id, { ...c }]));
  const roots = [];
  for (const node of byId.values()) {
    const parent = node.parent_id ? byId.get(node.parent_id) : null;
    if (parent) (parent._children ??= []).push(node);
    else roots.push(node);
  }
  return roots;
}

const titleOf = (pairs) => {
  const map = Object.fromEntries(pairs);
  return (cell) => map[cell.getValue()] ?? "";
};

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* не страшно */
  }
}

function recall(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

// Описание вкладок: что загрузить заранее и как построить таблицу и форму
const TABS = [
  {
    key: "bank-accounts",
    title: "Расчётные счета",
    addText: "Добавить расчётный счёт",
    columns: (d) => [
      { title: "Название", field: "name", widthGrow: 2 },
      { title: "Номер счёта", field: "number", width: 210 },
      { title: "Юрлицо", field: "legal_entity_id", formatter: lookup(d.entities, (e) => e.short_name) },
      { title: "Банк", field: "bank_id", widthGrow: 2, formatter: lookup(d.banks, (b) => b.name) },
      { title: "Валюта", field: "currency_id", width: 90, formatter: lookup(d.currencies, (c) => c.code) },
    ],
    fields: (d) => [
      { name: "legal_entity_id", label: "Юрлицо", type: "select", required: true, numeric: true, createOnly: true,
        options: optionsOf(d.entities, (e) => e.short_name, { empty: "— выберите —" }) },
      { name: "name", label: "Название для списков", required: true, placeholder: "Сбер, основной" },
      { name: "number", label: "Номер счёта", required: true, hint: "20 цифр" },
      { name: "bank_id", label: "Банк", type: "select", required: true, numeric: true,
        options: optionsOf(d.banks, (b) => `${b.name} (${b.bic})`, { empty: "— выберите —" }),
        hint: "Нет нужного банка — администратор добавит его в разделе «Банки»" },
      { name: "currency_id", label: "Валюта", type: "select", required: true, numeric: true,
        options: optionsOf(d.currencies, (c) => `${c.code} — ${c.name}`), default: String(d.rub?.id ?? "") },
    ],
  },
  {
    key: "salary-projects",
    title: "Зарплатные проекты",
    addText: "Добавить зарплатный проект",
    columns: (d) => [
      { title: "Наименование", field: "name", widthGrow: 2 },
      { title: "Юрлицо", field: "legal_entity_id", formatter: lookup(d.entities, (e) => e.short_name) },
      { title: "Банк", field: "bank_id", widthGrow: 2, formatter: lookup(d.banks, (b) => b.name) },
    ],
    fields: (d) => [
      { name: "legal_entity_id", label: "Юрлицо", type: "select", required: true, numeric: true, createOnly: true,
        options: optionsOf(d.entities, (e) => e.short_name, { empty: "— выберите —" }) },
      { name: "name", label: "Наименование", required: true, placeholder: "Сбер, зарплатный проект" },
      { name: "bank_id", label: "Банк", type: "select", required: true, numeric: true,
        options: optionsOf(d.banks, (b) => `${b.name} (${b.bic})`, { empty: "— выберите —" }) },
    ],
  },
  {
    key: "counterparties",
    title: "Контрагенты",
    addText: "Добавить контрагента",
    columns: () => [
      { title: "Наименование", field: "name", widthGrow: 3, headerFilter: "input", headerFilterPlaceholder: "поиск" },
      { title: "Вид", field: "kind", width: 130, formatter: titleOf(COUNTERPARTY_KINDS) },
      { title: "ИНН", field: "inn", width: 140, headerFilter: "input", headerFilterPlaceholder: "поиск" },
      { title: "КПП", field: "kpp", width: 110 },
      { title: "Покупатель", field: "is_customer", width: 110, formatter: (c) => (c.getValue() ? "да" : "") },
      { title: "Поставщик", field: "is_supplier", width: 110, formatter: (c) => (c.getValue() ? "да" : "") },
    ],
    fields: () => [
      { name: "name", label: "Наименование", required: true, placeholder: "Ромашка" },
      { name: "full_name", label: "Полное наименование", placeholder: "ООО «Ромашка»" },
      { name: "kind", label: "Вид", type: "select", options: COUNTERPARTY_KINDS, default: "legal" },
      { name: "inn", label: "ИНН", nullable: true, hint: "Организация — 10 цифр, ИП — 12, у физлица можно не указывать" },
      { name: "kpp", label: "КПП", nullable: true, hint: "9 цифр, только у организаций" },
      { name: "is_customer", label: "Покупатель", type: "checkbox" },
      { name: "is_supplier", label: "Поставщик", type: "checkbox" },
    ],
  },
  {
    key: "contracts",
    title: "Договоры",
    addText: "Добавить договор",
    columns: (d) => [
      { title: "Контрагент", field: "counterparty_id", widthGrow: 2, formatter: lookup(d.counterparties, (c) => c.name) },
      { title: "Номер", field: "number", width: 120 },
      { title: "Дата", field: "contract_date", width: 110,
        formatter: (c) => (c.getValue() ? c.getValue().split("-").reverse().join(".") : "") },
      { title: "Вид", field: "kind", width: 140, formatter: titleOf(CONTRACT_KINDS) },
      { title: "Наше юрлицо", field: "legal_entity_id", minWidth: 140, formatter: lookup(d.entities, (e) => e.short_name) },
      { title: "Валюта", field: "currency_id", width: 90, formatter: lookup(d.currencies, (c) => c.code) },
      { title: "Название", field: "name", widthGrow: 2 },
      { title: "Инвестпроект", field: "project_id", widthGrow: 1, formatter: lookup(d.projects, (x) => x.name) },
      { title: "", field: "_periodic", width: 120, headerSort: false, formatter: () => "периодический",
        cssClass: "link-like", tooltip: () => "Периодический платёж по договору",
        cellClick: (_e, c) => openContractPayments(c.getRow().getData(), d.org, d).catch((err) => toast(err.message, "error")) },
    ],
    fields: (d) => [
      { name: "counterparty_id", label: "Контрагент", type: "select", required: true, numeric: true,
        options: optionsOf(d.counterparties, (c) => (c.inn ? `${c.name} (ИНН ${c.inn})` : c.name), { empty: "— выберите —" }),
        hint: "Нет нужного — сначала добавьте его на вкладке «Контрагенты»" },
      { name: "legal_entity_id", label: "Наше юрлицо", type: "select", required: true, numeric: true,
        options: optionsOf(d.entities, (e) => e.short_name, { empty: "— выберите —" }) },
      { name: "kind", label: "Вид договора", type: "select", options: CONTRACT_KINDS, default: "supplier" },
      { name: "number", label: "Номер", required: true },
      { name: "contract_date", label: "Дата", type: "date", nullable: true },
      { name: "currency_id", label: "Валюта", type: "select", required: true, numeric: true,
        options: optionsOf(d.currencies, (c) => `${c.code} — ${c.name}`), default: String(d.rub?.id ?? "") },
      { name: "name", label: "Название (необязательно)", placeholder: "Аренда склада" },
      { name: "project_id", label: "Инвестпроект (необязательно)", type: "select", numeric: true, nullable: true,
        options: [["", "— нет —"], ...d.projects.map((x) => [String(x.id), x.name])],
        hint: "Оплаты по договору инвестпроекта попадут в отчёт «Инвестиции»" },
    ],
  },
  {
    key: "investment-projects",
    title: "Инвестпроекты",
    addText: "Добавить инвестпроект",
    columns: (d) => {
      const labels = cfoPathLabels(d.cfos);
      return [
        { title: "Проект", field: "name", widthGrow: 2 },
        { title: "ЦФО", field: "cfo_id", widthGrow: 2, formatter: (c) => labels.get(c.getValue()) ?? "" },
        { title: "Ответственный (ЦФУ)", field: "executor_id", widthGrow: 1, formatter: lookup(d.executors, (e) => e.name) },
        { title: "Начало", field: "start_on", width: 105, formatter: (c) => (c.getValue() ? c.getValue().split("-").reverse().join(".") : "") },
        { title: "Окончание", field: "finish_on", width: 105, formatter: (c) => (c.getValue() ? c.getValue().split("-").reverse().join(".") : "") },
        { title: "Лимит", field: "limit_amount", width: 140, hozAlign: "right", formatter: (c) => money(c.getValue()) },
        { title: "Статус", field: "status", width: 110, formatter: titleOf(PROJECT_STATUSES) },
        { title: "Сдвиг оплаты, мес.", field: "payment_lag_months", width: 110, hozAlign: "right" },
      ];
    },
    fields: (d) => {
      const labels = cfoPathLabels(d.cfos);
      return [
        { name: "name", label: "Название", required: true, placeholder: "Открытие магазина на Ленина" },
        { name: "cfo_id", label: "ЦФО (где объект)", type: "select", required: true, numeric: true,
          options: [["", "— выберите —"], ...d.cfos.filter((c) => c.is_active).map((c) => [String(c.id), labels.get(c.id)])] },
        { name: "executor_id", label: "Ответственный ЦФУ (необязательно)", type: "select", numeric: true, nullable: true,
          options: [["", "— нет —"], ...d.executors.map((e) => [String(e.id), e.name])] },
        { name: "start_on", label: "Начало", type: "date", nullable: true },
        { name: "finish_on", label: "Окончание", type: "date", nullable: true },
        { name: "limit_amount", label: "Лимит на весь срок, ₽", money: true, default: "0" },
        { name: "status", label: "Статус", type: "select", options: PROJECT_STATUSES, default: "planned" },
        { name: "payment_lag_months", label: "Сдвиг оплаты, месяцев", type: "number", numeric: true, default: "0",
          hint: "0 — оплата в месяце вложения, 1 — в следующем; по нему строятся плановые оплаты в плане ДДС" },
      ];
    },
  },
  {
    key: "cfos",
    title: "ЦФО",
    addText: "Добавить ЦФО уровня «Компания»",
    tree: true,
    columns: (d, tab) => [
      treeSearch("Наименование", "name", { widthGrow: 3 }),
      treeSearch("Код", "code", { width: 120 }),
      { title: "Уровень", field: "level", width: 190, headerSort: false, formatter: (c) => CFO_LEVELS[c.getValue()] },
      { title: "Юрлицо", field: "legal_entity_id", headerSort: false, formatter: lookup(d.entities, (e) => e.short_name) },
      ...(tab.canEdit
        ? [{
            title: "", field: "_add", width: 150, headerSort: false,
            formatter: (c) => (c.getRow().getData().level < 5 ? `+ ${CFO_LEVELS[c.getRow().getData().level + 1].toLowerCase()}` : ""),
            cellClick: (_e, c) => {
              const row = c.getRow().getData();
              if (row.level < 5) tab.ref.create({ parent_id: String(row.id) });
            },
          }]
        : []),
    ],
    fields: (d, row, preset) => {
      const labels = cfoPathLabels(d.cfos);
      const level = row ? row.level : preset.parent_id ? d.cfos.find((c) => String(c.id) === preset.parent_id).level + 1 : 1;
      const parentOptions = d.cfos
        .filter((c) => c.level === level - 1 && c.id !== row?.id)
        .map((c) => [String(c.id), labels.get(c.id)]);
      return [
        { name: "name", label: `Наименование (${CFO_LEVELS[level]})`, required: true },
        { name: "code", label: "Код", hint: "Необязательно; не повторяется в организации. По коду ЦФО находится при загрузке из Excel" },
        ...(level > 1
          ? [{ name: "parent_id", label: `Входит в (${CFO_LEVELS[level - 1]})`, type: "select", required: true, numeric: true, options: parentOptions }]
          : [{ name: "legal_entity_id", label: "Юрлицо (необязательно)", type: "select", numeric: true,
               options: optionsOf(d.entities, (e) => e.short_name, { empty: "— не указано —" }) }]),
      ];
    },
    prepare: cfoTree,
  },
  {
    key: "employees",
    title: "Сотрудники",
    addText: "Добавить сотрудника",
    extra: (org, reload) => employeesExcel(org, reload),
    columns: (d, state) => {
      const labels = cfoPathLabels(d.cfos);
      const date = (c) => (c.getValue() ? c.getValue().split("-").reverse().join(".") : "");
      return [
        { title: "ФИО", field: "full_name", widthGrow: 2, headerFilter: "input", headerFilterPlaceholder: "поиск" },
        { title: "Таб. №", field: "personnel_number", width: 100, headerFilter: "input", headerFilterPlaceholder: "поиск" },
        { title: "Вн. №", field: "internal_number", width: 100, headerFilter: "input", headerFilterPlaceholder: "поиск" },
        { title: "Должность", field: "position", widthGrow: 2 },
        { title: "ЦФО", field: "cfo_id", widthGrow: 3, formatter: (c) => labels.get(c.getValue()) ?? "", tooltip: (_e, c) => labels.get(c.getValue()) ?? "" },
        { title: "Принят", field: "hired_on", width: 110, formatter: date },
        { title: "Уволен", field: "fired_on", width: 110, formatter: date },
        { title: "Юрлицо", field: "legal_entity_id", width: 110, formatter: (c) => d.entities.find((e) => e.id === c.getValue())?.short_name ?? "" },
        { title: "Статья затрат", field: "pl_item_id", widthGrow: 2, formatter: (c) => d.articleIndex.label.get(c.getValue()) ?? "" },
        ...(state.canEdit ? [{ title: "", field: "_salary", width: 90, hozAlign: "center", headerSort: false,
          formatter: () => "оклады", cssClass: "link-like", cellClick: (_e, c) => openSalaries(c.getRow().getData()) }] : []),
      ];
    },
    fields: (d) => {
      const labels = cfoPathLabels(d.cfos);
      return [
        { name: "full_name", label: "ФИО", required: true },
        { name: "personnel_number", label: "Табельный номер" },
        { name: "internal_number", label: "Внутренний номер", hint: "Второй номер сотрудника; не повторяется в организации" },
        { name: "inn", label: "ИНН", nullable: true, hint: "12 цифр" },
        { name: "position", label: "Должность" },
        { name: "cfo_id", label: "ЦФО", type: "select", numeric: true,
          options: [["", "— не указан —"], ...d.cfos.map((c) => [String(c.id), labels.get(c.id) + (c.is_active ? "" : " (архив)")])] },
        { name: "hired_on", label: "Дата приёма", type: "date", nullable: true },
        { name: "fired_on", label: "Дата увольнения", type: "date", nullable: true },
        { name: "legal_entity_id", label: "Работодатель (юрлицо)", type: "select", numeric: true,
          options: optionsOf(d.entities, (e) => e.short_name, { empty: "— не указано —" }) },
        { name: "pl_item_id", label: "Статья затрат на оплату труда (ОПиУ)", type: "select", numeric: true,
          options: [["", "— не указана —"], ...d.articleIndex.leaves.filter((a) => a.is_pl && a.is_active)
            .map((a) => [String(a.id), d.articleIndex.label.get(a.id)])] },
      ];
    },
  },
  {
    key: "executors",
    title: "ЦФУ (исполнители)",
    addText: "Добавить отдел",
    tree: true,
    columns: (d, tab) => [
      treeSearch("Отдел / исполнитель", "name", { widthGrow: 3 }),
      treeSearch("Код", "code", { width: 120 }),
      { title: "Сотрудник", field: "employee_id", widthGrow: 2, headerSort: false,
        formatter: lookup(d.employees, (e) => e.full_name) },
      ...(tab.canEdit
        ? [{ title: "", field: "_add", width: 150, headerSort: false,
            formatter: (c) => (c.getRow().getData().parent_id ? "" : "+ исполнитель"),
            cellClick: (_e, c) => {
              const row = c.getRow().getData();
              if (!row.parent_id) tab.ref.create({ parent_id: String(row.id) });
            } }]
        : []),
    ],
    fields: (d, row, preset) => {
      const isMember = row ? Boolean(row.parent_id) : Boolean(preset.parent_id);
      return [
        { name: "name", label: isMember ? "Исполнитель" : "Отдел", required: true,
          placeholder: isMember ? "Инженер Петров" : "Инженерный отдел" },
        { name: "code", label: "Код", hint: "Необязательно; не повторяется в организации. По коду ЦФУ находится при загрузке из Excel" },
        ...(isMember
          ? [{ name: "parent_id", label: "Отдел", type: "select", required: true, numeric: true,
               options: d.executors.filter((e) => !e.parent_id && e.id !== row?.id).map((e) => [String(e.id), e.name]) }]
          : []),
        { name: "employee_id", label: "Сотрудник (необязательно)", type: "select", numeric: true,
          options: [["", "— не указан —"], ...d.employees.map((e) => [String(e.id), e.full_name])] },
      ];
    },
    prepare: cfoTree,
  },
  {
    key: "nomenclature",
    title: "Номенклатура",
    addText: "Добавить номенклатуру",
    columns: (d) => [
      { title: "Наименование", field: "name", widthGrow: 3, headerFilter: "input", headerFilterPlaceholder: "поиск" },
      { title: "Артикул", field: "article", width: 140, headerFilter: "input", headerFilterPlaceholder: "поиск" },
      { title: "Вид", field: "kind", width: 130, formatter: titleOf(NOMENCLATURE_KINDS) },
      { title: "Ед. изм.", field: "unit_id", width: 100, formatter: lookup(d.units, (u) => u.short_name) },
    ],
    fields: (d) => [
      { name: "name", label: "Наименование", required: true },
      { name: "article", label: "Артикул", hint: "Если указан — не повторяется" },
      { name: "kind", label: "Вид", type: "select", options: NOMENCLATURE_KINDS, default: "goods" },
      { name: "unit_id", label: "Единица измерения", type: "select", required: true, numeric: true,
        options: optionsOf(d.units, (u) => `${u.short_name} — ${u.name}`),
        default: String(d.units.find((u) => u.okei === "796")?.id ?? "") },
    ],
  },
  {
    key: "warehouses",
    title: "Склады",
    addText: "Добавить склад",
    columns: (d) => {
      const labels = cfoPathLabels(d.cfos);
      return [
        { title: "Наименование", field: "name", widthGrow: 2 },
        { title: "ЦФО", field: "cfo_id", widthGrow: 3, formatter: (c) => labels.get(c.getValue()) ?? "" },
      ];
    },
    fields: (d) => {
      const labels = cfoPathLabels(d.cfos);
      return [
        { name: "name", label: "Наименование", required: true, placeholder: "Склад магазина на Ленина" },
        { name: "cfo_id", label: "ЦФО", type: "select", numeric: true,
          options: [["", "— не указан —"], ...d.cfos.map((c) => [String(c.id), labels.get(c.id) + (c.is_active ? "" : " (архив)")])] },
      ];
    },
  },
  {
    // метки примечаний к цифрам отчётов и операциям (docs/TZ.md, раздел 23)
    key: "note-labels",
    title: "Метки примечаний",
    addText: "Добавить метку",
    columns: () => [
      { title: "Метка", field: "name", widthGrow: 2,
        formatter: (c) => `<span class="note-label" style="background:${c.getRow().getData().color}">${
          String(c.getValue()).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch])}</span>` },
      { title: "Цвет", field: "color", width: 110 },
    ],
    fields: () => [
      { name: "name", label: "Название", required: true, placeholder: "Проверить" },
      { name: "color", label: "Цвет", type: "color", default: "#f79009" },
    ],
  },
];

export async function renderReferences(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Справочники"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  let tabKey = TABS.some((t) => t.key === recall(TAB_KEY)) ? recall(TAB_KEY) : TABS[0].key;

  const orgSelect = el("select", { "aria-label": "Организация", class: "inline-select" });
  for (const o of orgs) orgSelect.append(el("option", { value: o.id, selected: o.id === org.id }, o.name));
  const tabBar = el("div", { class: "tabs", role: "tablist" });
  const tabBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Справочники"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    tabBar,
    tabBox,
  );

  async function show() {
    tabBar.replaceChildren(
      ...TABS.map((t) =>
        el("button", {
          type: "button", role: "tab", class: t.key === tabKey ? "tab active" : "tab",
          "aria-selected": t.key === tabKey ? "true" : "false",
          onclick: () => { tabKey = t.key; remember(TAB_KEY, tabKey); show(); },
        }, t.title),
      ),
    );
    tabBox.replaceChildren(el("p", { class: "muted" }, "Загрузка…"));
    const tab = TABS.find((t) => t.key === tabKey);
    // заранее загружаем только то, что нужно этой вкладке
    const need = (keys, load) => (keys.includes(tab.key) ? load() : []);
    const [entities, banks, currencies, counterparties, cfos, units, articles, employees, executors, projects] = await Promise.all([
      api.get(`/api/organizations/${org.id}/legal-entities`),
      need(["bank-accounts", "salary-projects"], () => api.get("/api/banks")),
      need(["bank-accounts", "contracts"], () => api.get("/api/currencies")),
      need(["contracts"], () => api.get(`/api/organizations/${org.id}/counterparties`)),
      need(["cfos", "employees", "warehouses", "investment-projects"], () => api.get(`/api/organizations/${org.id}/cfos`)),
      need(["nomenclature"], () => api.get("/api/units")),
      need(["employees"], () => api.get(`/api/organizations/${org.id}/articles`)),
      need(["executors"], () => api.get(`/api/organizations/${org.id}/employees`)),
      need(["executors", "investment-projects"], () => api.get(`/api/organizations/${org.id}/executors`)),
      need(["contracts"], () => api.get(`/api/organizations/${org.id}/investment-projects`)),
    ]);
    const d = { entities, banks, currencies, counterparties, cfos, units, articles, articleIndex: articleIndex(articles),
      employees, executors, projects, org,
      rub: currencies.find((c) => c.is_base) };
    const state = { canEdit: org.permissions.includes("edit_reference"), ref: null };
    state.ref = await referenceTab(tabBox, {
      title: tab.title,
      addText: tab.addText,
      listUrl: `/api/organizations/${org.id}/${tab.key}`,
      itemUrl: (id) => `/api/${tab.key}/${id}`,
      canEdit: state.canEdit,
      tree: tab.tree,
      // дерево ЦФО нужно и формам — обновляем его вместе с таблицей
      prepare: (list) => {
        if (tab.key === "cfos") d.cfos = list;
        if (tab.key === "executors") d.executors = list;
        return tab.prepare ? tab.prepare(list) : list;
      },
      columns: tab.columns(d, state),
      extra: tab.extra && state.canEdit ? tab.extra(org, () => state.ref.reload()) : [],
      fields: (row, preset) => tab.fields(d, row, preset ?? {}),
    });
  }

  orgSelect.addEventListener("change", () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    show();
  });

  await show();
}

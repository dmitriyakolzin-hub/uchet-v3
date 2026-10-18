// «Справочники»: справочники выбранной организации, по вкладкам.
import { api } from "../api.js";
import { lookup, optionsOf, referenceTab } from "../reftab.js";
import { el } from "../ui.js";

const ORG_KEY = "uchet3_org";
const TAB_KEY = "uchet3_ref_tab";

const COUNTERPARTY_KINDS = [
  ["legal", "Организация"],
  ["entrepreneur", "ИП"],
  ["person", "Физлицо"],
];
const CONTRACT_KINDS = [
  ["supplier", "С поставщиком"],
  ["customer", "С покупателем"],
  ["other", "Прочий"],
];
const CFO_LEVELS = { 1: "Компания", 2: "Направление", 3: "Филиал", 4: "Магазин", 5: "Подразделение учёта" };

// «АВМВ / Торговля / Филиал Юг» — полный путь ЦФО по названиям
function cfoPathLabels(cfos) {
  const names = new Map(cfos.map((c) => [String(c.id), c.name]));
  return new Map(cfos.map((c) => [c.id, c.path.split(".").map((id) => names.get(id)).join(" / ")]));
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
    ],
  },
  {
    key: "cfos",
    title: "ЦФО",
    addText: "Добавить ЦФО уровня «Компания»",
    tree: true,
    columns: (d, tab) => [
      { title: "Наименование", field: "name", widthGrow: 3, headerSort: false },
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
    columns: (d) => {
      const labels = cfoPathLabels(d.cfos);
      const date = (c) => (c.getValue() ? c.getValue().split("-").reverse().join(".") : "");
      return [
        { title: "ФИО", field: "full_name", widthGrow: 2, headerFilter: "input", headerFilterPlaceholder: "поиск" },
        { title: "Таб. №", field: "personnel_number", width: 100 },
        { title: "Должность", field: "position", widthGrow: 2 },
        { title: "ЦФО", field: "cfo_id", widthGrow: 3, formatter: (c) => labels.get(c.getValue()) ?? "", tooltip: (_e, c) => labels.get(c.getValue()) ?? "" },
        { title: "Принят", field: "hired_on", width: 110, formatter: date },
        { title: "Уволен", field: "fired_on", width: 110, formatter: date },
      ];
    },
    fields: (d) => {
      const labels = cfoPathLabels(d.cfos);
      return [
        { name: "full_name", label: "ФИО", required: true },
        { name: "personnel_number", label: "Табельный номер" },
        { name: "inn", label: "ИНН", nullable: true, hint: "12 цифр" },
        { name: "position", label: "Должность" },
        { name: "cfo_id", label: "ЦФО", type: "select", numeric: true,
          options: [["", "— не указан —"], ...d.cfos.map((c) => [String(c.id), labels.get(c.id) + (c.is_active ? "" : " (архив)")])] },
        { name: "hired_on", label: "Дата приёма", type: "date", nullable: true },
        { name: "fired_on", label: "Дата увольнения", type: "date", nullable: true },
      ];
    },
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
    const [entities, banks, currencies, counterparties] = await Promise.all([
      api.get(`/api/organizations/${org.id}/legal-entities`),
      api.get("/api/banks"),
      api.get("/api/currencies"),
      tab.key === "contracts" ? api.get(`/api/organizations/${org.id}/counterparties`) : [],
    ]);
    const d = { entities, banks, currencies, counterparties, cfos: [], rub: currencies.find((c) => c.is_base) };
    const loadCfos = async () => {
      d.cfos = ["cfos", "employees"].includes(tab.key) ? await api.get(`/api/organizations/${org.id}/cfos`) : [];
    };
    await loadCfos();
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
        return tab.prepare ? tab.prepare(list) : list;
      },
      columns: tab.columns(d, state),
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

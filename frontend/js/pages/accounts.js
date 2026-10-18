import { api } from "../api.js";
import { el, makeTable, openForm, toast } from "../ui.js";

const TYPES = [
  ["active", "Активный"],
  ["passive", "Пассивный"],
  ["active_passive", "Активно-пассивный"],
];
const TYPE_TITLES = Object.fromEntries(TYPES);

// Плоский список счетов → дерево для Tabulator (субсчета в _children)
function toTree(accounts) {
  const byId = new Map(accounts.map((a) => [a.id, { ...a }]));
  const roots = [];
  for (const node of byId.values()) {
    const parent = node.parent_id ? byId.get(node.parent_id) : null;
    if (parent) (parent._children ??= []).push(node);
    else roots.push(node);
  }
  return roots;
}

// Флажки видов аналитики; порядок — как в справочнике видов
function analyticsEditor(kinds, selected) {
  const box = el(
    "fieldset",
    { class: "roles" },
    el("legend", {}, "Обязательная аналитика в проводке"),
    ...kinds.map((k) =>
      el(
        "label",
        { class: "check" },
        el("input", { type: "checkbox", value: k.code, checked: selected.includes(k.code) }),
        " ",
        k.title,
      ),
    ),
  );
  box.collect = () => [...box.querySelectorAll("input:checked")].map((i) => i.value);
  return box;
}

export async function renderAccounts(view, { me }) {
  const kinds = await api.get("/api/analytic-kinds");
  const kindTitles = Object.fromEntries(kinds.map((k) => [k.code, k.title]));

  const tableBox = el("div");
  view.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("h1", {}, "План счетов"),
      me.is_admin ? el("button", { type: "button", onclick: () => addAccount() }, "Добавить счёт или субсчёт") : null,
    ),
    el(
      "p",
      { class: "muted" },
      "Общий для всех организаций. Нажмите ▸ слева от счёта, чтобы увидеть субсчета.",
    ),
    tableBox,
  );

  const columns = [
    { title: "Код", field: "code", width: 110, headerSort: false },
    { title: "Наименование", field: "name", widthGrow: 4, minWidth: 220, headerSort: false, tooltip: true },
    { title: "Вид", field: "account_type", formatter: (c) => TYPE_TITLES[c.getValue()], width: 150, headerSort: false },
    { title: "Кол-во", field: "is_quantitative", formatter: (c) => (c.getValue() ? "да" : ""), width: 75, headerSort: false },
    { title: "Валюта", field: "is_currency", formatter: (c) => (c.getValue() ? "да" : ""), width: 75, headerSort: false },
    {
      title: "Обязательная аналитика",
      field: "analytics",
      widthGrow: 3,
      minWidth: 200,
      headerSort: false,
      formatter: (c) => c.getValue().map((k) => kindTitles[k]).join(", "),
      tooltip: (_e, c) => c.getValue().map((k) => kindTitles[k]).join(", "),
    },
    {
      title: "Активен", field: "is_active", width: 80, headerSort: false,
      formatter: (c) => (c.getValue() ? "да" : "архив"),
    },
  ];
  if (me.is_admin) {
    columns.push({
      title: "", field: "_edit", width: 48, hozAlign: "center", headerSort: false,
      formatter: () => "✎", cellClick: (_e, cell) => editAccount(cell.getRow().getData()),
    });
  }

  const table = makeTable(tableBox, {
    dataTree: true,
    dataTreeStartExpanded: false,
    dataTreeChildIndent: 18,
    columns,
    rowFormatter: (row) => row.getElement().classList.toggle("row-archived", !row.getData().is_active),
  });

  async function load() {
    const expanded = new Set(
      table.getRows().filter((r) => r.isTreeExpanded?.()).map((r) => r.getData().id),
    );
    await table.setData(toTree(await api.get("/api/accounts")));
    for (const row of table.getRows()) {
      if (expanded.has(row.getData().id)) row.treeExpand();
    }
  }

  function addAccount() {
    openForm({
      title: "Новый счёт или субсчёт",
      fields: [
        { name: "code", label: "Код", required: true, hint: "Счёт — две цифры (77). Субсчёт — через точку (76.05): получит аналитику и признаки счёта 76" },
        { name: "name", label: "Наименование", required: true },
        { name: "account_type", label: "Вид", type: "select", options: [["", "— как у счёта-родителя —"], ...TYPES] },
      ],
      onSubmit: async (values) => {
        const body = { code: values.code, name: values.name };
        if (values.account_type) body.account_type = values.account_type;
        await api.post("/api/accounts", body);
        toast("Счёт добавлен");
        await load();
      },
    });
  }

  function editAccount(account) {
    const editor = analyticsEditor(kinds, account.analytics);
    openForm({
      title: `Счёт ${account.code}`,
      fields: [
        { name: "name", label: "Наименование", required: true },
        { name: "account_type", label: "Вид", type: "select", options: TYPES },
        { name: "is_quantitative", label: "Ведётся количество", type: "checkbox" },
        { name: "is_currency", label: "Ведётся валюта", type: "checkbox" },
        { name: "is_active", label: "Активен (снимите, чтобы убрать счёт в архив)", type: "checkbox" },
      ],
      values: account,
      extra: editor,
      onSubmit: async (values) => {
        await api.patch(`/api/accounts/${account.id}`, { ...values, analytics: editor.collect() });
        toast("Сохранено");
        await load();
      },
    });
  }

  await table.ready;
  await load();
}

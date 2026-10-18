import { api } from "../api.js";
import { el, makeTable, openForm, toast } from "../ui.js";

const ACTIVITIES = [["operating", "Операционная"], ["investing", "Инвестиционная"], ["financing", "Финансовая"]];
const DIRECTIONS = [["inflow", "Поступление"], ["outflow", "Выплата"]];
const SECTIONS = [
  ["revenue", "Выручка"],
  ["cost", "Себестоимость"],
  ["selling", "Коммерческие расходы"],
  ["admin", "Управленческие расходы"],
  ["other_income", "Прочие доходы"],
  ["other_expense", "Прочие расходы"],
  ["income_tax", "Налог на прибыль"],
];
const title = Object.fromEntries([...ACTIVITIES, ...DIRECTIONS, ...SECTIONS]);
const FILTERS = [["all", "Все"], ["cash_flow", "ДДС"], ["pl", "ОПиУ"]];

export async function renderArticles(view, { me }) {
  const accounts = await api.get("/api/accounts");
  const codeOf = new Map(accounts.map((a) => [a.id, a.code]));
  // в шаблоне — только счета без субсчетов
  const postable = accounts.filter((a) => !a.has_children && a.is_active).map((a) => [String(a.id), `${a.code} ${a.name}`]);

  let filter = "all";
  const filterBar = el("div", { class: "tabs", role: "tablist" });
  const tableBox = el("div");
  view.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("h1", {}, "Статьи ДДС и ОПиУ"),
      me.is_admin ? el("button", { type: "button", onclick: () => edit(null) }, "Добавить статью") : null,
    ),
    el("p", { class: "muted" }, "Общий справочник для всех организаций. Шаблон проводки подставляется при вводе операции, счета можно поменять."),
    filterBar,
    tableBox,
  );

  const columns = [
    { title: "Наименование", field: "name", widthGrow: 3, headerFilter: "input", headerFilterPlaceholder: "поиск" },
    {
      title: "ДДС", field: "cash_flow_activity", widthGrow: 2, minWidth: 250,
      formatter: (c) => {
        const a = c.getRow().getData();
        return a.is_cash_flow ? `${title[a.cash_flow_activity]} · ${title[a.cash_flow_direction]}` : "";
      },
    },
    { title: "ОПиУ", field: "pl_section", widthGrow: 2, formatter: (c) => title[c.getValue()] ?? "" },
    {
      title: "Шаблон проводки", field: "debit_account_id", width: 170, headerSort: false,
      formatter: (c) => {
        const a = c.getRow().getData();
        return a.debit_account_id ? `Дт ${codeOf.get(a.debit_account_id)} — Кт ${codeOf.get(a.credit_account_id)}` : "";
      },
    },
    { title: "Активна", field: "is_active", width: 95, formatter: (c) => (c.getValue() ? "да" : "архив") },
  ];
  if (me.is_admin) {
    columns.push({
      title: "", field: "_edit", width: 48, hozAlign: "center", headerSort: false,
      formatter: () => "✎", cellClick: (_e, cell) => edit(cell.getRow().getData()),
    });
  }
  const table = makeTable(tableBox, {
    columns,
    rowFormatter: (row) => row.getElement().classList.toggle("row-archived", !row.getData().is_active),
  });

  function applyFilter() {
    filterBar.replaceChildren(
      ...FILTERS.map(([key, label]) =>
        el("button", {
          type: "button", role: "tab", class: key === filter ? "tab active" : "tab",
          onclick: () => { filter = key; applyFilter(); },
        }, label),
      ),
    );
    if (filter === "all") table.clearFilter();
    else table.setFilter(filter === "cash_flow" ? "is_cash_flow" : "is_pl", "=", true);
  }

  async function load() {
    await table.setData(await api.get("/api/articles"));
    applyFilter();
  }

  function edit(article) {
    const empty = (label) => [["", label]];
    openForm({
      title: article ? "Статья" : "Новая статья",
      fields: [
        { name: "name", label: "Наименование", required: true },
        { name: "is_cash_flow", label: "Участвует в ДДС", type: "checkbox" },
        { name: "cash_flow_activity", label: "ДДС: вид деятельности", type: "select", options: [...empty("—"), ...ACTIVITIES] },
        { name: "cash_flow_direction", label: "ДДС: направление", type: "select", options: [...empty("—"), ...DIRECTIONS] },
        { name: "is_pl", label: "Участвует в ОПиУ", type: "checkbox" },
        { name: "pl_section", label: "ОПиУ: раздел", type: "select", options: [...empty("—"), ...SECTIONS] },
        { name: "debit_account_id", label: "Шаблон проводки: счёт дебета", type: "select", options: [...empty("— без шаблона —"), ...postable] },
        { name: "credit_account_id", label: "Шаблон проводки: счёт кредита", type: "select", options: [...empty("— без шаблона —"), ...postable] },
        ...(article ? [{ name: "is_active", label: "Активна (снимите, чтобы убрать в архив)", type: "checkbox" }] : []),
      ],
      values: article
        ? { ...article, debit_account_id: String(article.debit_account_id ?? ""), credit_account_id: String(article.credit_account_id ?? "") }
        : { is_cash_flow: true },
      onSubmit: async (v) => {
        const body = {
          name: v.name,
          is_cash_flow: v.is_cash_flow,
          cash_flow_activity: v.cash_flow_activity || null,
          cash_flow_direction: v.cash_flow_direction || null,
          is_pl: v.is_pl,
          pl_section: v.pl_section || null,
          debit_account_id: v.debit_account_id ? Number(v.debit_account_id) : null,
          credit_account_id: v.credit_account_id ? Number(v.credit_account_id) : null,
          is_active: article ? v.is_active : true,
        };
        if (article) await api.put(`/api/articles/${article.id}`, body);
        else await api.post("/api/articles", body);
        toast("Сохранено");
        await load();
      },
    });
  }

  await table.ready;
  await load();
}

// «Статьи ДДС и ОПиУ»: справочник организации деревом — коды, подстатьи (docs/TZ.md, раздел 19, «Детали 9.1»).
import { api } from "../api.js";
import { articleIndex } from "../articles.js";
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
const ORG_KEY = "uchet3_org";

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

// сортировка соседних статей: порядок, затем код по числам, затем название
const byOrder = (a, b) => a.sort_order - b.sort_order
  || (a.code ?? "").localeCompare(b.code ?? "", "ru", { numeric: true }) || a.name.localeCompare(b.name, "ru");

function buildTree(items) {
  const rows = new Map(items.map((a) => [a.id, { ...a }]));
  const roots = [];
  for (const row of rows.values()) {
    const parent = rows.get(row.parent_id);
    if (parent) (parent._children ??= []).push(row);
    else roots.push(row);
  }
  const sort = (list) => { list.sort(byOrder); list.forEach((r) => r._children && sort(r._children)); return list; };
  return sort(roots);
}

export async function renderArticles(view) {
  const [orgs, accounts] = await Promise.all([api.get("/api/organizations"), api.get("/api/accounts")]);
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Статьи ДДС и ОПиУ"), el("p", { class: "muted" }, "Нет доступных организаций."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  const codeOf = new Map(accounts.map((a) => [a.id, a.code]));
  // в шаблоне — только счета без субсчетов
  const postable = accounts.filter((a) => !a.has_children && a.is_active).map((a) => [String(a.id), `${a.code} ${a.name}`]);
  let items = [];
  let filter = "all";
  let search = "";

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    await load();
  });
  const searchInput = el("input", { type: "search", placeholder: "Поиск по коду или названию", "aria-label": "Поиск статьи" });
  searchInput.addEventListener("input", () => { search = searchInput.value.trim().toLowerCase(); show(); });
  const filterBar = el("div", { class: "tabs", role: "tablist" });
  const addButton = el("button", { type: "button", onclick: () => edit(null) }, "Добавить статью");
  const tableBox = el("div");
  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Статьи ДДС и ОПиУ"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("p", { class: "muted" }, "Справочник организации. Статьи вкладываются друг в друга: группа складывается из подстатей, "
      + "в операции и план ставятся только статьи без подстатей. Подстатья берёт признаки ДДС / ОПиУ у родителя. "
      + "Шаблон проводки подставляется при вводе операции, счета можно поменять."),
    el("div", { class: "filters" }, searchInput, addButton),
    filterBar,
    tableBox,
  );

  const canEdit = () => org.permissions.includes("edit_reference");
  const table = makeTable(tableBox, {
    dataTree: true, dataTreeStartExpanded: false, dataTreeChildIndent: 18,
    columns: [
      { title: "Код", field: "code", width: 110, headerSort: false },
      { title: "Наименование", field: "name", widthGrow: 3, minWidth: 260, headerSort: false,
        formatter: (c) => {
          const a = c.getRow().getData();
          const name = String(a.name).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
          const marks = [a.has_children ? "<b>" : "", name, a.has_children ? "</b>" : "",
            a.system_key ? ' <span class="status status-cancelled" title="Статью использует программа">системная</span>' : ""];
          return marks.join("");
        } },
      { title: "ДДС", field: "cash_flow_activity", widthGrow: 2, minWidth: 210, headerSort: false,
        formatter: (c) => {
          const a = c.getRow().getData();
          return a.is_cash_flow ? `${title[a.cash_flow_activity]} · ${title[a.cash_flow_direction]}` : "";
        } },
      { title: "ОПиУ", field: "pl_section", widthGrow: 2, headerSort: false, formatter: (c) => title[c.getValue()] ?? "" },
      { title: "Шаблон проводки", field: "debit_account_id", width: 160, headerSort: false,
        formatter: (c) => {
          const a = c.getRow().getData();
          return a.debit_account_id ? `Дт ${codeOf.get(a.debit_account_id)} — Кт ${codeOf.get(a.credit_account_id)}` : "";
        } },
      { title: "", field: "_add", width: 110, hozAlign: "center", headerSort: false,
        formatter: (c) => (canEdit() && !c.getRow().getData().system_key && !c.getRow().getData().is_transfer ? "+ подстатья" : ""),
        cellClick: (_e, c) => {
          const a = c.getRow().getData();
          if (canEdit() && !a.system_key && !a.is_transfer) edit(null, a);
        } },
      { title: "", field: "_edit", width: 48, hozAlign: "center", headerSort: false,
        formatter: () => (canEdit() ? "✎" : ""), cellClick: (_e, c) => canEdit() && edit(c.getRow().getData()) },
    ],
    rowFormatter: (row) => row.getElement().classList.toggle("row-archived", !row.getData().is_active),
  });

  function show() {
    filterBar.replaceChildren(...FILTERS.map(([key, label]) => el("button", {
      type: "button", role: "tab", class: key === filter ? "tab active" : "tab",
      onclick: () => { filter = key; show(); },
    }, label)));
    const visible = items.filter((a) => filter === "all" || (filter === "cash_flow" ? a.is_cash_flow : a.is_pl));
    if (search) {
      // поиск — плоским списком с полным путём
      const index = articleIndex(items);
      table.setData(visible.filter((a) => index.label.get(a.id).toLowerCase().includes(search))
        .map((a) => ({ ...a, name: index.label.get(a.id).replace(a.code ? `${a.code} ` : "", "") })));
    } else {
      table.setData(buildTree(visible));
    }
  }

  async function load() {
    addButton.hidden = !canEdit();
    items = await api.get(`/api/organizations/${org.id}/articles`);
    show();
  }

  function edit(article, parent = null) {
    const empty = (label) => [["", label]];
    const index = articleIndex(items);
    // родитель — любая статья, кроме самой статьи и её ветки, системных и перевода
    const own = new Set();
    if (article) {
      own.add(article.id);
      let grew = true;
      while (grew) {
        grew = false;
        for (const a of items) if (a.parent_id !== null && own.has(a.parent_id) && !own.has(a.id)) { own.add(a.id); grew = true; }
      }
    }
    const parents = items.filter((a) => !own.has(a.id) && !a.system_key && !a.is_transfer)
      .sort((a, b) => index.label.get(a.id).localeCompare(index.label.get(b.id), "ru", { numeric: true }))
      .map((a) => [String(a.id), index.label.get(a.id)]);
    const base = article ?? parent;
    openForm({
      title: article ? `Статья: ${article.name}` : parent ? `Новая подстатья: ${parent.name}` : "Новая статья",
      fields: [
        { name: "code", label: "Код", placeholder: "например, 2.3.2.", hint: "Необязательно; не повторяется в организации" },
        { name: "name", label: "Наименование", required: true },
        { name: "parent_id", label: "Родительская статья", type: "select", options: [...empty("— верхний уровень —"), ...parents],
          hint: "У подстатьи признаки ДДС / ОПиУ берутся от родителя — поля ниже для неё не меняются" },
        { name: "sort_order", label: "Порядок среди соседних", type: "number" },
        { name: "is_cash_flow", label: "Участвует в ДДС", type: "checkbox" },
        { name: "cash_flow_activity", label: "ДДС: вид деятельности", type: "select", options: [...empty("—"), ...ACTIVITIES] },
        { name: "cash_flow_direction", label: "ДДС: направление", type: "select", options: [...empty("—"), ...DIRECTIONS] },
        { name: "is_pl", label: "Участвует в ОПиУ", type: "checkbox" },
        { name: "pl_section", label: "ОПиУ: раздел", type: "select", options: [...empty("—"), ...SECTIONS] },
        { name: "debit_account_id", label: "Шаблон проводки: счёт дебета", type: "select", options: [...empty("— без шаблона —"), ...postable] },
        { name: "credit_account_id", label: "Шаблон проводки: счёт кредита", type: "select", options: [...empty("— без шаблона —"), ...postable] },
        ...(article ? [{ name: "is_active", label: "Активна (снимите, чтобы убрать в архив)", type: "checkbox" }] : []),
      ],
      values: {
        ...(base ?? { is_cash_flow: true }),
        code: article?.code ?? "", name: article?.name ?? "",
        parent_id: String(article ? (article.parent_id ?? "") : (parent?.id ?? "")),
        sort_order: String(article?.sort_order ?? 0),
        debit_account_id: String(article?.debit_account_id ?? ""), credit_account_id: String(article?.credit_account_id ?? ""),
        is_active: article?.is_active ?? true,
      },
      onSubmit: async (v) => {
        const body = {
          code: v.code || null, name: v.name, parent_id: v.parent_id ? Number(v.parent_id) : null,
          sort_order: Number(v.sort_order || 0),
          is_cash_flow: v.is_cash_flow, cash_flow_activity: v.cash_flow_activity || null,
          cash_flow_direction: v.cash_flow_direction || null, is_pl: v.is_pl, pl_section: v.pl_section || null,
          debit_account_id: v.debit_account_id ? Number(v.debit_account_id) : null,
          credit_account_id: v.credit_account_id ? Number(v.credit_account_id) : null,
          is_transfer: article?.is_transfer ?? false,
          is_active: article ? v.is_active : true,
        };
        if (article) await api.put(`/api/articles/${article.id}`, body);
        else await api.post(`/api/organizations/${org.id}/articles`, body);
        toast("Сохранено");
        await load();
      },
    });
  }

  await table.ready;
  await load();
}

// «Журнал операций»: фильтры, таблица операций, массовые действия, форма операции.
import { api } from "../api.js";
import { date, isoDate, money, monthBounds, sumMoney } from "../format.js";
import { openOperation } from "../opform.js";
import { el, makeTable, toast } from "../ui.js";

const ORG_KEY = "uchet3_org";
const LIMIT = 500;
const STATUSES = [["", "Все статусы"], ["draft", "Черновики"], ["posted", "Проведённые"], ["cancelled", "Аннулированные"]];
const STATUS_TITLES = { draft: "Черновик", posted: "Проведена", cancelled: "Аннулирована" };
const BULK = [
  { action: "post", label: "Провести", perm: "post" },
  { action: "unpost", label: "Вернуть в черновик", perm: "post" },
  { action: "cancel", label: "Аннулировать", perm: "post" },
  { action: "restore", label: "Восстановить", perm: "post" },
  { action: "delete", label: "Удалить черновики", perm: "draft", confirm: true },
];

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

// Все справочники, нужные форме операции и фильтрам
async function loadReferences(orgId) {
  const o = `/api/organizations/${orgId}`;
  const [accounts, articles, currencies, entities, bankAccounts, counterparties, contracts, employees, nomenclature, warehouses, cfos] =
    await Promise.all([
      api.get("/api/accounts"), api.get("/api/articles"), api.get("/api/currencies"),
      api.get(`${o}/legal-entities`), api.get(`${o}/bank-accounts`), api.get(`${o}/counterparties`),
      api.get(`${o}/contracts`), api.get(`${o}/employees`), api.get(`${o}/nomenclature`),
      api.get(`${o}/warehouses`), api.get(`${o}/cfos`),
    ]);
  const cfoNames = new Map(cfos.map((c) => [String(c.id), c.name]));
  const activeOnly = (items) => items.filter((i) => i.is_active);
  return {
    accounts, articles, currencies, entities,
    bankAccounts: activeOnly(bankAccounts), counterparties: activeOnly(counterparties), contracts: activeOnly(contracts),
    employees: activeOnly(employees), nomenclature: activeOnly(nomenclature), warehouses: activeOnly(warehouses),
    cfos: activeOnly(cfos),
    cfoLabel: new Map(cfos.map((c) => [c.id, c.path.split(".").map((id) => cfoNames.get(id)).join(" / ")])),
    counterpartyName: new Map(counterparties.map((c) => [c.id, c.name])),
    rubId: currencies.find((c) => c.is_base)?.id,
    today: isoDate(new Date()),
  };
}

export async function renderJournal(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Журнал операций"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  let ref = await loadReferences(org.id);
  const [monthStart, monthEnd] = monthBounds();

  // --- Фильтры ---
  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const f = {
    date_from: el("input", { type: "date", "aria-label": "С даты" }),
    date_to: el("input", { type: "date", "aria-label": "По дату" }),
    status: el("select", { "aria-label": "Статус" }, ...STATUSES.map(([v, t]) => el("option", { value: v }, t))),
    legal_entity_id: el("select", { "aria-label": "Юрлицо" }),
    account_id: el("select", { "aria-label": "Счёт" }),
    counterparty_id: el("select", { "aria-label": "Контрагент" }),
    article_id: el("select", { "aria-label": "Статья" }),
    q: el("input", { type: "search", placeholder: "Поиск в описании", "aria-label": "Поиск" }),
  };
  f.date_from.value = monthStart;
  f.date_to.value = monthEnd;

  function fillFilterLists() {
    const fill = (node, empty, items) => node.replaceChildren(el("option", { value: "" }, empty), ...items.map(([v, t]) => el("option", { value: v }, t)));
    fill(f.legal_entity_id, "Все юрлица", ref.entities.map((e) => [e.id, e.short_name]));
    fill(f.account_id, "Все счета", ref.accounts.map((a) => [a.id, `${a.code} ${a.name}`]));
    fill(f.counterparty_id, "Все контрагенты", ref.counterparties.map((c) => [c.id, c.name]));
    fill(f.article_id, "Все статьи", ref.articles.map((a) => [a.id, a.name]));
  }
  fillFilterLists();

  const newButton = el("button", { type: "button", onclick: () => openForm(null) }, "Новая операция");
  const bulkBar = el("div", { class: "bulk-bar", hidden: true });
  const summary = el("p", { class: "muted" });
  const tableBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Журнал операций"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("div", { class: "filters" }, ...Object.values(f)),
    el("div", { class: "page-head" }, summary, newButton),
    bulkBar,
    tableBox,
  );

  const accountCode = () => new Map(ref.accounts.map((a) => [a.id, a.code]));
  let codes = accountCode();
  const entityName = () => new Map(ref.entities.map((e) => [e.id, e.short_name]));
  let entities = entityName();
  const articleName = () => new Map(ref.articles.map((a) => [a.id, a.name]));
  let articles = articleName();

  const table = makeTable(tableBox, {
    index: "id",
    selectableRows: "highlight",  // отмечают только флажки; щелчок по строке открывает операцию
    columns: [
      { formatter: "rowSelection", titleFormatter: "rowSelection", hozAlign: "center", headerSort: false, width: 44 },
      { title: "№", field: "number", width: 75, sorter: "number" },
      { title: "Дата", field: "op_date", width: 110, formatter: (c) => date(c.getValue()) },
      {
        title: "Статус", field: "status", width: 130,
        formatter: (c) => `<span class="status status-${c.getValue()}">${STATUS_TITLES[c.getValue()]}</span>`,
      },
      { title: "Юрлицо", field: "legal_entity_id", width: 120, formatter: (c) => escape(entities.get(c.getValue()) ?? "") },
      { title: "Статья", field: "article_id", widthGrow: 2, formatter: (c) => escape(articles.get(c.getValue()) ?? "") },
      {
        title: "Проводки", field: "postings", widthGrow: 2, headerSort: false,
        formatter: (c) => escape(c.getValue().map((p) => `${codes.get(p.debit_account_id) ?? "?"} → ${codes.get(p.credit_account_id) ?? "?"}`).join(", ")),
      },
      { title: "Описание", field: "description", widthGrow: 3, tooltip: true },
      { title: "Сумма, ₽", field: "total_rub", width: 140, hozAlign: "right", sorter: "number", formatter: (c) => money(c.getValue()) },
    ],
    rowFormatter: (row) => row.getElement().classList.toggle("row-archived", row.getData().status === "cancelled"),
  });
  // щелчок по строке открывает операцию; по флажку — только отмечает
  table.on("cellClick", (_e, cell) => {
    if (cell.getColumn().getDefinition().formatter === "rowSelection") return;
    openForm(cell.getRow().getData());
  });
  table.on("rowSelectionChanged", () => renderBulk());

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
  }

  function renderBulk() {
    const selected = table.getSelectedData();
    const allowed = BULK.filter((b) => org.permissions.includes(b.perm));
    bulkBar.hidden = selected.length === 0 || allowed.length === 0;
    bulkBar.replaceChildren(
      el("span", {}, `Отмечено: ${selected.length}`),
      ...allowed.map((b) => el("button", { type: "button", class: "secondary", onclick: () => runBulk(b, selected) }, b.label)),
    );
  }

  async function runBulk(b, selected) {
    if (b.confirm && !confirm(`${b.label}: ${selected.length} шт.?`)) return;
    try {
      const result = await api.post(`/api/organizations/${org.id}/operations/bulk`, { action: b.action, ids: selected.map((o) => o.id) });
      const numbers = new Map(selected.map((o) => [o.id, o.number]));
      if (result.failed === 0) {
        toast(`${b.label}: выполнено ${result.done}`);
      } else {
        showBulkErrors(b.label, result, numbers);
      }
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function showBulkErrors(label, result, numbers) {
    const dialog = el(
      "dialog",
      { class: "dialog wide" },
      el("h2", {}, `${label}: выполнено ${result.done}, не выполнено ${result.failed}`),
      el("ul", { class: "error-list" }, ...result.results.filter((r) => !r.ok).map((r) => el("li", {}, `№ ${numbers.get(r.id) ?? r.id}: ${r.error}`))),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Понятно")),
    );
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async function load() {
    const params = new URLSearchParams({ limit: LIMIT });
    for (const [key, node] of Object.entries(f)) if (node.value) params.set(key, node.value);
    const data = await api.get(`/api/organizations/${org.id}/operations?${params}`);
    await table.setData(data.items);
    const total = sumMoney(data.items.filter((o) => o.status === "posted").map((o) => o.total_rub));
    summary.textContent = data.total > data.items.length
      ? `Показано ${data.items.length} из ${data.total} — уточните фильтр`
      : `Операций: ${data.total}`;
    if (data.items.length) summary.textContent += ` · сумма проведённых: ${money(total)} ₽`;
    renderBulk();
  }

  function openForm(op) {
    openOperation({ ref, org, op, perms: org.permissions, onChanged: load });
  }

  newButton.hidden = !org.permissions.includes("draft");
  for (const node of Object.values(f)) node.addEventListener("change", load);
  f.q.addEventListener("input", () => { clearTimeout(f.q.timer); f.q.timer = setTimeout(load, 400); });
  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    ref = await loadReferences(org.id);
    codes = accountCode();
    entities = entityName();
    articles = articleName();
    fillFilterLists();
    newButton.hidden = !org.permissions.includes("draft");
    await load();
  });

  await table.ready;
  await load();
}

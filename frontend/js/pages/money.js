// «Деньги»: остатки по расчётным счетам и кассе, движение по выбранному счёту, быстрый ввод.
import { api } from "../api.js";
import { date, money, monthBounds } from "../format.js";
import { openOperation } from "../opform.js";
import { el, makeTable, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

const moneyCol = (title, field, extra = {}) => ({
  title, field, hozAlign: "right", width: 150, formatter: (c) => money(c.getValue()), ...extra,
});

export async function renderMoney(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Деньги"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  let ref = await loadReferences(org.id);
  let selected = null;
  const [monthStart, monthEnd] = monthBounds();

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const dateFrom = el("input", { type: "date", "aria-label": "С даты" });
  const dateTo = el("input", { type: "date", "aria-label": "По дату" });
  dateFrom.value = monthStart;
  dateTo.value = monthEnd;

  const quick = el("div", { class: "quick-actions" });
  const accountsBox = el("div");
  const movementsTitle = el("h2", {}, "Движение");
  const movementsHint = el("p", { class: "muted" }, "Выберите счёт в таблице выше.");
  const movementsBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Деньги"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("div", { class: "filters" }, dateFrom, dateTo),
    quick,
    accountsBox,
    el("div", { class: "page-head section" }, movementsTitle),
    movementsHint,
    movementsBox,
  );

  const entityName = (id) => ref.entities.find((e) => e.id === id)?.short_name ?? "";
  const accountsTable = makeTable(accountsBox, {
    index: "key",
    selectableRows: 1,
    columns: [
      { title: "Счёт", field: "name", widthGrow: 3, formatter: (c) => {
        const r = c.getRow().getData();
        return r.kind === "bank" ? `${escape(r.name)} <span class="muted">··${r.number.slice(-4)}</span>` : escape(r.name);
      } },
      { title: "Юрлицо", field: "legal_entity_id", width: 130, formatter: (c) => escape(entityName(c.getValue())) },
      { title: "Валюта", field: "currency", width: 90 },
      moneyCol("На начало", "opening"),
      moneyCol("Поступления", "inflow"),
      moneyCol("Списания", "outflow"),
      moneyCol("На конец", "closing", { cssClass: "strong" }),
    ],
    columnDefaults: { headerSort: false }, // строка «Итого» всегда последняя
  });
  accountsTable.on("rowClick", (_e, row) => selectAccount(row.getData()));

  const counterpartyName = (id) => ref.counterpartyName.get(id) ?? "";
  const articleName = (id) => ref.articles.find((a) => a.id === id)?.name ?? "";
  const movementsTable = makeTable(movementsBox, {
    columns: [
      { title: "Дата", field: "op_date", width: 110, formatter: (c) => date(c.getValue()) },
      { title: "№", field: "number", width: 70 },
      { title: "Контрагент", field: "counterparty_id", widthGrow: 2, formatter: (c) => escape(counterpartyName(c.getValue())) },
      { title: "Статья ДДС", field: "cash_flow_item", widthGrow: 2, formatter: (c) => escape(articleName(c.getValue())) },
      { title: "Описание", field: "description", widthGrow: 2, tooltip: true },
      moneyCol("Приход", "inflow", { width: 130 }),
      moneyCol("Расход", "outflow", { width: 130 }),
      moneyCol("Остаток", "balance", { width: 140 }),
    ],
  });
  movementsTable.on("rowClick", async (_e, row) => {
    const op = await api.get(`/api/operations/${row.getData().operation_id}`);
    openOperation({ ref, org, op, perms: org.permissions, onChanged: reload });
  });

  function escape(text) {
    return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
  }

  const accountId = (code) => ref.accounts.find((a) => a.code === code)?.id;
  const articleId = (name) => ref.articles.find((a) => a.name === name)?.id;

  // Заготовки быстрых операций для выбранного счёта
  function moneySide(row) {
    if (row.kind === "cash") return { account: accountId("50"), analytics: { cfo: row.cfo_id } };
    const account = row.currency === "RUB" ? accountId("51") : accountId("52");
    return { account, analytics: { bank_account: row.bank_account_id } };
  }

  const fileInput = el("input", { type: "file", accept: ".txt", hidden: true, "aria-label": "Файл выписки" });
  fileInput.addEventListener("change", () => uploadStatement(fileInput.files[0]));

  function renderQuick() {
    const can = org.permissions.includes("draft");
    const hint = selected ? `для «${selected.name}»` : "— сначала выберите счёт";
    quick.replaceChildren(...(can ? [
      el("button", { type: "button", class: "secondary", onclick: () => { fileInput.value = ""; fileInput.click(); } }, "Загрузить выписку"),
      fileInput,
      el("button", { type: "button", disabled: !selected, onclick: () => openPreset("in") }, "Поступление"),
      el("button", { type: "button", disabled: !selected, onclick: () => openPreset("out") }, "Списание"),
      el("button", { type: "button", class: "secondary", disabled: !selected || selected.kind !== "bank", onclick: () => openPreset("transfer") }, "Перевод между своими счетами"),
      el("span", { class: "muted" }, hint),
    ] : []));
  }

  function openPreset(kind) {
    const side = moneySide(selected);
    let preset;
    if (kind === "in") preset = { posting: { debit_account_id: side.account, debit: side.analytics } };
    if (kind === "out") preset = { posting: { credit_account_id: side.account, credit: side.analytics } };
    if (kind === "transfer") {
      const art = articleId("Перевод между своими счетами");
      preset = {
        article_id: art,
        posting: { debit_account_id: side.account, credit_account_id: side.account, credit: { ...side.analytics, cash_flow_item: art } },
      };
    }
    openOperation({ ref, org, op: null, perms: org.permissions, onChanged: reload, preset });
  }

  async function uploadStatement(file) {
    if (!file) return;
    fileInput.value = ""; // чтобы тот же файл можно было выбрать снова
    toast(`Загружается ${file.name}…`);
    try {
      const result = await api.upload(`/api/organizations/${org.id}/bank-statements`, file);
      showImportResult(file.name, result);
      await reload();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function showImportResult(fileName, r) {
    const period = r.date_from ? `${date(r.date_from)} — ${date(r.date_to)}` : "";
    const lines = [
      `Создано черновиков: ${r.created.length}`,
      r.incomplete.length ? `Из них неполных (нужно дополнить в журнале): ${r.incomplete.length} — № ${r.incomplete.join(", ")}` : null,
      r.skipped_duplicates ? `Пропущено — уже были загружены раньше: ${r.skipped_duplicates}` : null,
      r.skipped_before_opening ? `Пропущено — не позже даты начальных остатков: ${r.skipped_before_opening}` : null,
      r.new_counterparties.length ? `Новые контрагенты (${r.new_counterparties.length}): ${r.new_counterparties.join(", ")}` : null,
    ].filter(Boolean);
    const checks = r.accounts.map((a) => {
      const diff = (x, y) => (x === y ? "✓" : "≠");
      return el("table", { class: "recon" },
        el("caption", {}, `${a.name} ··${a.number.slice(-4)}`),
        el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, "По выписке"), el("th", {}, "В системе (проведено)"), el("th", {}, ""))),
        el("tbody", {},
          el("tr", {}, el("td", {}, "Остаток на начало"), el("td", {}, money(a.statement_opening)), el("td", {}, money(a.system_opening)), el("td", {}, diff(a.statement_opening, a.system_opening))),
          el("tr", {}, el("td", {}, "Остаток на конец"), el("td", {}, money(a.statement_closing)), el("td", {}, money(a.system_closing)), el("td", {}, diff(a.statement_closing, a.system_closing))),
        ),
        el("tfoot", {}, el("tr", {}, el("td", { colspan: 4 }, a.totals_match
          ? `Все платежи файла учтены: поступило ${money(a.statement_in)}, списано ${money(a.statement_out)}`
          : `Внимание: итоги банка (поступило ${money(a.statement_in)}, списано ${money(a.statement_out)}) не совпадают с суммой платежей в файле (${money(a.documents_in)} / ${money(a.documents_out)})`))),
      );
    });
    const dialog = el(
      "dialog",
      { class: "dialog wide" },
      el("h2", {}, `Выписка загружена: ${fileName}`),
      el("p", { class: "muted" }, period),
      el("ul", {}, ...lines.map((l) => el("li", {}, l))),
      ...checks,
      el("p", { class: "muted" }, "Остаток в системе сойдётся с выпиской, когда все её платежи будут проведены. Черновики — в «Журнале операций» (фильтр «Черновики»)."),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Понятно")),
    );
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async function loadAccounts() {
    const data = await api.get(`/api/organizations/${org.id}/money?date_from=${dateFrom.value}&date_to=${dateTo.value}`);
    const rows = data.rows.map((r) => ({ ...r, key: r.kind === "bank" ? `b${r.bank_account_id}` : `c${r.cfo_id}` }));
    await accountsTable.setData(rows);
    // итоговая строка — точные суммы с сервера
    accountsTable.addRow({ key: "total", name: "Итого", currency: "", opening: data.opening, inflow: data.inflow, outflow: data.outflow, closing: data.closing, kind: "total" }, false);
    accountsTable.getRow("total")?.getElement().classList.add("row-total");
    if (selected) {
      selected = rows.find((r) => r.key === selected.key) ?? null;
      if (selected) accountsTable.selectRow(selected.key);
    }
  }

  async function selectAccount(row) {
    if (row.kind === "total") return;
    selected = row;
    accountsTable.deselectRow();
    accountsTable.selectRow(row.key);
    renderQuick();
    movementsHint.hidden = true;
    const param = row.kind === "bank" ? `bank_account_id=${row.bank_account_id}` : `cfo_id=${row.cfo_id}`;
    const data = await api.get(`/api/organizations/${org.id}/money/movements?${param}&date_from=${dateFrom.value}&date_to=${dateTo.value}`);
    movementsTitle.textContent = `Движение: ${row.name} — на начало ${money(data.opening)}, на конец ${money(data.closing)}`;
    await movementsTable.setData(data.rows);
  }

  async function reload() {
    await loadAccounts();
    if (selected) await selectAccount(selected);
  }

  dateFrom.addEventListener("change", reload);
  dateTo.addEventListener("change", reload);
  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    ref = await loadReferences(org.id);
    selected = null;
    movementsHint.hidden = false;
    movementsTitle.textContent = "Движение";
    await movementsTable.setData([]);
    renderQuick();
    await loadAccounts();
  });

  renderQuick();
  await Promise.all([accountsTable.ready, movementsTable.ready]);
  await loadAccounts();
}

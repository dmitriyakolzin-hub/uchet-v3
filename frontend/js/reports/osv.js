// Оборотно-сальдовая ведомость: дерево счетов, разрез счёта по аналитике, расшифровка любой цифры.
import { api } from "../api.js";
import { el, makeTable, toast } from "../ui.js";
import { moneyCell, openDrill } from "./common.js";

// Колонка отчёта → какие проводки её составляют
const CELLS = {
  opening_debit: { title: "сальдо на начало", period: "opening" },
  opening_credit: { title: "сальдо на начало", period: "opening" },
  debit: { title: "обороты Дт", period: "turnover", side: "debit" },
  credit: { title: "обороты Кт", period: "turnover", side: "credit" },
  closing_debit: { title: "сальдо на конец", period: "closing" },
  closing_credit: { title: "сальдо на конец", period: "closing" },
};

function balanceColumns() {
  const col = (title, field) => ({ title, field, hozAlign: "right", minWidth: 100, widthGrow: 1, headerSort: false, formatter: moneyCell });
  return [
    { title: "Сальдо на начало", columns: [col("Дт", "opening_debit"), col("Кт", "opening_credit")] },
    { title: "Обороты за период", columns: [col("Дт", "debit"), col("Кт", "credit")] },
    { title: "Сальдо на конец", columns: [col("Дт", "closing_debit"), col("Кт", "closing_credit")] },
  ];
}

function toTree(rows) {
  const byId = new Map(rows.map((r) => [r.account_id, { ...r }]));
  const roots = [];
  for (const node of byId.values()) {
    const parent = node.parent_id ? byId.get(node.parent_id) : null;
    if (parent) (parent._children ??= []).push(node);
    else roots.push(node);
  }
  return roots;
}

export function osvReport({ box, ctx }) {
  const tableBox = el("div");
  const detailsHead = el("div", { class: "page-head section" });
  const detailsBox = el("div");
  box.replaceChildren(
    el("p", { class: "muted" }, "Щёлкните по сумме — откроются проводки, из которых она сложилась. Щёлкните по счёту с аналитикой — ОСВ по этому счёту в разрезе контрагентов, договоров и т.п."),
    tableBox, detailsHead, detailsBox,
  );
  let selected = null; // счёт, открытый в разрезе по аналитике
  let kind = null;

  const drillFor = (field, title, extra) => {
    const cell = CELLS[field];
    if (!cell) return;
    openDrill({
      org: ctx.org(), ref: ctx.ref(), filters: ctx.filters,
      extra: { period: cell.period, side: cell.side, ...extra },
      title: `${title}, ${cell.title} (${ctx.filters.period()})`,
      onChanged: () => ctx.reload(),
    });
  };

  const table = makeTable(tableBox, {
    dataTree: true,
    dataTreeStartExpanded: false,
    dataTreeChildIndent: 18,
    index: "account_id",
    columns: [
      { title: "Счёт", field: "code", width: 95, headerSort: false },
      { title: "Наименование", field: "name", minWidth: 180, widthGrow: 2.5, headerSort: false,
        formatter: (c) => {
          const r = c.getRow().getData();
          const text = String(r.name).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
          return r.has_analytics ? `<span class="link-like">${text}</span>` : text;
        } },
      ...balanceColumns(),
    ],
  });
  table.on("cellClick", (_e, cell) => {
    const r = cell.getRow().getData();
    if (r.account_id === "total") return;
    const field = cell.getField();
    if (field === "name" && r.has_analytics) {
      selected = r;
      kind = null;
      loadDetails();
    } else if (CELLS[field] && Number(cell.getValue()) !== 0) {
      drillFor(field, `счёт ${r.code}`, { account_id: r.account_id });
    }
  });

  const detailsTable = makeTable(detailsBox, {
    index: "key",
    columns: [
      { title: "Значение аналитики", field: "name", minWidth: 200, widthGrow: 3.5, headerSort: false },
      ...balanceColumns(),
    ],
  });
  detailsTable.on("cellClick", (_e, cell) => {
    const r = cell.getRow().getData();
    const field = cell.getField();
    if (r.value === "total" || !CELLS[field] || Number(cell.getValue()) === 0) return;
    drillFor(field, `счёт ${selected.code}, ${r.name}`, {
      account_id: selected.account_id, a: [`${kind}:${r.value ?? "none"}`],
    });
  });

  async function loadDetails() {
    if (!selected) {
      detailsHead.replaceChildren();
      await detailsTable.setData([]);
      detailsBox.hidden = true;
      return;
    }
    detailsBox.hidden = false;
    let data;
    try {
      data = await api.get(`/api/organizations/${ctx.org().id}/reports/osv/details?${ctx.filters.params({ account_id: selected.account_id, kind })}`);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    kind = data.kind;
    const kindSelect = el("select", { class: "inline-select", "aria-label": "Разрез" },
      ...data.kinds.map((k) => el("option", { value: k.code, selected: k.code === kind }, k.title)));
    kindSelect.addEventListener("change", () => { kind = kindSelect.value; loadDetails(); });
    detailsHead.replaceChildren(
      el("h2", {}, `Счёт ${selected.code} «${selected.name}»`),
      el("label", { class: "inline" }, "в разрезе ", kindSelect,
        el("button", { type: "button", class: "secondary", onclick: () => { selected = null; loadDetails(); } }, "Закрыть")),
    );
    await detailsTable.setData([
      ...data.rows.map((r) => ({ ...r, key: String(r.value) })),
      { key: "total", value: "total", name: "Итого", ...data.total },
    ]);
    detailsTable.getRow("total")?.getElement().classList.add("row-total");
  }

  async function load() {
    const data = await api.get(`/api/organizations/${ctx.org().id}/reports/osv?${ctx.filters.params()}`);
    await table.setData([...toTree(data.rows), { account_id: "total", code: "Итого", name: "", ...data.total }]);
    table.getRow("total")?.getElement().classList.add("row-total");
    if (selected) {
      selected = data.rows.find((r) => r.account_id === selected.account_id) ?? null;
      await loadDetails();
    }
  }

  return {
    ready: Promise.all([table.ready, detailsTable.ready]).then(() => { detailsBox.hidden = true; }),
    load,
    reset() { selected = null; return loadDetails(); },
    excel: () => api.download(`/api/organizations/${ctx.org().id}/reports/osv.xlsx?${ctx.filters.params()}`,
      `osv_${ctx.filters.dateFrom()}_${ctx.filters.dateTo()}.xlsx`),
  };
}

// Склад: остатки и обороты по складам и номенклатуре — количество и сумма; любая цифра раскрывается до проводок.
import { api } from "../api.js";
import { money, quantity } from "../format.js";
import { el, makeTable } from "../ui.js";
import { moneyCell, openDrill } from "./common.js";

// колонка → условия расшифровки: остаток на начало / приход (дебет) / расход (кредит) / остаток на конец
const PARTS = {
  opening: { period: "opening", title: "остаток на начало" },
  in: { period: "turnover", side: "debit", title: "приход" },
  out: { period: "turnover", side: "credit", title: "расход" },
  closing: { period: "closing", title: "остаток на конец" },
};

function toTree(rows) {
  const parents = rows.filter((r) => !r.parent).map((r) => ({ ...r, _children: [] }));
  const byKey = new Map(parents.map((p) => [p.key, p]));
  for (const r of rows) if (r.parent) byKey.get(r.parent)?._children.push({ ...r });
  return parents;
}

export function stockReport({ box, ctx }) {
  let accountId = "";
  let warehouseId = "";
  const accountSelect = el("select", { class: "inline-select", "aria-label": "Счёт склада" });
  const warehouseSelect = el("select", { class: "inline-select", "aria-label": "Склад" });
  const tableBox = el("div");
  box.replaceChildren(
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "Счёт ", accountSelect), el("label", { class: "inline" }, "Склад ", warehouseSelect)),
    el("p", { class: "muted" }, "Количественные счета (10, 41, 43): приход — по дебету, расход — по кредиту; перемещение между "
      + "складами — расход одного и приход другого. Нажмите ⊞ у склада, чтобы увидеть номенклатуру; щёлкните по цифре — откроются проводки."),
    tableBox,
  );

  const qtyCell = (c) => {
    const v = c.getValue();
    const text = Number(v) ? quantity(v) : "";
    return text && c.getRow().getData().key !== "total" ? `<span class="drill">${text}</span>` : text;
  };
  const sumCell = (c) => (c.getRow().getData().key === "total" ? (Number(c.getValue()) ? money(c.getValue()) : "") : moneyCell(c));
  const group = (part, title) => ({
    title,
    columns: [
      { title: "Кол-во", field: `${part}_qty`, hozAlign: "right", minWidth: 80, headerSort: false, formatter: qtyCell },
      { title: "Сумма", field: `${part}_sum`, hozAlign: "right", minWidth: 105, headerSort: false, formatter: sumCell },
    ],
  });
  const table = makeTable(tableBox, {
    dataTree: true, dataTreeStartExpanded: true, dataTreeChildIndent: 18, index: "key",
    columnDefaults: { headerWordWrap: true },
    columns: [
      { title: "Склад / номенклатура", field: "title", minWidth: 220, widthGrow: 3, headerSort: false },
      { title: "Счёт", field: "account", width: 70, headerSort: false },
      { title: "Ед.", field: "unit", width: 60, headerSort: false },
      group("opening", "Остаток на начало"), group("in", "Приход"), group("out", "Расход"), group("closing", "Остаток на конец"),
    ],
  });
  table.on("cellClick", (_e, c) => {
    const r = c.getRow().getData();
    const [part] = c.getField().split("_");
    if (!PARTS[part] || !r.drill || Number(c.getValue()) === 0) return;
    const p = PARTS[part];
    openDrill({
      org: ctx.org(), ref: ctx.ref(), filters: ctx.filters, onChanged: () => ctx.reload(),
      title: `${r.title}${r.account ? `, счёт ${r.account}` : ""}, ${p.title}`,
      extra: { period: p.period, ...(p.side ? { side: p.side } : {}), account_id: r.drill.account_id, a: r.drill.a },
    });
  });

  const params = () => ctx.filters.params({ ...(accountId ? { account_id: accountId } : {}), ...(warehouseId ? { warehouse_id: warehouseId } : {}) });

  async function load() {
    const data = await api.get(`/api/organizations/${ctx.org().id}/reports/stock?${params()}`);
    accountSelect.replaceChildren(el("option", { value: "" }, "Все (10, 41, 43)"),
      ...data.accounts.map((a) => el("option", { value: a.id, selected: String(a.id) === accountId }, `${a.code} ${a.name}`)));
    warehouseSelect.replaceChildren(el("option", { value: "" }, "Все склады"),
      ...ctx.ref().warehouses.map((w) => el("option", { value: w.id, selected: String(w.id) === warehouseId }, w.name)));
    await table.setData([...toTree(data.rows), { key: "total", title: "Итого", account: "", unit: "", ...data.total }]);
    table.getRow("total")?.getElement().classList.add("row-total");
  }

  accountSelect.addEventListener("change", () => { accountId = accountSelect.value; ctx.reload(); });
  warehouseSelect.addEventListener("change", () => { warehouseId = warehouseSelect.value; ctx.reload(); });

  return {
    ready: table.ready,
    load,
    reset: async () => { accountId = ""; warehouseId = ""; },
    excel: () => api.download(`/api/organizations/${ctx.org().id}/reports/stock.xlsx?${params()}`,
      `stock_${ctx.filters.dateFrom()}_${ctx.filters.dateTo()}.xlsx`),
  };
}

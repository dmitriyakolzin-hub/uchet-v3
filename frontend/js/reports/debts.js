// Дебиторская и кредиторская задолженность на конец периода: контрагенты → счета и договоры.
import { api } from "../api.js";
import { money } from "../format.js";
import { el, makeTable } from "../ui.js";
import { moneyCell, openDrill } from "./common.js";

const TITLES = {
  receivable: "дебиторская задолженность", advance_paid: "авансы выданные", payable: "кредиторская задолженность",
  advance_received: "авансы полученные", opening: "сальдо на начало", saldo: "сальдо на конец",
};
// У контрагента в целом расшифровываются только сальдо: колонки по знаку собраны из разных договоров
const PARENT_DRILL = new Set(["opening", "saldo"]);

function toTree(rows) {
  const parents = rows.filter((r) => !r.parent).map((r) => ({ ...r, _children: [] }));
  const byKey = new Map(parents.map((p) => [p.key, p]));
  for (const r of rows) if (r.parent) byKey.get(r.parent)?._children.push({ ...r });
  for (const p of parents) if (!p._children.length) delete p._children;
  return parents;
}

export function debtsReport({ box, ctx }) {
  const tableBox = el("div");
  box.replaceChildren(
    el("p", { class: "muted" }, "Задолженность на конец периода по счетам 60, 62, 66, 67, 71, 76. Нажмите ⊞, чтобы увидеть "
      + "счета и договоры контрагента; щёлкните по сумме — откроются проводки. При фильтре по ЦФО отчёт пуст: у расчётов нет ЦФО."),
    tableBox,
  );
  const cell = (field, title) => ({
    title, field, hozAlign: "right", minWidth: 110, widthGrow: 1, headerSort: false,
    formatter: (c) => {
      const r = c.getRow().getData();
      const drillable = r.key !== "total" && (r.parent || PARENT_DRILL.has(field));
      return drillable ? moneyCell(c) : (Number(c.getValue()) ? money(c.getValue()) : "");
    },
  });
  const table = makeTable(tableBox, {
    dataTree: true, dataTreeStartExpanded: false, dataTreeChildIndent: 18, index: "key",
    columnDefaults: { headerWordWrap: true },
    columns: [
      { title: "Контрагент / договор", field: "title", minWidth: 220, widthGrow: 3, headerSort: false },
      { title: "Счёт", field: "account", width: 80, headerSort: false },
      cell("receivable", "Дебиторка"), cell("advance_paid", "Авансы выданные"),
      cell("payable", "Кредиторка"), cell("advance_received", "Авансы полученные"),
      cell("opening", "Сальдо на начало"), cell("saldo", "Сальдо на конец"),
    ],
  });
  table.on("cellClick", (_e, c) => {
    const r = c.getRow().getData();
    const field = c.getField();
    if (!TITLES[field] || r.key === "total" || Number(c.getValue()) === 0) return;
    if (!r.parent && !PARENT_DRILL.has(field)) return;
    openDrill({
      org: ctx.org(), ref: ctx.ref(), filters: ctx.filters, onChanged: () => ctx.reload(),
      title: `${r.title}${r.account ? `, счёт ${r.account}` : ""}, ${TITLES[field]}`,
      extra: { period: field === "opening" ? "opening" : "closing", account_id: r.drill.account_id, a: r.drill.a },
    });
  });

  async function load() {
    const data = await api.get(`/api/organizations/${ctx.org().id}/reports/debts?${ctx.filters.params()}`);
    await table.setData([...toTree(data.rows), { key: "total", title: "Итого", account: "", ...data.total }]);
    table.getRow("total")?.getElement().classList.add("row-total");
  }

  return {
    ready: table.ready,
    load,
    reset: async () => {},
    excel: () => api.download(`/api/organizations/${ctx.org().id}/reports/debts.xlsx?${ctx.filters.params()}`,
      `debts_${ctx.filters.dateTo()}.xlsx`),
  };
}

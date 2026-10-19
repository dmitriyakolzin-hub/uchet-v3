// ДДС: остаток на начало, потоки по видам деятельности и статьям, остаток на конец; колонки по месяцам.
import { api } from "../api.js";
import { el, makeTable } from "../ui.js";
import { moneyCell, openDrill } from "./common.js";

const BOLD = new Set(["title", "section", "balance", "total"]);

function escape(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
}

/** Отчёт с колонками по месяцам (ДДС, ОПиУ): строки приходят с сервера готовыми. */
export function columnsReport({ box, ctx, path, hint, fileName, onDetails, firstTitle = "Статья" }) {
  const tableBox = el("div");
  box.replaceChildren(el("p", { class: "muted" }, hint), tableBox);
  let data = null;

  const table = makeTable(tableBox, {
    index: "key",
    columns: [{ title: "Статья", field: "title" }],
    rowFormatter: (row) => {
      const r = row.getData();
      row.getElement().classList.toggle("row-total", BOLD.has(r.kind));
      row.getElement().classList.toggle("row-key", r.kind === "total" || r.kind === "balance");
    },
  });
  table.on("cellClick", (_e, cell) => {
    const r = cell.getRow().getData();
    const col = data?.columns.find((c) => c.key === cell.getField());
    if (!col || !r.drill || Number(cell.getValue() ?? 0) === 0) return;
    if (r.drill.details) {
      onDetails?.(r, col);
      return;
    }
    const money = data.money_account_ids;
    const extra = {
      period: r.drill.period, a: r.drill.a, opening: r.drill.opening,
      date_from: col.date_from, date_to: col.date_to,
      ...(money.length ? { account_id: money } : {}),
      ...(r.drill.exclude_corr ? { exclude_corr_account_id: money } : {}),
      ...(r.drill.account_id ? { account_id: r.drill.account_id } : {}),
      ...(r.drill.side ? { side: r.drill.side } : {}),
    };
    openDrill({ org: ctx.org(), ref: ctx.ref(), filters: ctx.filters, extra,
      title: `${r.title}, ${col.title.toLowerCase()}`, onChanged: () => ctx.reload() });
  });

  async function load() {
    data = await api.get(`/api/organizations/${ctx.org().id}/reports/${path}?${ctx.filters.params()}`);
    table.setColumns([
      { title: firstTitle, field: "title", minWidth: 260, widthGrow: 3, headerSort: false, frozen: true,
        formatter: (c) => {
          const r = c.getRow().getData();
          return `<span style="padding-left:${r.level * 18}px">${escape(r.title)}</span>`;
        } },
      ...data.columns.map((c) => ({
        title: c.title, field: c.key, hozAlign: "right", minWidth: 120, headerSort: false,
        cssClass: c.key === "total" ? "strong" : "", formatter: (cell) => {
          const r = cell.getRow().getData();
          return r.drill ? moneyCell(cell) : moneyCell(cell).replace('class="drill"', "");
        },
      })),
    ]);
    await table.setData(data.rows.map((r) => ({ ...r, ...r.values })));
  }

  return {
    ready: table.ready,
    load,
    reset: async () => {},
    excel: () => api.download(`/api/organizations/${ctx.org().id}/reports/${path}.xlsx?${ctx.filters.params()}`,
      `${fileName}_${ctx.filters.dateFrom()}_${ctx.filters.dateTo()}.xlsx`),
  };
}

export function ddsReport({ box, ctx }) {
  return columnsReport({
    box, ctx, path: "dds", fileName: "dds",
    hint: "Деньги — счета 50, 51, 52, 55. Переводы между своими счетами не показываются. "
      + "Щёлкните по сумме — откроются проводки, из которых она сложилась. При фильтре по ЦФО видна только касса.",
  });
}

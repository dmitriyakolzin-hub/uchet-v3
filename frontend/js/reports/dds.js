// ДДС: остаток на начало, потоки по видам деятельности и статьям, остаток на конец; колонки по месяцам.
import { api } from "../api.js";
import { tableNotes } from "../notes.js";
import { el, makeTable } from "../ui.js";
import { compareBar, extraHtml, moneyCell, nestRows, openDrill, TREE_OPTIONS } from "./common.js";

const BOLD = new Set(["title", "section", "balance", "total"]);

function escape(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
}

/** Отчёт с колонками по месяцам (ДДС, ОПиУ): строки приходят с сервера готовыми. */
export function columnsReport({ box, ctx, path, hint, fileName, onDetails, firstTitle = "Статья", compare = false }) {
  const tableBox = el("div");
  // «Сравнить» и «Косты» — в ДДС и ОПиУ (шаг 17.1)
  const bar = compare ? compareBar(() => load()) : null;
  box.replaceChildren(el("p", { class: "muted" }, hint), ...(bar ? [bar.node] : []), tableBox);
  let data = null;
  const extra = () => (bar ? bar.params() : {});

  const table = makeTable(tableBox, {
    index: "key",
    ...TREE_OPTIONS,
    columns: [{ title: "Статья", field: "title" }],
    rowFormatter: (row) => {
      const r = row.getData();
      row.getElement().classList.toggle("row-total", BOLD.has(r.kind));
      row.getElement().classList.toggle("row-group", r.kind === "item_group");
      row.getElement().classList.toggle("row-key", r.kind === "total" || r.kind === "balance");
    },
  });
  // примечания к цифрам (правый щелчок / долгое нажатие)
  const notes = tableNotes(table, { report: path, ctx, rowTitle: (r) => r.title,
    columnTitle: (field) => data?.columns.find((c) => c.key === field)?.title ?? field });
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
      period: r.drill.period, a: r.drill.a, a_tree: r.drill.a_tree, opening: r.drill.opening,
      date_from: col.date_from, date_to: col.date_to,
      ...(money.length ? { account_id: money } : {}),
      ...(r.drill.exclude_corr ? { exclude_corr_account_id: money } : {}),
      ...(r.drill.exclude_corr_ids?.length ? { exclude_corr_account_id: r.drill.exclude_corr_ids } : {}),
      ...(r.drill.account_id ? { account_id: r.drill.account_id } : {}),
      ...(r.drill.side ? { side: r.drill.side } : {}),
    };
    openDrill({ org: ctx.org(), ref: ctx.ref(), filters: ctx.filters, extra,
      title: `${r.title}, ${col.title.toLowerCase()}`, onChanged: () => ctx.reload() });
  });

  async function load() {
    data = await api.get(`/api/organizations/${ctx.org().id}/reports/${path}?${ctx.filters.params(extra())}`);
    table.setColumns([
      { title: firstTitle, field: "title", minWidth: 260, widthGrow: 3, headerSort: false, frozen: true,
        formatter: (c) => {
          const r = c.getRow().getData();
          return `<span style="padding-left:${r._indent ?? r.level * 18}px">${escape(r.title)}</span>`;
        } },
      ...data.columns.map((c) => ({
        title: c.title, field: c.key, hozAlign: "right", minWidth: 120, headerSort: false,
        cssClass: c.key === "total" ? "strong" : "", formatter: (cell) => {
          const r = cell.getRow().getData();
          const main = r.drill ? moneyCell(cell) : moneyCell(cell).replace('class="drill"', "");
          if (!r.values || !Object.keys(r.values).length) return main;
          return main + extraHtml(cell.getValue(), r.compare?.[c.key], r.better,
            r.kind === "balance" ? null : data.revenue?.[c.key]);
        },
      })),
    ]);
    await table.setData(nestRows(data.rows.map((r) => ({ ...r, ...r.values }))));
    await notes.refresh();
  }

  return {
    ready: table.ready,
    load,
    reset: async () => {},
    notes,
    excel: () => api.download(`/api/organizations/${ctx.org().id}/reports/${path}.xlsx?${ctx.filters.params(extra())}`,
      `${fileName}_${ctx.filters.dateFrom()}_${ctx.filters.dateTo()}.xlsx`),
  };
}

export function ddsReport({ box, ctx }) {
  return columnsReport({
    box, ctx, path: "dds", fileName: "dds", compare: true,
    hint: "Деньги — счета 50, 51, 52, 55. Переводы между своими счетами не показываются. "
      + "Щёлкните по сумме — откроются проводки, из которых она сложилась. При фильтре по ЦФО видна только касса. "
      + "Правый щелчок по цифре (на телефоне — долгое нажатие) — примечание. «Сравнить» — под цифрой синим сумма для сравнения и отклонение (зелёное — лучше, красное — хуже); «Косты» — доля к выручке ОПиУ того же месяца.",
  });
}

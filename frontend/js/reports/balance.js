// Управленческий баланс на начало и конец периода. Дебиторка и кредиторка раскрываются по контрагентам.
import { api } from "../api.js";
import { money } from "../format.js";
import { el, makeTable, toast } from "../ui.js";
import { moneyCell, openDrill } from "./common.js";
import { columnsReport } from "./dds.js";

export function balanceReport({ box, ctx }) {
  return columnsReport({
    box, ctx, path: "balance", fileName: "balance", firstTitle: "Показатель",
    hint: "Баланс на день перед началом периода и на конец периода. Прибыль, пока период не закрыт, — строка "
      + "«Прибыль (убыток) текущего периода». Расчёты с контрагентами показаны развёрнуто: кто должен нам — в активе, "
      + "кому должны мы — в пассиве. Щёлкните по сумме — откроется расшифровка.",
    onDetails: (row, col) => openDetails(ctx, row, col),
  });
}

async function openDetails(ctx, row, col) {
  const org = ctx.org();
  let data;
  try {
    const params = ctx.filters.params({ line: row.key, date_from: col.date_to, date_to: col.date_to });
    data = await api.get(`/api/organizations/${org.id}/reports/balance/details?${params}`);
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const tableBox = el("div");
  const dialog = el("dialog", { class: "dialog wide" },
    el("h2", {}, `${data.title} — ${col.title.toLowerCase()}`),
    el("p", { class: "muted" }, `Итого: ${money(data.total)}. Щёлкните по сумме — откроются проводки.`),
    tableBox,
    el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")),
  );
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  const table = makeTable(tableBox, {
    maxHeight: "60vh",
    columns: [
      { title: "Счёт", field: "account", width: 90 },
      { title: "Контрагент, договор, сотрудник", field: "name", widthGrow: 3 },
      { title: "Сумма", field: "amount", width: 150, hozAlign: "right", formatter: moneyCell },
    ],
    data: data.rows,
  });
  table.on("cellClick", (_e, cell) => {
    if (cell.getField() !== "amount") return;
    const r = cell.getRow().getData();
    openDrill({
      org, ref: ctx.ref(), filters: ctx.filters, onChanged: () => ctx.reload(),
      title: `счёт ${r.account}, ${r.name}, ${col.title.toLowerCase()}`,
      extra: {
        period: "closing", date_from: col.date_to, date_to: col.date_to, account_id: r.account_id,
        a: Object.entries(r.analytics).map(([k, v]) => `${k}:${v ?? "none"}`),
      },
    });
  });
}

// Общее для отчётов: панель фильтров и расшифровка цифры до проводок.
import { api } from "../api.js";
import { date, money, monthBounds, sumMoney } from "../format.js";
import { openOperation } from "../opform.js";
import { el, makeTable, toast } from "../ui.js";

const STATUS_TITLES = { draft: "Черновик", posted: "Проведена", cancelled: "Аннулирована" };

/** Деньги в ячейке отчёта: ноль — пусто; ячейка с суммой кликабельна (класс drill). */
export function moneyCell(cell) {
  const v = cell.getValue();
  if (v === null || v === undefined || Number(v) === 0) return "";
  return `<span class="drill">${money(v)}</span>`;
}

/**
 * Панель фильтров: юрлицо, период, ЦФО, «с черновиками». onChange вызывается при изменении.
 * filters.params() — URLSearchParams для запросов отчёта; filters.describe() — для заголовка расшифровки.
 */
export function reportFilters({ ref, onChange, onExcel }) {
  const [monthStart, monthEnd] = monthBounds();
  const dateFrom = el("input", { type: "date", "aria-label": "С даты", value: monthStart });
  const dateTo = el("input", { type: "date", "aria-label": "По дату", value: monthEnd });
  dateFrom.value = monthStart;
  dateTo.value = monthEnd;
  const entity = el("select", { "aria-label": "Юрлицо" });
  const cfo = el("select", { "aria-label": "ЦФО" });
  const drafts = el("input", { type: "checkbox", "aria-label": "С черновиками" });

  function fillLists(r) {
    entity.replaceChildren(el("option", { value: "" }, "Все юрлица"),
      ...r.entities.map((e) => el("option", { value: e.id }, e.short_name)));
    const cfos = [...r.cfos].sort((a, b) => r.cfoLabel.get(a.id).localeCompare(r.cfoLabel.get(b.id)));
    cfo.replaceChildren(el("option", { value: "" }, "Все ЦФО"),
      ...cfos.map((c) => el("option", { value: c.id }, r.cfoLabel.get(c.id))));
  }
  fillLists(ref);
  for (const input of [dateFrom, dateTo, entity, cfo, drafts]) input.addEventListener("change", () => onChange());

  const box = el("div", { class: "filters" },
    dateFrom, dateTo, entity, cfo,
    el("label", { class: "inline check-inline" }, drafts, "с черновиками"),
    onExcel ? el("button", { type: "button", class: "secondary push-right", onclick: () => onExcel() }, "Скачать в Excel") : null,
  );

  return {
    box,
    setRef(r) { fillLists(r); },
    valid() {
      if (!dateFrom.value || !dateTo.value) return false;
      if (dateFrom.value > dateTo.value) { toast("Начало периода позже его конца", "error"); return false; }
      return true;
    },
    params(extra = {}) {
      const p = new URLSearchParams({ date_from: dateFrom.value, date_to: dateTo.value });
      if (entity.value) p.set("legal_entity_id", entity.value);
      if (cfo.value) p.set("cfo_id", cfo.value);
      if (drafts.checked) p.set("drafts", "true");
      for (const [k, v] of Object.entries(extra)) {
        if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
        else if (v !== null && v !== undefined) p.set(k, v);
      }
      return p;
    },
    period() { return `${date(dateFrom.value)} — ${date(dateTo.value)}`; },
    dateFrom: () => dateFrom.value,
    dateTo: () => dateTo.value,
  };
}

/** Окно расшифровки: проводки, из которых сложилась цифра; итог равен цифре отчёта. */
export async function openDrill({ org, ref, filters, extra, title, onChanged }) {
  let data;
  try {
    data = await api.get(`/api/organizations/${org.id}/reports/lines?${filters.params(extra)}`);
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const tableBox = el("div");
  const net = sumMoney([data.debit, `-${data.credit}`]); // обе суммы неотрицательны
  const dialog = el("dialog", { class: "dialog wide" },
    el("h2", {}, `Расшифровка: ${title}`),
    el("p", { class: "muted" },
      `Проводок: ${data.count}. Дебет: ${money(data.debit)}, кредит: ${money(data.credit)}, разница: ${money(net)}.`,
      data.count > data.limit ? ` Показаны первые ${data.limit}.` : ""),
    tableBox,
    el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")),
  );
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  const table = makeTable(tableBox, {
    maxHeight: "60vh",
    columns: [
      { title: "Дата", field: "op_date", width: 100, formatter: (c) => date(c.getValue()) },
      { title: "№", field: "number", width: 65 },
      { title: "Статус", field: "status", width: 110, formatter: (c) => STATUS_TITLES[c.getValue()] },
      { title: "Счёт", field: "account", width: 75 },
      { title: "Корр.", field: "corr_account", width: 75 },
      { title: "Аналитика", field: "analytics", widthGrow: 2, tooltip: true },
      { title: "Описание", field: "description", widthGrow: 2, tooltip: true },
      { title: "Дебет", field: "debit", width: 120, hozAlign: "right", formatter: (c) => money(c.getValue()) },
      { title: "Кредит", field: "credit", width: 120, hozAlign: "right", formatter: (c) => money(c.getValue()) },
    ],
    data: data.rows,
  });
  table.on("rowClick", async (_e, row) => {
    const op = await api.get(`/api/operations/${row.getData().operation_id}`);
    openOperation({ ref, org, op, perms: org.permissions, onChanged });
  });
}

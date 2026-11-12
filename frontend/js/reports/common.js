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
 * Панель фильтров: юрлицо, период, ЦФО, ЦФУ (если есть), «с черновиками». onChange вызывается при изменении.
 * filters.params() — URLSearchParams для запросов отчёта; filters.describe() — для заголовка расшифровки.
 */
export function reportFilters({ ref, scope = null, onChange, onExcel }) {
  const [monthStart, monthEnd] = monthBounds();
  const dateFrom = el("input", { type: "date", "aria-label": "С даты", value: monthStart });
  const dateTo = el("input", { type: "date", "aria-label": "По дату", value: monthEnd });
  dateFrom.value = monthStart;
  dateTo.value = monthEnd;
  const entity = el("select", { "aria-label": "Юрлицо" });
  const cfo = el("select", { "aria-label": "ЦФО" });
  const executor = el("select", { "aria-label": "ЦФУ" });
  const drafts = el("input", { type: "checkbox", "aria-label": "С черновиками" });

  function fillLists(r, branchRoot = scope) {
    scope = branchRoot;
    entity.replaceChildren(el("option", { value: "" }, "Все юрлица"),
      ...r.entities.map((e) => el("option", { value: e.id }, e.short_name)));
    // руководителю ЦФО — только его ветка (сервер всё равно ограничит отчёт веткой)
    const inBranch = (c) => !scope || c.path.split(".").includes(String(scope));
    const cfos = r.cfos.filter(inBranch).sort((a, b) => r.cfoLabel.get(a.id).localeCompare(r.cfoLabel.get(b.id)));
    cfo.replaceChildren(el("option", { value: "" }, scope ? "Вся моя ветка" : "Все ЦФО"),
      ...cfos.map((c) => el("option", { value: c.id }, r.cfoLabel.get(c.id))));
    const executors = [...(r.executors ?? [])].sort((a, b) => r.executorLabel.get(a.id).localeCompare(r.executorLabel.get(b.id)));
    executor.replaceChildren(el("option", { value: "" }, "Все ЦФУ"),
      ...executors.map((e) => el("option", { value: e.id }, r.executorLabel.get(e.id))));
    executor.hidden = !executors.length;
  }
  fillLists(ref);
  for (const input of [dateFrom, dateTo, entity, cfo, executor, drafts]) input.addEventListener("change", () => onChange());

  const box = el("div", { class: "filters" },
    dateFrom, dateTo, entity, cfo, executor,
    el("label", { class: "inline check-inline" }, drafts, "с черновиками"),
    onExcel ? el("button", { type: "button", class: "secondary push-right", onclick: () => onExcel() }, "Скачать в Excel") : null,
  );

  return {
    box,
    setRef(r, branchRoot = null) { fillLists(r, branchRoot); },
    // поставить фильтры (переход к примечанию): период, юрлицо, ЦФО, ЦФУ; корень ветки руководителя — «вся ветка»
    set({ date_from: from, date_to: to, legal_entity_id: le, cfo_id: c, executor_id: ex }) {
      if (from) dateFrom.value = from;
      if (to) dateTo.value = to;
      const pick = (select, value) => {
        select.value = value && [...select.options].some((o) => o.value === String(value)) ? String(value) : "";
      };
      pick(entity, le);
      pick(cfo, c && String(c) !== String(scope) ? c : null);
      pick(executor, ex);
    },
    valid() {
      if (!dateFrom.value || !dateTo.value) return false;
      if (dateFrom.value > dateTo.value) { toast("Начало периода позже его конца", "error"); return false; }
      return true;
    },
    params(extra = {}) {
      const p = new URLSearchParams({ date_from: dateFrom.value, date_to: dateTo.value });
      if (entity.value) p.set("legal_entity_id", entity.value);
      if (cfo.value) p.set("cfo_id", cfo.value);
      if (executor.value) p.set("executor_id", executor.value);
      if (drafts.checked) p.set("drafts", "true");
      for (const [k, v] of Object.entries(extra)) {
        if (Array.isArray(v)) v.forEach((x) => p.append(k, x));
        else if (v !== null && v !== undefined) p.set(k, v);
      }
      return p;
    },
    // юрлицо, ЦФО и ЦФУ отчёта — те же ограничения для окна плановых операций
    scope() {
      const result = {};
      if (entity.value) result.legal_entity_id = entity.value;
      if (cfo.value) result.cfo_id = cfo.value;
      if (executor.value) result.executor_id = executor.value;
      return result;
    },
    period() { return `${date(dateFrom.value)} — ${date(dateTo.value)}`; },
    dateFrom: () => dateFrom.value,
    dateTo: () => dateTo.value,
  };
}

/** Статья строки отчёта для окна плановых операций: из условий расшифровки (a или a_tree — «pl_item:12»). */
export function planArticle(drill) {
  const items = [].concat(drill?.a ?? [], drill?.a_tree ?? []);
  const item = items.find((x) => /^(pl_item|cash_flow_item):\d+$/.test(x));
  return item ? Number(item.split(":")[1]) : null;
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

/** Строки отчёта деревом статей (docs/TZ.md, раздел 19): строка с parent — внутри строки-группы статей.
 *  Возвращает строки верхнего уровня; _indent — отступ строки, не считая отступа дерева. */
export function nestRows(rows) {
  const byKey = new Map(rows.map((r) => [r.key, r]));
  const roots = [];
  for (const r of rows) {
    const parent = r.parent ? byKey.get(r.parent) : null;  // группа всегда идёт раньше своих подстатей
    r._depth = parent ? parent._depth + 1 : 0;
    r._indent = (r.level - r._depth) * 18;
    if (parent) (parent._children ??= []).push(r);
    else roots.push(r);
  }
  return roots;
}

export const TREE_OPTIONS = { dataTree: true, dataTreeStartExpanded: false, dataTreeChildIndent: 18,
  dataTreeElementColumn: "title" };

// --- Сравнение с другим периодом и «косты» (docs/TZ.md, раздел 27, «Детали 17.1») ---
const COMPARE_MODES = [["", "Без сравнения"], ["year", "С прошлым годом"], ["month", "С прошлым месяцем"],
  ["avg6", "Со средним за 6 мес."]];
const COMPARE_KEY = "reports.compare";

function loadCompare() {
  try {
    return { compare: "", costs: false, ...JSON.parse(localStorage.getItem(COMPARE_KEY) || "{}") };
  } catch {
    return { compare: "", costs: false };
  }
}

/** Переключатели «Сравнить» и «Косты»; состояние общее для отчётов и запоминается в браузере. */
export function compareBar(onChange) {
  const state = loadCompare();
  const select = el("select", { "aria-label": "Сравнить" },
    ...COMPARE_MODES.map(([v, t]) => el("option", { value: v, selected: v === state.compare }, t)));
  const costs = el("input", { type: "checkbox", "aria-label": "Косты", checked: state.costs });
  const save = () => {
    state.compare = select.value;
    state.costs = costs.checked;
    try {
      localStorage.setItem(COMPARE_KEY, JSON.stringify(state));
    } catch { /* без памяти браузера — только на эту страницу */ }
    onChange();
  };
  select.addEventListener("change", save);
  costs.addEventListener("change", save);
  const node = el("div", { class: "filters compare-bar" },
    el("label", {}, "Сравнить ", select),
    el("label", { class: "check", title: "Доля каждой цифры к выручке того же месяца" }, costs, " Косты (% к выручке)"));
  return {
    node,
    params: () => ({ compare: select.value || null, costs: costs.checked ? "true" : null }),
  };
}

/** Отклонение в процентах — как в Excel: изменение суммы по модулю (для сумм одного знака). */
function changePct(value, base) {
  const v = Number(value || 0);
  const b = Number(base || 0);
  if (!b) return null;
  return (v >= 0) === (b > 0) ? (Math.abs(v) - Math.abs(b)) / Math.abs(b) * 100 : (v - b) / Math.abs(b) * 100;
}

const pctText = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} %`;

/** Строки под суммой ячейки: сумма для сравнения и отклонение (зелёное — лучше, красное — хуже), % выручки. */
export function extraHtml(value, base, better, revenue) {
  let html = "";
  if (base !== undefined && base !== null) {
    const pct = changePct(value, base);
    const diff = Number(value || 0) - Number(base || 0);
    const tone = !better || !diff ? "" : diff * better > 0 ? " good" : " bad";
    html += `<div class="cmp">${Number(base) ? money(base) : "0"}${pct === null ? "" : ` · <span class="pct${tone}">${pctText(pct)}</span>`}</div>`;
  }
  if (revenue !== undefined && revenue !== null && Number(value || 0)) {
    const r = Number(revenue);
    html += `<div class="share">${r > 0 ? `${(Math.abs(Number(value)) / r * 100).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} % выручки` : "нет выручки"}</div>`;
  }
  return html;
}

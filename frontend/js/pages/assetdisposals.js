// Выбытие имущества (docs/TZ.md, раздел 28, «Детали 19.6»): предложение (продажа, списание, гибель / утрата),
// согласование ФД, отмена; вкладка «Выбытие».
import { api } from "../api.js";
import { date as ruDate, money, parseNumber } from "../format.js";
import { el, instruction, openForm, toast } from "../ui.js";

const STATUS_FILTER = [["pending", "На согласовании"], ["", "Все"], ["done", "Выбыло"], ["rejected,cancelled,undone", "Отклонённые и отменённые"]];
const KINDS = [["writeoff", "Списание"], ["sale", "Продажа"], ["loss", "Гибель / утрата"]];
const idOrNull = (v) => (v ? Number(v) : null);
const options = (items, label, empty) => [["", empty], ...items.map((i) => [String(i.id), label(i)])];

/** Форма выбытия отмеченных единиц. */
export async function disposalForm({ org, ref, items, onDone }) {
  let counterparties = [];
  let contracts = [];
  try {
    [counterparties, contracts] = await Promise.all([api.get(`/api/organizations/${org.id}/counterparties`),
      api.get(`/api/organizations/${org.id}/contracts`)]);
  } catch { /* без покупателей — только списание */ }
  const contractOptions = (cp) => options(contracts.filter((c) => String(c.counterparty_id) === cp && c.is_active),
    (c) => c.name || `№ ${c.number}`, "— без договора —");
  const dialog = openForm({
    title: `Выбытие: ${items.length === 1 ? `${items[0].number} ${items[0].name}` : `${items.length} ед.`}`,
    fields: [
      { name: "kind", label: "Как выбывает", type: "select", options: KINDS },
      { name: "on_date", label: "Дата", type: "date", required: true },
      { name: "reason", label: "Причина / основание", type: "textarea", hint: "Для списания и утраты обязательна" },
      { name: "counterparty_id", label: "Покупатель", type: "select", options: options(counterparties.filter((c) => c.is_active), (c) => c.name, "— выберите —") },
      { name: "contract_id", label: "Договор", type: "select", options: contractOptions("") },
      { name: "price", label: "Цена за единицу, ₽ (с НДС)", placeholder: "0,00", hint: "Разные цены — оформите отдельными актами" },
      { name: "vat_rate", label: "НДС", type: "select", options: [["0", "без НДС"], ["5", "5 %"], ["7", "7 %"], ["10", "10 %"], ["20", "20 %"], ["22", "22 %"]] },
      { name: "employee_id", label: "Виновное лицо", type: "select", options: options(ref.employees, (e) => e.full_name, "— не указано —") },
    ],
    values: { kind: "writeoff", on_date: new Date().toISOString().slice(0, 10), vat_rate: "0" },
    submitText: "На согласование ФД",
    onSubmit: async (v) => {
      const sale = v.kind === "sale";
      const price = sale && v.price ? parseNumber(v.price) : null;
      const act = await api.post(`/api/organizations/${org.id}/asset-disposals`, {
        ids: items.map((i) => i.id), kind: v.kind, on_date: v.on_date, reason: v.reason ?? "",
        counterparty_id: sale ? idOrNull(v.counterparty_id) : null, contract_id: sale ? idOrNull(v.contract_id) : null,
        vat_rate: sale ? Number(v.vat_rate) : 0, employee_id: v.kind === "loss" ? idOrNull(v.employee_id) : null,
        prices: price ? Object.fromEntries(items.map((i) => [i.id, price])) : {},
      });
      toast(`Акт выбытия № ${act.number} — на согласовании ФД`);
      await onDone?.();
    },
  });
  const form = dialog.querySelector("form");
  const nodes = (name) => {
    const input = form.elements[name];
    return [form.querySelector(`label[for="${input.id}"]`), input,
      input.nextElementSibling?.tagName === "SMALL" ? input.nextElementSibling : null].filter(Boolean);
  };
  const toggle = () => {
    const kind = form.elements.kind.value;
    ["counterparty_id", "contract_id", "price", "vat_rate"].forEach((n) => nodes(n).forEach((x) => { x.hidden = kind !== "sale"; }));
    nodes("employee_id").forEach((x) => { x.hidden = kind !== "loss"; });
  };
  form.elements.kind.addEventListener("change", toggle);
  form.elements.counterparty_id.addEventListener("change", () => {
    form.elements.contract_id.replaceChildren(...contractOptions(form.elements.counterparty_id.value).map(([v, t]) => el("option", { value: v }, t)));
  });
  toggle();
}

/** Вкладка «Выбытие». */
export function assetDisposalsTab(box, ctx) {
  let filter = "pending";
  const listBox = el("div");

  function render() {
    const status = el("select", { "aria-label": "Статус выбытия" },
      ...STATUS_FILTER.map(([v, t]) => el("option", { value: v, selected: v === filter }, t)));
    status.addEventListener("change", () => { filter = status.value; load(); });
    box.replaceChildren(
      instruction("Выбытие: отметьте единицы в «Реестре» → «Выбытие…» — продажа, списание или гибель / утрата. "
        + "Предлагают инженер, оператор АХО, бухгалтер; согласует финансовый директор — тогда единицы выбывают и создаются "
        + "проводки. Основное средство — как в «Основных средствах»: амортизация закрывается (02 → 01), остаток — в прочие "
        + "расходы (91.02), при продаже — выручка (62.01 → 91.01) и НДС. Малоценка и имущество без учёта: продажа — только "
        + "выручка, списание и утрата — без проводок. Ошибочное выбытие ФД может отменить — проводки удалятся."),
      el("div", { class: "filters" }, el("label", { class: "inline" }, "Показать ", status)),
      listBox,
    );
  }

  async function act(d, action, body) {
    try {
      await api.post(`/api/asset-disposals/${d.id}/${action}`, body);
      await load();
      await ctx.reload?.();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function load() {
    let rows;
    try {
      rows = await api.get(`/api/organizations/${ctx.org().id}/asset-disposals${filter ? `?status=${filter}` : ""}`);
    } catch (err) {
      listBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    if (!rows.length) {
      listBox.replaceChildren(el("p", { class: "muted" }, "Актов выбытия нет."));
      return;
    }
    listBox.replaceChildren(el("table", { class: "recon asset-disposals" },
      el("thead", {}, el("tr", {}, ...["№", "Дата", "Как", "Что", "Сумма", "Основание", "Статус", ""].map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows.map((d) => el("tr", { "data-number": d.number },
        el("td", {}, String(d.number)),
        el("td", {}, ruDate(d.on_date)),
        el("td", {}, d.kind_title, d.counterparty_name ? el("div", { class: "muted small" }, d.counterparty_name) : "",
          d.employee_name ? el("div", { class: "muted small" }, `виновный: ${d.employee_name}`) : ""),
        el("td", {}, ...d.items.slice(0, 5).map((i) => el("div", {}, `${i.number} ${i.name}`,
          el("span", { class: "muted small" }, ` · ${i.accounting_title}${i.operation_number ? `, операция № ${i.operation_number}` : ""}`))),
          d.items.length > 5 ? el("div", { class: "muted small" }, `и ещё ${d.items.length - 5}`) : ""),
        el("td", { class: "num" }, Number(d.total) ? money(d.total) : ""),
        el("td", {}, d.reason, d.reject_reason ? el("div", { class: "muted small" }, `Причина отказа: ${d.reject_reason}`) : ""),
        el("td", {}, el("span", { class: `badge disposal-${d.status}` }, d.status_title)),
        el("td", { class: "req-actions" },
          d.can_decide ? el("button", { type: "button", onclick: () => act(d, "approve") }, "Согласовать") : "",
          d.can_decide ? el("button", { type: "button", class: "link", onclick: () => openForm({
            title: `Отклонить акт № ${d.number}`, fields: [{ name: "reason", label: "Причина", type: "textarea" }],
            submitText: "Отклонить", onSubmit: async (v) => act(d, "reject", { reason: v.reason ?? "" }) }) }, "Отклонить") : "",
          d.can_cancel ? el("button", { type: "button", class: "link", onclick: () => act(d, "cancel") }, "Отменить") : "",
          d.can_undo ? el("button", { type: "button", class: "link danger", onclick: () => {
            if (confirm(`Отменить выбытие по акту № ${d.number}? Проводки удалятся, единицы вернутся.`)) act(d, "undo");
          } }, "Отменить выбытие") : ""))))));
  }

  return { show: async () => { render(); await load(); } };
}

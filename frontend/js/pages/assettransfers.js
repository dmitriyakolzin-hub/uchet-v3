// Перемещения имущества (docs/TZ.md, раздел 28, «Детали 19.5»): список, подтверждение получателем, отказ, отмена;
// форма «Переместить / передать» для отмеченных единиц.
import { api } from "../api.js";
import { date as ruDate } from "../format.js";
import { el, instruction, openForm, toast } from "../ui.js";

const STATUS_FILTER = [["pending", "Ждут подтверждения"], ["", "Все"], ["accepted", "Принятые"], ["rejected,cancelled", "Отказы и отмены"]];
const idOrNull = (v) => (v ? Number(v) : null);
const options = (items, label, empty) => [["", empty], ...items.map((i) => [String(i.id), label(i)])];

/** Форма перемещения отмеченных единиц; onDone — после создания. */
export function transferForm({ org, ref, items, onDone }) {
  openForm({
    title: `Переместить / передать: ${items.length === 1 ? `${items[0].number} ${items[0].name}` : `${items.length} ед.`}`,
    fields: [
      { name: "on_date", label: "Дата", type: "date", required: true },
      { name: "to_cfo_id", label: "Куда (место, ЦФО)", type: "select", options: options(ref.cfos, (c) => ref.cfoLabel.get(c.id), "— не менять —") },
      { name: "to_warehouse_id", label: "Склад", type: "select", options: options(ref.warehouses, (w) => w.name, "— не менять —") },
      { name: "to_employee_id", label: "Кому (новый МОЛ)", type: "select", options: options(ref.employees, (e) => e.full_name, "— не менять —"),
        hint: "Единицы перейдут, когда получатель подтвердит у себя (или АХО за него)" },
      { name: "to_status", label: "Статус после передачи", type: "select",
        options: [["", "— не менять —"], ["in_use", "В эксплуатацию"], ["stock", "На склад"], ["repair", "В ремонт"]] },
      { name: "reason", label: "Основание", type: "textarea" },
    ],
    values: { on_date: new Date().toISOString().slice(0, 10) },
    submitText: "Отправить",
    onSubmit: async (v) => {
      const t = await api.post(`/api/organizations/${org.id}/asset-transfers`, {
        ids: items.map((i) => i.id), on_date: v.on_date, to_cfo_id: idOrNull(v.to_cfo_id),
        to_warehouse_id: idOrNull(v.to_warehouse_id), to_employee_id: idOrNull(v.to_employee_id),
        to_status: v.to_status || null, reason: v.reason ?? "",
      });
      toast(`Перемещение № ${t.number} создано — ждёт подтверждения получателя`);
      await onDone?.();
    },
  });
}

/** Вкладка «Перемещения». */
export function assetTransfersTab(box, ctx) {
  let filter = "pending";
  let toMe = false;
  const listBox = el("div");

  function render() {
    const status = el("select", { "aria-label": "Статус перемещений" },
      ...STATUS_FILTER.map(([v, t]) => el("option", { value: v, selected: v === filter }, t)));
    status.addEventListener("change", () => { filter = status.value; load(); });
    const me = el("input", { type: "checkbox", checked: toMe, "aria-label": "Мне на подтверждение" });
    me.addEventListener("change", () => { toMe = me.checked; load(); });
    box.replaceChildren(
      instruction("Перемещение: отметьте единицы в «Реестре» → «Переместить / передать». Передают АХО, МОЛ — своё, "
        + "руководитель ЦФО — имущество своей ветки, руководитель ЦФУ — своего ЦФУ. Сначала перемещение согласует АХО "
        + "(«Согласовать (АХО)»; если в организации нет оператора АХО — не нужно). Единицы переходят на новое место "
        + "и к новому МОЛ, когда получатель подтвердит у себя на этой вкладке («Принять»): МОЛ, руководитель ЦФО ветки, "
        + "куда перемещают, руководитель ЦФУ нового МОЛ — или АХО за него. Получатель "
        + "может отказаться, автор или АХО — отменить; тогда единицы остаются, где были. Так же оформляется передача "
        + "в эксплуатацию (статус «В эксплуатацию») и возврат на склад."),
      el("div", { class: "filters" }, el("label", { class: "inline" }, "Показать ", status),
        el("label", { class: "check" }, me, " Мне на подтверждение")),
      listBox,
    );
  }

  async function act(t, action, body) {
    try {
      await api.post(`/api/asset-transfers/${t.id}/${action}`, body);
      await load();
      await ctx.reload?.();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function load() {
    const p = new URLSearchParams();
    if (filter) p.set("status", filter);
    if (toMe) p.set("to_me", "true");
    let rows;
    try {
      rows = await api.get(`/api/organizations/${ctx.org().id}/asset-transfers?${p}`);
    } catch (err) {
      listBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    if (!rows.length) {
      listBox.replaceChildren(el("p", { class: "muted" }, "Перемещений нет."));
      return;
    }
    listBox.replaceChildren(el("table", { class: "recon asset-transfers" },
      el("thead", {}, el("tr", {}, ...["№", "Дата", "Что", "Откуда", "Куда", "Кому", "Статус", ""].map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows.map((t) => el("tr", { "data-number": t.number },
        el("td", {}, String(t.number)),
        el("td", {}, ruDate(t.on_date)),
        el("td", {}, ...t.items.slice(0, 5).map((i) => el("div", {}, `${i.number} ${i.name}`)),
          t.items.length > 5 ? el("div", { class: "muted small" }, `и ещё ${t.items.length - 5}`) : ""),
        el("td", {}, ...[...new Set(t.items.map((i) => [i.from_place, i.from_employee].filter(Boolean).join(", ")))].map((x) => el("div", {}, x))),
        el("td", {}, [t.to_place, t.to_warehouse].filter(Boolean).join(" · ") || "—",
          t.to_status_title ? el("div", { class: "muted small" }, t.to_status_title) : ""),
        el("td", {}, t.to_employee || "—"),
        el("td", {}, el("span", { class: `badge transfer-${t.status}` }, t.status_title),
          t.reason ? el("div", { class: "muted small" }, t.reason) : "",
          t.reject_reason ? el("div", { class: "muted small" }, `Причина: ${t.reject_reason}`) : ""),
        el("td", { class: "req-actions" },
          t.can_approve ? el("button", { type: "button", onclick: () => act(t, "approve") }, "Согласовать (АХО)") : "",
          t.can_accept ? el("button", { type: "button", class: t.can_approve ? "secondary" : "", onclick: () => act(t, "accept") },
            t.can_approve ? "Согласовать и принять" : "Принять") : "",
          t.can_accept || t.can_approve ? el("button", { type: "button", class: "link", onclick: () => openForm({
            title: `Отказаться от перемещения № ${t.number}`, fields: [{ name: "reason", label: "Причина", type: "textarea" }],
            submitText: "Отказаться", onSubmit: async (v) => act(t, "reject", { reason: v.reason ?? "" }) }) }, "Отказаться") : "",
          t.can_cancel ? el("button", { type: "button", class: "link", onclick: () => act(t, "cancel") }, "Отменить") : ""))))));
  }

  return { show: async () => { render(); await load(); } };
}

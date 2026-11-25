// Ручная смена статуса заявки — администратор и финансовый директор (docs/TZ.md, раздел 30, «Детали 22.7»).
// kind: "payment-requests" или "asset-requests". Автоматика — как у обычного действия (делает сервер).
import { api } from "./api.js";
import { el, openForm, toast } from "./ui.js";

const HINTS = {
  "payment-requests": {
    pending: "Маршрут строится заново; выберите, с какого шага продолжить.",
    paid: "Как «Оплачено»: у кассовой выдачи под отчёт без № операции создаётся операция Дт 71 — Кт 50.",
    leavePaid: "Отметка об оплате снимается; операция, созданная самой заявкой (выдача под отчёт), удаляется.",
    rejected: "Оплата поставщику — накладные снова ждут оплаты.",
  },
  "asset-requests": {
    pending: "Маршрут строится заново (цепочка ЦФО, отметка о бюджете); выберите, с какого шага продолжить.",
    decided: "Нужно решение ФД: оплаты снова попадут в календарь.",
    undecide: "Оплаты решения уберутся из календаря (если по ним ещё не платили).",
    received: "«Принято» ставит только приёмка («Принять…»).",
  },
};
const DECIDED = ["approved", "ordered", "shipped", "delivered"];

export async function openManualStatus(kind, r, onDone) {
  let options;
  try {
    options = await api.get(`/api/${kind}/${r.id}/status-options`);
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  const statusOptions = Object.entries(options.statuses).filter(([k]) => k !== r.status || k === "pending");
  const steps = options.route.map((s) => [String(s.index), `${s.index + 1}. ${s.title}`]);
  const fields = [
    { name: "status", label: "Новый статус", type: "select", options: statusOptions, required: true },
    { name: "step", label: "С какого шага маршрута", type: "select", options: steps },
  ];
  if (kind === "payment-requests") {
    fields.push({ name: "paid_on", label: "Дата оплаты", type: "date" },
      { name: "operation_number", label: "№ операции в журнале (необязательно)", type: "number" });
  } else {
    fields.push({ name: "on", label: "Дата доставки", type: "date" });
  }
  fields.push({ name: "reason", label: "Причина (запишется в историю)", type: "textarea", required: true });
  const hint = el("p", { class: "muted small" });
  const dialog = openForm({
    title: `Сменить статус вручную: заявка № ${r.number}`,
    fields,
    values: { status: statusOptions[0]?.[0] ?? "", step: steps.length ? steps[steps.length - 1][0] : "", paid_on: today, on: today },
    extra: hint,
    submitText: "Сменить статус",
    onSubmit: async (v) => {
      const body = { status: v.status, reason: v.reason, step: Number(v.step || 0) };
      if (v.status === "paid") {
        body.paid_on = v.paid_on || today;
        if (v.operation_number) body.operation_number = Number(v.operation_number);
      }
      if (v.status === "delivered") body.on = v.on || today;
      await api.post(`/api/${kind}/${r.id}/status`, body);
      toast("Статус изменён");
      await onDone?.();
    },
  });
  const form = dialog.querySelector("form");
  const show = (name, visible) => {
    const input = form.elements[name];
    if (!input) return;
    input.hidden = !visible;
    const label = form.querySelector(`label[for="${input.id}"]`);
    if (label) label.hidden = !visible;
  };
  const sync = () => {
    const target = form.elements.status.value;
    const h = HINTS[kind];
    show("step", target === "pending" && steps.length > 0);
    show("paid_on", target === "paid");
    show("operation_number", target === "paid");
    show("on", target === "delivered");
    const notes = [];
    if (target === "pending") notes.push(options.route_note || h.pending);
    if (kind === "payment-requests") {
      if (target === "paid") notes.push(h.paid);
      if (r.status === "paid" && target !== "paid") notes.push(h.leavePaid);
      if (target === "rejected" && r.source === "supplier") notes.push(h.rejected);
    } else {
      if (DECIDED.includes(target) && !DECIDED.includes(r.status)) notes.push(h.decided);
      if (!DECIDED.includes(target) && DECIDED.includes(r.status)) notes.push(h.undecide);
      if (target === "received") notes.push(h.received);
    }
    hint.textContent = notes.join(" ");
  };
  form.elements.status.addEventListener("change", sync);
  sync();
}

// «Платёжный календарь»: заявки на оплату и ожидаемые поступления — подача, согласование, оплата (docs/TZ.md, раздел 18).
import { api } from "../api.js";
import { date, money, monthBounds, parseNumber } from "../format.js";
import { el, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const STATUS = { draft: "Черновик", pending: "На согласовании", approved: "Согласована", rejected: "Отклонена", paid: "Оплачена" };
const DIRECTION = { out: "Выплата", in: "Поступление" };

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}
const options = (items, label, empty = "— не указано —") => [["", empty], ...items.map((i) => [String(i.id), label(i)])];
const idOrNull = (v) => (v ? Number(v) : null);

export async function renderCalendar(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Платёжный календарь"), el("p", { class: "muted" }, "Нет доступных организаций."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  let ref = await loadReferences(org.id);
  const [from, to] = monthBounds();
  const filter = { date_from: from, date_to: to, status: "", direction: "" };

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    ref = await loadReferences(org.id);
    await load();
  });
  const input = (type, key, label) => {
    const node = el("input", { type, value: filter[key], "aria-label": label });
    node.addEventListener("change", () => { filter[key] = node.value; load(); });
    return node;
  };
  const select = (key, label, items) => {
    const node = el("select", { "aria-label": label }, ...items.map(([v, t]) => el("option", { value: v }, t)));
    node.addEventListener("change", () => { filter[key] = node.value; load(); });
    return node;
  };
  const listBox = el("div");
  const totalsBox = el("p", { class: "muted" });
  const can = (perm) => org.permissions.includes(perm);
  const query = () => new URLSearchParams(Object.entries(filter).filter(([, v]) => v)).toString();

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Платёжный календарь"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("p", { class: "muted" }, "Заявка на оплату: черновик → на согласовании → согласована → оплачена. Согласует финансовый директор. "
      + "Платёж из выписки на ту же сумму тому же контрагенту сам отметит согласованную заявку оплаченной. "
      + "Ожидаемые поступления согласования не требуют."),
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "С ", input("date", "date_from", "С даты")),
      el("label", { class: "inline" }, "по ", input("date", "date_to", "По дату")),
      select("status", "Статус", [["", "Все статусы"], ...Object.entries(STATUS)]),
      select("direction", "Вид", [["", "Выплаты и поступления"], ["out", "Выплаты"], ["in", "Поступления"]]),
      el("button", { type: "button", onclick: () => edit(null, "out") }, "Новая заявка"),
      el("button", { type: "button", class: "secondary", onclick: () => edit(null, "in") }, "Ожидаемое поступление"),
      el("button", { type: "button", class: "secondary",
        onclick: () => api.download(`/api/organizations/${org.id}/payment-requests.xlsx?${query()}`, "payment_requests.xlsx")
          .catch((err) => toast(err.message, "error")) }, "Скачать в Excel")),
    listBox, totalsBox,
  );

  function budgetCell(r) {
    const b = r.budget;
    if (!b) return "";
    if (b.plan === null) return el("span", { class: "muted", title: "Нет утверждённого плана ДДС на этот месяц" }, "нет плана");
    if (Number(b.over) > 0) {
      return el("span", { class: "over-budget", title: `План ${money(b.plan)}, факт ${money(b.fact)}, другие заявки ${money(b.requested)}` },
        `сверх бюджета на ${money(b.over)}`);
    }
    return el("span", { class: "muted", title: `План ${money(b.plan)}, факт ${money(b.fact)}, другие заявки ${money(b.requested)}` },
      `в бюджете (остаток ${money(b.remaining)})`);
  }

  function actions(r) {
    const button = (text, onclick, cls = "link") => el("button", { type: "button", class: cls, onclick }, text);
    const run = (action, body) => act(r, action, body);
    const list = [];
    if (["draft", "rejected"].includes(r.status) && can("draft")) {
      list.push(button("Изменить", () => edit(r, r.direction)), button(r.direction === "in" ? "Подтвердить" : "На согласование", () => run("submit")));
      if (r.status === "draft") list.push(button("Удалить", () => remove(r)));
    }
    if (r.status === "pending") {
      if (can("post")) list.push(button("Согласовать", () => run("approve")), button("Отклонить", () => reject(r)));
      if (can("draft")) list.push(button("Отозвать", () => run("recall")));
    }
    if (r.status === "approved" && can("post")) {
      list.push(button("Оплачено", () => pay(r)), button("Перенести", () => move(r)), button("Отклонить", () => reject(r)),
        button(r.direction === "in" ? "В черновик" : "Вернуть на согласование", () => run("unapprove")));
    }
    if (r.status === "pending" && can("post")) list.push(button("Перенести", () => move(r)));
    if (r.status === "paid" && can("post")) list.push(button("Снять оплату", () => run("unpay")));
    return el("td", { class: "req-actions" }, ...list);
  }

  function statusCell(r) {
    const note = r.status === "rejected" && r.reject_reason ? el("div", { class: "muted" }, `Причина: ${r.reject_reason}`)
      : r.status === "paid" ? el("div", { class: "muted" }, `${date(r.paid_on)}${r.operation_number ? `, операция № ${r.operation_number}` : ""}`
        + `${r.confirmed_by_statement ? " (выписка)" : ""}`) : null;
    return el("td", {}, el("span", { class: `status req-${r.status}` }, STATUS[r.status]), note);
  }

  async function load() {
    let data;
    try {
      data = await api.get(`/api/organizations/${org.id}/payment-requests?${query()}`);
    } catch (err) {
      listBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    listBox.replaceChildren(data.rows.length
      ? el("table", { class: "recon versions requests" },
        el("thead", {}, el("tr", {}, ...["№", "Дата оплаты", "Вид", "Контрагент / назначение", "Статья ДДС", "ЦФО", "Счёт / касса",
          "Сумма", "Бюджет", "Статус", ""].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...data.rows.map((r) => el("tr", { "data-number": r.number },
          el("td", {}, String(r.number)), el("td", {}, date(r.pay_date)), el("td", {}, DIRECTION[r.direction]),
          el("td", {}, r.counterparty_name || "—", r.contract_name ? el("div", { class: "muted" }, `Договор ${r.contract_name}`) : null,
            r.description ? el("div", { class: "muted" }, r.description) : null),
          el("td", {}, r.article_name), el("td", {}, r.cfo_name, r.executor_name ? el("div", { class: "muted" }, r.executor_name) : null),
          el("td", {}, r.source_name),
          el("td", { class: "num" }, money(r.amount)), el("td", {}, budgetCell(r)), statusCell(r), actions(r)))))
      : el("p", { class: "muted" }, "За этот период заявок нет."));
    totalsBox.textContent = `Выплаты: ${money(data.total_out)} · Поступления: ${money(data.total_in)}`;
  }

  async function act(r, action, body) {
    try {
      await api.post(`/api/payment-requests/${r.id}/${action}`, body);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function remove(r) {
    if (!confirm(`Удалить заявку № ${r.number}?`)) return;
    try {
      await api.delete(`/api/payment-requests/${r.id}`);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function reject(r) {
    openForm({
      title: `Отклонить заявку № ${r.number}`,
      fields: [{ name: "reason", label: "Причина", type: "textarea", required: true }],
      submitText: "Отклонить",
      onSubmit: async (v) => { await api.post(`/api/payment-requests/${r.id}/reject`, v); await load(); },
    });
  }

  function pay(r) {
    openForm({
      title: `Оплата заявки № ${r.number}`,
      fields: [
        { name: "paid_on", label: "Дата оплаты", type: "date", required: true },
        { name: "operation_number", label: "№ операции журнала", type: "number",
          hint: "Необязательно: операция, которой проведена оплата (касса, другой банк)" },
      ],
      values: { paid_on: r.pay_date },
      submitText: "Отметить оплату",
      onSubmit: async (v) => {
        await api.post(`/api/payment-requests/${r.id}/pay`, { paid_on: v.paid_on, operation_number: idOrNull(v.operation_number) });
        await load();
      },
    });
  }

  // откуда платим: расчётный счёт юрлица или касса (ЦФО с кассой); руководитель ЦФО счёт не выбирает
  function sourceOptions(entityId) {
    const banks = org.restricted ? [] : ref.bankAccounts.filter((b) => String(b.legal_entity_id) === String(entityId))
      .map((b) => [`bank:${b.id}`, `${b.name} ··${b.number.slice(-4)}`]);
    const cash = ref.cfos.filter((c) => c.has_cash && inBranch(c)).map((c) => [`cash:${c.id}`, `Касса: ${c.name}`]);
    return [["", "— не указано —"], ...banks, ...cash];
  }
  const inBranch = (c) => !org.cfo_scope || c.path.split(".").includes(String(org.cfo_scope));
  const sourceValue = (r) => (r?.bank_account_id ? `bank:${r.bank_account_id}` : r?.cash_cfo_id ? `cash:${r.cash_cfo_id}` : "");
  const sourceBody = (value) => ({
    bank_account_id: value.startsWith("bank:") ? Number(value.slice(5)) : null,
    cash_cfo_id: value.startsWith("cash:") ? Number(value.slice(5)) : null,
  });
  function refill(selectNode, items, keep) {
    selectNode.replaceChildren(...items.map(([v, t]) => el("option", { value: v, selected: v === keep }, t)));
  }

  function move(r) {
    const dialog = openForm({
      title: `Перенести заявку № ${r.number}`,
      fields: [
        { name: "pay_date", label: "Дата оплаты", type: "date", required: true },
        { name: "source", label: r.direction === "in" ? "Куда поступит" : "Откуда платим", type: "select", options: sourceOptions(r.legal_entity_id) },
      ],
      values: { pay_date: r.pay_date, source: sourceValue(r) },
      submitText: "Сохранить",
      onSubmit: async (v) => {
        await api.post(`/api/payment-requests/${r.id}/move`, { pay_date: v.pay_date, ...sourceBody(v.source) });
        await load();
      },
    });
    return dialog;
  }

  function edit(r, direction) {
    const entities = ref.entities.filter((e) => e.is_active);
    const flow = direction === "out" ? "outflow" : "inflow";
    const articles = ref.articles.filter((a) => a.is_active && a.is_cash_flow && !a.is_transfer && a.cash_flow_direction === flow);
    const cfos = ref.cfos.filter(inBranch);
    const entityId = String(r?.legal_entity_id ?? entities[0]?.id ?? "");
    const contractOptions = (cpId, leId) => options(ref.contracts.filter((c) => String(c.counterparty_id) === cpId
      && String(c.legal_entity_id) === leId), (c) => c.name || `№ ${c.number}`);
    const dialog = openForm({
      title: r ? `Заявка № ${r.number}` : direction === "in" ? "Ожидаемое поступление" : "Новая заявка на оплату",
      fields: [
        { name: "pay_date", label: direction === "in" ? "Ожидаемая дата" : "Дата оплаты", type: "date", required: true },
        { name: "legal_entity_id", label: "Юрлицо", type: "select", options: entities.map((e) => [String(e.id), e.short_name]) },
        { name: "source", label: direction === "in" ? "Куда поступит" : "Откуда платим", type: "select", options: sourceOptions(entityId),
          hint: org.restricted ? "Расчётный счёт выбирает финансовый директор" : "Можно выбрать при согласовании" },
        { name: "counterparty_id", label: "Контрагент", type: "select", options: options(ref.counterparties, (c) => c.name) },
        { name: "contract_id", label: "Договор", type: "select", options: contractOptions(String(r?.counterparty_id ?? ""), entityId) },
        { name: "article_id", label: "Статья ДДС", type: "select", options: options(articles, (a) => a.name) },
        { name: "cfo_id", label: "ЦФО", type: "select", options: options(cfos, (c) => ref.cfoLabel.get(c.id)),
          required: Boolean(org.restricted) },
        { name: "executor_id", label: "ЦФУ", type: "select", options: options(ref.executors, (e) => ref.executorLabel.get(e.id)) },
        { name: "amount", label: "Сумма", required: true, placeholder: "0,00" },
        { name: "description", label: "Назначение", type: "textarea" },
      ],
      values: {
        pay_date: r?.pay_date ?? ref.today, legal_entity_id: entityId, source: sourceValue(r),
        counterparty_id: String(r?.counterparty_id ?? ""), contract_id: String(r?.contract_id ?? ""),
        article_id: String(r?.article_id ?? ""), cfo_id: String(r?.cfo_id ?? (org.cfo_scope ?? "")),
        executor_id: String(r?.executor_id ?? ""), amount: r ? money(r.amount) : "", description: r?.description ?? "",
      },
      submitText: r ? "Сохранить" : "Создать",
      onSubmit: async (v) => {
        const body = {
          direction, pay_date: v.pay_date, legal_entity_id: Number(v.legal_entity_id), ...sourceBody(v.source),
          counterparty_id: idOrNull(v.counterparty_id), contract_id: idOrNull(v.contract_id), article_id: idOrNull(v.article_id),
          cfo_id: idOrNull(v.cfo_id), executor_id: idOrNull(v.executor_id), amount: parseNumber(v.amount), description: v.description,
        };
        if (r) await api.put(`/api/payment-requests/${r.id}`, body);
        else await api.post(`/api/organizations/${org.id}/payment-requests`, body);
        await load();
      },
    });
    const form = dialog.querySelector("form");
    const { legal_entity_id: entity, source, counterparty_id: cp, contract_id: contract } = form.elements;
    entity.addEventListener("change", () => {
      refill(source, sourceOptions(entity.value), source.value);
      refill(contract, contractOptions(cp.value, entity.value), contract.value);
    });
    cp.addEventListener("change", () => refill(contract, contractOptions(cp.value, entity.value), ""));
  }

  await load();
}

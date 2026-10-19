// «Платёжный календарь»: заявки на оплату и ожидаемые поступления — подача, согласование, оплата (docs/TZ.md, раздел 18).
import { api } from "../api.js";
import { date, money, monthBounds, parseNumber } from "../format.js";
import { openOperation } from "../opform.js";
import { el, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const TAB_KEY = "uchet3_calendar_tab";
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
    await openTab(tab);
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

  const tabs = el("div", { class: "tabs" });
  const content = el("div");
  const requestsBox = el("div", {},
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
  const expectedBox = el("div");
  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Платёжный календарь"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    tabs, content,
  );
  let tab = ["requests", "expected"].includes(recall(TAB_KEY)) ? recall(TAB_KEY) : "grid";
  function openTab(key) {
    tab = org.restricted ? "requests" : key;
    remember(TAB_KEY, tab);
    const list = org.restricted ? [["requests", "Заявки"]]
      : [["grid", "Календарь"], ["requests", "Заявки"], ["expected", "Ожидаемые платежи"]];
    tabs.replaceChildren(...list.map(([k, title]) =>
      el("button", { type: "button", class: `tab${k === tab ? " active" : ""}`, onclick: () => openTab(k) }, title)));
    content.replaceChildren({ grid: gridBox, requests: requestsBox, expected: expectedBox }[tab]);
    return { grid: loadGrid, requests: load, expected: loadExpected }[tab]();
  }

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

  function move(r, after = load) {
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
        await after();
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

  // --- ожидаемые платежи: заявки, ведомости, регламентные операции, оплаты инвестпроектов ---
  const expFilter = { date_from: from, date_to: to, legal_entity_id: "", include_pending: false };
  const expList = el("div");
  const expTotals = el("p", { class: "muted" });
  const expInput = (key, label) => {
    const node = el("input", { type: "date", value: expFilter[key], "aria-label": label });
    node.addEventListener("change", () => { expFilter[key] = node.value; loadExpected(); });
    return node;
  };
  const entitySelect = el("select", { "aria-label": "Юрлицо" });
  entitySelect.addEventListener("change", () => { expFilter.legal_entity_id = entitySelect.value; loadExpected(); });
  const pendingBox = el("input", { type: "checkbox", "aria-label": "С заявками на согласовании" });
  pendingBox.addEventListener("change", () => { expFilter.include_pending = pendingBox.checked; loadExpected(); });
  expectedBox.append(
    el("p", { class: "muted" }, "Всё, что ещё не стало фактом в журнале: согласованные заявки, неоплаченные ведомости, "
      + "невыполненные регламентные операции и плановые оплаты инвестпроектов из утверждённого бюджета. "
      + "Платёж с прошедшей датой помечен «просрочен»."),
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "С ", expInput("date_from", "С даты")),
      el("label", { class: "inline" }, "по ", expInput("date_to", "По дату")),
      entitySelect,
      el("label", { class: "check" }, pendingBox, " с заявками на согласовании")),
    expList, expTotals,
  );

  async function loadExpected() {
    entitySelect.replaceChildren(...[["", "Все юрлица"], ...ref.entities.map((e) => [String(e.id), e.short_name])]
      .map(([v, t]) => el("option", { value: v, selected: v === expFilter.legal_entity_id }, t)));
    let data;
    try {
      const params = new URLSearchParams(Object.entries(expFilter).filter(([, v]) => v !== "" && v !== false));
      data = await api.get(`/api/organizations/${org.id}/payment-calendar/expected?${params}`);
    } catch (err) {
      expList.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    expList.replaceChildren(data.rows.length
      ? el("table", { class: "recon versions expected" },
        el("thead", {}, el("tr", {}, ...["Дата", "Источник", "Юрлицо", "Счёт / касса", "Контрагент", "Статья ДДС",
          "Поступление", "Выплата"].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...data.rows.map((r) => el("tr", { "data-source": r.source },
          el("td", {}, date(r.date), r.overdue ? el("div", { class: "over-budget" }, "просрочен") : null),
          el("td", {}, r.title),
          el("td", {}, r.legal_entity_name), el("td", {}, r.source_name), el("td", {}, r.counterparty_name),
          el("td", {}, r.article_name),
          el("td", { class: "num" }, r.direction === "in" ? money(r.amount) : ""),
          el("td", { class: "num" }, r.direction === "out" ? money(r.amount) : "")))))
      : el("p", { class: "muted" }, "За этот период ожидаемых платежей нет."));
    expTotals.textContent = `Поступления: ${money(data.total_in)} · Выплаты: ${money(data.total_out)}`;
  }

  // --- календарь по дням: остаток, факт и ожидаемое, кассовые разрывы ---
  const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const nextMonthEnd = (() => { const d = new Date(); return monthBounds(new Date(d.getFullYear(), d.getMonth() + 1, 1))[1]; })();
  const gridFilter = { date_from: from, date_to: nextMonthEnd, legal_entity_id: "", source: "", include_pending: false };
  const gridBox = el("div");
  const gridTable = el("div");
  const gridHead = el("div");
  const gridDate = (key, label) => {
    const node = el("input", { type: "date", value: gridFilter[key], "aria-label": label });
    node.addEventListener("change", () => { gridFilter[key] = node.value; loadGrid(); });
    return node;
  };
  const gridEntity = el("select", { "aria-label": "Юрлицо" });
  gridEntity.addEventListener("change", () => { gridFilter.legal_entity_id = gridEntity.value; gridFilter.source = ""; loadGrid(); });
  const gridSource = el("select", { "aria-label": "Счёт или касса" });
  gridSource.addEventListener("change", () => { gridFilter.source = gridSource.value; loadGrid(); });
  const gridPending = el("input", { type: "checkbox", "aria-label": "С заявками на согласовании" });
  gridPending.addEventListener("change", () => { gridFilter.include_pending = gridPending.checked; loadGrid(); });
  const gridParams = () => {
    const p = new URLSearchParams({ date_from: gridFilter.date_from, date_to: gridFilter.date_to });
    if (gridFilter.legal_entity_id) p.set("legal_entity_id", gridFilter.legal_entity_id);
    if (gridFilter.source.startsWith("bank:")) p.set("bank_account_id", gridFilter.source.slice(5));
    if (gridFilter.source.startsWith("cash:")) p.set("cash_cfo_id", gridFilter.source.slice(5));
    if (gridFilter.include_pending) p.set("include_pending", "true");
    return p;
  };
  gridBox.append(
    el("p", { class: "muted" }, "Прошедшие дни — факт из журнала; с сегодняшнего дня — плюс ожидаемые платежи "
      + "(согласованные заявки, ведомости, регламентные операции, оплаты инвестпроектов). Просроченные — на сегодня. "
      + "Красным — дни, где остаток уходит в минус. Нажмите на день, чтобы увидеть, из чего сложились суммы."),
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "С ", gridDate("date_from", "Календарь с")),
      el("label", { class: "inline" }, "по ", gridDate("date_to", "Календарь по")),
      gridEntity, gridSource,
      el("label", { class: "check" }, gridPending, " с заявками на согласовании"),
      el("button", { type: "button", class: "secondary",
        onclick: () => api.download(`/api/organizations/${org.id}/payment-calendar.xlsx?${gridParams()}`, "payment_calendar.xlsx")
          .catch((err) => toast(err.message, "error")) }, "Скачать в Excel")),
    gridHead, gridTable,
  );

  function cellMoney(total, expectedPart) {
    if (!Number(total)) return el("td", { class: "num" }, "");
    return el("td", { class: "num" }, money(total),
      Number(expectedPart) ? el("div", { class: "muted small" }, `ожид. ${money(expectedPart)}`) : null);
  }

  async function loadGrid() {
    gridTable.replaceChildren(el("p", { class: "muted" }, "Загрузка…"));  // старая таблица не должна оставаться кликабельной
    gridEntity.replaceChildren(...[["", "Все юрлица"], ...ref.entities.map((e) => [String(e.id), e.short_name])]
      .map(([v, t]) => el("option", { value: v, selected: v === gridFilter.legal_entity_id }, t)));
    const banks = ref.bankAccounts.filter((b) => !gridFilter.legal_entity_id || String(b.legal_entity_id) === gridFilter.legal_entity_id);
    gridSource.replaceChildren(...[["", "Все счета и кассы"],
      ...banks.map((b) => [`bank:${b.id}`, `${b.name} ··${b.number.slice(-4)}`]),
      ...ref.cfos.filter((c) => c.has_cash).map((c) => [`cash:${c.id}`, `Касса: ${c.name}`])]
      .map(([v, t]) => el("option", { value: v, selected: v === gridFilter.source }, t)));
    let data;
    try {
      data = await api.get(`/api/organizations/${org.id}/payment-calendar?${gridParams()}`);
    } catch (err) {
      gridHead.replaceChildren();
      gridTable.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    gridHead.replaceChildren(data.first_gap
      ? el("p", { class: "gap-note" }, `Первый кассовый разрыв: ${date(data.first_gap.date)}, остаток ${money(data.first_gap.closing)}`)
      : el("p", { class: "muted" }, "Кассовых разрывов в периоде нет."));
    const today = ref.today;
    const rows = [];
    let w = 0;
    for (const d of data.days) {
      const weekday = WEEKDAYS[new Date(`${d.date}T00:00:00`).getDay()];
      const cls = [Number(d.closing) < 0 ? "gap-row" : "", d.date === today ? "today-row" : "", "day-row"].join(" ");
      rows.push(el("tr", { class: cls, "data-date": d.date, onclick: () => showDay(d), title: "Подробнее" },
        el("td", {}, `${date(d.date)} ${weekday}`, d.date === today ? el("span", { class: "muted" }, " · сегодня") : null),
        el("td", { class: "num" }, money(d.opening)),
        cellMoney(d.inflow, d.expected_in), cellMoney(d.outflow, d.expected_out),
        el("td", { class: "num strong" }, money(d.closing))));
      const week = data.weeks[w];
      if (week && d.date === week.date_to) {
        rows.push(el("tr", { class: `week-row${Number(week.closing) < 0 ? " gap-row" : ""}` },
          el("td", {}, `Неделя ${date(week.date_from).slice(0, 5)}–${date(week.date_to).slice(0, 5)}`), el("td", {}),
          el("td", { class: "num" }, Number(week.inflow) ? money(week.inflow) : ""),
          el("td", { class: "num" }, Number(week.outflow) ? money(week.outflow) : ""),
          el("td", { class: "num" }, money(week.closing))));
        w += 1;
      }
    }
    gridTable.replaceChildren(el("table", { class: "recon calendar-grid" },
      el("thead", {}, el("tr", {}, ...["День", "Остаток на начало", "Поступления", "Выплаты", "Остаток на конец"]
        .map((t) => el("th", {}, t)))),
      el("tbody", {},
        el("tr", { class: "week-row" }, el("td", {}, "Остаток на начало периода"), el("td", {}), el("td", {}), el("td", {}),
          el("td", { class: "num" }, money(data.opening))),
        ...rows,
        el("tr", { class: "week-row" }, el("td", {}, "Итого за период"), el("td", { class: "num" }, money(data.opening)),
          el("td", { class: "num" }, money(data.inflow)), el("td", { class: "num" }, money(data.outflow)),
          el("td", { class: "num" }, money(data.closing))))));
  }

  function showDay(d) {
    const close = () => dialog.close();
    const factList = d.fact.length
      ? el("table", { class: "recon" }, el("tbody", {}, ...d.fact.map((f) => el("tr", {},
        el("td", {}, el("button", { type: "button", class: "link", onclick: async () => {
          close();
          const op = await api.get(`/api/operations/${f.operation_id}`);
          openOperation({ ref, org, op, perms: org.permissions, onChanged: loadGrid });
        } }, `№ ${f.number}`)),
        el("td", {}, f.description),
        el("td", { class: "num" }, Number(f.inflow) ? `+${money(f.inflow)}` : ""),
        el("td", { class: "num" }, Number(f.outflow) ? `−${money(f.outflow)}` : "")))))
      : el("p", { class: "muted" }, "Нет.");
    const expectedList = d.expected.length
      ? el("table", { class: "recon" }, el("tbody", {}, ...d.expected.map((i) => el("tr", {},
        el("td", {}, i.title, i.overdue ? el("div", { class: "over-budget" }, `просрочен (${date(i.date)})`) : null),
        el("td", {}, i.counterparty_name || i.source_name),
        el("td", { class: "num" }, i.direction === "in" ? `+${money(i.amount)}` : `−${money(i.amount)}`),
        el("td", {}, i.source === "request" && can("post")
          ? el("button", { type: "button", class: "link", onclick: async () => {
            close();
            move(await api.get(`/api/payment-requests/${i.source_id}`), loadGrid);
          } }, "Перенести") : "")))))
      : el("p", { class: "muted" }, "Нет.");
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, `${date(d.date)}: остаток ${money(d.opening)} → ${money(d.closing)}`),
      el("h3", {}, "Факт (журнал)"), factList,
      el("h3", {}, "Ожидается"), expectedList,
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: close }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  await openTab(tab);
}

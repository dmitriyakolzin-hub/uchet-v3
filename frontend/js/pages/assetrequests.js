// Вкладка «Заявки на имущество» (docs/TZ.md, раздел 28, «Детали 19.2», «Детали 21.1»): что нужно → согласование
// руководителями ЦФО снизу вверх → отметка о бюджете → CEO (сверх бюджета) → решение ФД (наименование, поставщик,
// ответственный, вариант закупки) → оплаты в платёжном календаре → поставка (АХО или ЦФУ, который закупает).
import { api } from "../api.js";
import { date as ruDate, money, parseNumber, sumMoney } from "../format.js";
import { openManualStatus } from "../manualstatus.js";
import { el, instruction, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

// «to_act» — ждут моего решения, «over» — сверх бюджета (рассматриваются отдельно)
const STATUS_FILTER = [["", "Все"], ["to_act", "Ждут моего решения"], ["over", "Сверх бюджета"],
  ["draft,revision,rejected", "Черновики, доработка, отклонённые"], ["pending", "На согласовании"],
  ["approved,ordered,shipped", "Закупаются"], ["delivered", "Доставлены"], ["received,cancelled", "Закрытые"]];
const KINDS = [["prepay", "Предоплата"], ["deferral", "Отсрочка"], ["leasing", "Лизинг"]];
const COST_KINDS = [["", "— выберите —"], ["invest", "Инвестиционная (капвложение)"], ["opex", "Операционная (расход)"]];
const OPEN = ["pending", "approved", "ordered", "shipped", "delivered"];
const ACTIONS = { created: "создал(а)", edited: "изменил(а)", submitted: "подал(а) на согласование", recalled: "отозвал(а)",
  step_approved: "согласовал(а)", revised: "вернул(а) на доработку", budget: "поставил(а) отметку о бюджете",
  lease: "заполнил(а) лизинг", lease_removed: "снял(а) сведения о лизинге", cost: "указал(а) вид затрат",
  approved: "принял(а) решение", rejected: "отклонил(а)", unapproved: "вернул(а) на решение", cancelled: "отменил(а)",
  status: "сменил(а) статус вручную:",
  delivery: "отметил(а) поставку", received: "принял(а)", receipt_undone: "отменил(а) приёмку",
  file: "приложил(а) файл", file_removed: "убрал(а) файл" };
const idOrNull = (v) => (v ? Number(v) : null);
const options = (items, label, empty = "— не указано —") => [["", empty], ...items.map((i) => [String(i.id), label(i)])];

/** box — контейнер вкладки; ctx: { org, ref } (ref — справочники страницы «Имущество»). */
export function assetRequestsTab(box, ctx) {
  let filter = "";
  let mine = false;
  const can = (perm) => ctx.org().permissions.includes(perm);
  const listBox = el("div");

  function render() {
    const status = el("select", { "aria-label": "Статус заявок" },
      ...STATUS_FILTER.map(([v, t]) => el("option", { value: v, selected: v === filter }, t)));
    status.addEventListener("change", () => { filter = status.value; load(); });
    const mineBox = el("input", { type: "checkbox", checked: mine, "aria-label": "Мои заявки" });
    mineBox.addEventListener("change", () => { mine = mineBox.checked; load(); });
    box.replaceChildren(
      instruction("Заявка на имущество: что нужно, сколько, для какого ЦФО (не выбран — вся компания), когда нужно, "
        + "ориентир цены и кто закупает (ЦФУ или сам ЦФО). Подают руководитель ЦФО, руководитель ЦФУ (закупает его ЦФУ), "
        + "сотрудник, инженер, оператор АХО, финансовый директор. Согласование: "
        + "руководители ЦФО снизу вверх — от ЦФО заявки (руководитель ЦФО сам себя не согласует — сразу вышестоящий) "
        + "до верха; ЦФО без руководителя пропускается. Затем система ставит отметку о бюджете: план ДДС ветки ЦФО "
        + "по статье «Покупка основных средств» на месяц «когда нужно» минус другие заявки на имущество ветки этого "
        + "месяца. В рамках бюджета — заявка у финансового директора; сверх бюджета — сначала у CEO, потом у "
        + "финансового директора. Финансовый директор может поменять отметку вручную с комментарием. Отклонить или "
        + "вернуть на доработку может тот, у кого заявка, или финансовый директор. Финансовый директор принимает "
        + "решение: наименование, поставщик, ответственный, вариант закупки — оплата попадает в платёжный календарь "
        + "(лизинг — периодическим платежом). Поставку отмечают оператор АХО или ЦФУ, который закупает: заказано → "
        + "отгружено → доставлено → приёмка. Заявки сверх бюджета — отдельным списком «Сверх бюджета»."),
      el("div", { class: "filters" },
        can("asset_request") ? el("button", { type: "button", onclick: () => editRequest(null) }, "+ Заявка на имущество") : "",
        el("label", { class: "inline" }, "Статус ", status),
        el("label", { class: "check" }, mineBox, " Мои")),
      listBox,
    );
  }

  async function load() {
    const p = new URLSearchParams();
    if (filter === "to_act") p.set("to_act", "true");
    else if (filter === "over") p.set("budget", "over");
    else if (filter) p.set("status", filter);
    if (mine) p.set("mine", "true");
    let rows;
    try {
      rows = await api.get(`/api/organizations/${ctx.org().id}/asset-requests?${p}`);
    } catch (err) {
      listBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    if (!rows.length) {
      listBox.replaceChildren(el("p", { class: "muted" }, "Заявок пока нет."));
      return;
    }
    listBox.replaceChildren(filter === "over" ? overSummary(rows) : "", el("table", { class: "recon requests asset-requests" },
      el("thead", {}, el("tr", {}, ...["№", "Дата", "Что нужно", "ЦФО", "Когда нужно", "Поставщик", "Закупка", "Сумма", "Бюджет", "Поставка", "Статус", "Действие"]
        .map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows.map((r) => {
        const tr = el("tr", { "data-number": r.number },
          el("td", {}, String(r.number)),
          el("td", {}, ruDate(r.created_at.slice(0, 10))),
          el("td", {}, `${r.name} × ${r.quantity}`, r.model_name && r.model_name !== r.name ? el("div", { class: "muted small" }, r.model_name) : ""),
          el("td", {}, r.cfo_name, r.executor_name ? el("div", { class: "muted small" }, `закупает: ${r.executor_name}`) : ""),
          el("td", {}, r.needed_on ? ruDate(r.needed_on) : ""),
          el("td", {}, r.counterparty_name),
          el("td", {}, r.purchase_kind_title, r.purchase_kind === "leasing" && r.lease_payment ? el("div", { class: "muted small" }, `${money(r.lease_payment)} × ${r.lease_months ?? "?"} мес.${r.lease_pending ? " (ориентир)" : ""}`) : ""),
          el("td", { class: "num" }, r.total ? money(r.total) : r.amount ? `≈ ${money(r.amount)}` : ""),
          el("td", {}, budgetBadge(r)),
          el("td", {}, r.delivered_on ? `доставлено ${ruDate(r.delivered_on)}` : r.expected_on ? `ожидается ${ruDate(r.expected_on)}` : "",
            r.received_quantity ? el("div", { class: "muted small" }, `принято ${r.received_quantity} из ${r.quantity}`) : ""),
          el("td", {}, el("span", { class: `badge areq-${r.status}` }, r.status_title),
            r.lease_pending ? el("div", { class: "lease-pending small" }, "ждём договор лизинга") : "",
            r.step_title ? el("div", { class: "muted small" }, `у: ${r.step_title}`) : ""),
          el("td", { class: "req-actions" }, actions(r)));
        tr.addEventListener("click", (e) => { if (!e.target.closest("select, button")) openCard(r); });
        return tr;
      }))));
  }

  function budgetBadge(r) {
    const what = r.project_name || r.budget_article_name;
    const kind = r.cost_kind ? el("div", { class: "muted small" }, `${r.cost_kind === "invest" ? "инвест." : "расход"}${what ? `: ${what}` : ""}`) : "";
    if (!r.budget_mark) return kind;
    return el("span", {}, el("span", { class: `badge budget-${r.budget_mark}`, title: r.budget_comment || "" },
      r.budget_mark_title + (r.budget_manual ? " (вручную)" : ""),
      r.budget_excess ? el("div", { class: "small" }, `+${money(r.budget_excess)}`) : ""), kind);
  }

  // расчёт бюджета словами (21.3): план, факт, другие заявки, остаток, лимит проекта
  function budgetCalc(r) {
    if (r.budget_plan === null) return "плана на этот месяц нет";
    return `план ${money(r.budget_plan)}${r.budget_fact !== null ? `, факт ${money(r.budget_fact)}` : ""}, другие заявки ${money(r.budget_used)}, `
      + `остаток ${money(r.budget_remaining)}, заявка ${money(r.amount)}`
      + (r.budget_limit_left !== null ? `; остаток лимита проекта ${money(r.budget_limit_left)}` : "");
  }

  // вид затрат, инвестпроект и статья — поля формы заявки и окна «Вид затрат…»
  const loadBudgetOptions = () => api.get(`/api/organizations/${ctx.org().id}/asset-requests/budget-options`);
  const costFields = (opts) => [
    { name: "cost_kind", label: "Вид затрат", type: "select", options: COST_KINDS },
    { name: "project_id", label: "Инвестпроект", type: "select", options: options(opts.projects, (p) => p.name) },
    { name: "budget_article_id", label: "Статья", type: "select", options: [["", "— не указано —"]] },
  ];
  function wireCost(form, opts, value) {
    const nodes = (name) => {
      const input = form.elements[name];
      return [form.querySelector(`label[for="${input.id}"]`), input].filter(Boolean);
    };
    const toggle = () => {
      const kind = form.elements.cost_kind.value;
      nodes("project_id").forEach((n) => { n.hidden = kind !== "invest"; });
      nodes("budget_article_id").forEach((n) => { n.hidden = !kind; });
      const list = kind === "invest" ? options(opts.invest_articles, (a) => a.name, "— любая статья вложений —")
        : options(opts.opex_articles, (a) => a.name, "— выберите статью расходов —");
      const current = form.elements.budget_article_id.value || value;
      form.elements.budget_article_id.replaceChildren(...list.map(([v, t]) => el("option", { value: v, selected: v === current }, t)));
      form.querySelector(`label[for="${form.elements.budget_article_id.id}"]`).firstChild.textContent =
        kind === "invest" ? "Статья вложений" : "Статья расходов ОПиУ";
    };
    form.elements.cost_kind.addEventListener("change", () => { form.elements.budget_article_id.value = ""; toggle(); });
    toggle();
  }
  const costBody = (v) => ({ cost_kind: v.cost_kind || null, project_id: v.cost_kind === "invest" ? idOrNull(v.project_id) : null,
    budget_article_id: idOrNull(v.budget_article_id) });

  async function setCost(r) {
    let opts;
    try {
      opts = await loadBudgetOptions();
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const dialog = openForm({ title: `Вид затрат: заявка № ${r.number}`, fields: costFields(opts),
      values: { cost_kind: r.cost_kind ?? "", project_id: String(r.project_id ?? "") },
      extra: el("p", { class: "muted small" }, "Бюджет заявки пересчитается: инвестиционная — план вложений проекта на месяц "
        + "и остаток лимита проекта, операционная — план расходов ОПиУ ветки ЦФО по статье минус факт."),
      submitText: "Сохранить",
      onSubmit: async (v) => { await api.post(`/api/asset-requests/${r.id}/cost`, costBody(v)); await load(); } });
    wireCost(dialog.querySelector("form"), opts, String(r.budget_article_id ?? ""));
  }

  // сверх бюджета — отдельно: по ЦФО бюджета, сколько заявок и насколько сверх
  function overSummary(rows) {
    const groups = new Map();
    for (const r of rows) {
      const key = r.budget_cfo_name || r.cfo_name || "—";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(r);
    }
    const line = (title, list) => el("tr", {}, el("td", {}, title), el("td", { class: "num" }, String(list.length)),
      el("td", { class: "num" }, money(sumMoney(list.map((r) => r.amount ?? "0")))),
      el("td", { class: "num" }, money(sumMoney(list.map((r) => r.budget_excess ?? "0")))));
    return el("div", { class: "over-summary" }, el("h3", {}, "Сверх бюджета по ЦФО"),
      el("table", { class: "recon" },
        el("thead", {}, el("tr", {}, ...["ЦФО бюджета", "Заявок", "Сумма заявок", "Сверх бюджета"].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...[...groups].map(([k, list]) => line(k, list)),
          el("tr", { class: "row-key" }, ...line("Итого", rows).children))));
  }

  function actions(r) {
    const list = [];
    const editable = ["draft", "revision", "rejected"].includes(r.status);
    const step = r.route.find((s) => s.state === "current")?.key;
    if (editable && r.can_edit) list.push(["edit", "Изменить"], ["submit", "Подать на согласование"], ["delete", "Удалить"]);
    if (r.status === "pending" && r.can_edit) list.push(["recall", "Отозвать"]);
    if (r.can_act && step === "fd") list.push(["approve", "Принять решение…"]);
    else if (r.can_act) list.push(["step", "Согласовать"]);
    if (r.status === "pending" && (r.can_act || can("supervise"))) list.push(["revise", "Вернуть на доработку"], ["reject", "Отклонить"]);
    if (r.can_mark) list.push(["mark", "Отметка о бюджете…"]);
    if (OPEN.includes(r.status) && can("supervise")) list.push(["cost", "Вид затрат…"]);
    if (r.lease_pending && can("supervise")) list.push(["lease", "Заполнить лизинг…"]);
    if (r.purchase_kind === "leasing" && r.contract_id && !r.lease_pending && r.status !== "cancelled" && can("supervise")) {
      list.push(["unlease", "Снять сведения о лизинге"]);
    }
    if (r.status === "approved" && can("supervise")) list.push(["unapprove", "Вернуть на решение"]);
    if (r.can_delivery) {
      if (r.status === "approved") list.push(["ordered", "Заказано"]);
      if (r.status !== "shipped") list.push(["shipped", "Отгружено"]);
      list.push(["delivered", "Доставлено…"]);
    }
    if (r.can_receive) list.push(["receive", "Принять…"]);
    if (["pending", "approved", "ordered", "shipped", "delivered"].includes(r.status) && can("supervise") && !r.received_quantity) {
      list.push(["cancel", "Отменить закупку"]);
    }
    if (can("supervise")) list.push(["status", "Сменить статус вручную…"]);
    if (!list.length) return "";
    const menu = el("select", { class: "action-menu", "aria-label": `Действие с заявкой ${r.number}` },
      el("option", { value: "" }, "Действие…"), ...list.map(([v, t]) => el("option", { value: v }, t)));
    menu.addEventListener("change", () => { const v = menu.value; menu.value = ""; run(r, v); });
    return menu;
  }

  async function post(r, action, body) {
    try {
      await api.post(`/api/asset-requests/${r.id}/${action}`, body);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function run(r, action) {
    if (action === "edit") editRequest(r);
    else if (action === "status") openManualStatus("asset-requests", r, load);
    else if (action === "delete") {
      if (confirm(`Удалить заявку № ${r.number}?`)) api.delete(`/api/asset-requests/${r.id}`).then(load).catch((err) => toast(err.message, "error"));
    } else if (["submit", "recall", "unapprove", "ordered", "shipped"].includes(action)) {
      post(r, ["ordered", "shipped"].includes(action) ? "delivery" : action, ["ordered", "shipped"].includes(action) ? { status: action } : undefined);
    } else if (action === "approve") decide(r);
    else if (action === "step") {
      openForm({ title: `Согласовать заявку № ${r.number}: ${r.name} × ${r.quantity}`,
        fields: [{ name: "comment", label: "Комментарий (необязательно)", type: "textarea" }], submitText: "Согласовать",
        onSubmit: async (v) => { await api.post(`/api/asset-requests/${r.id}/approve-step`, { comment: v.comment ?? "" }); await load(); } });
    } else if (action === "mark") markBudget(r);
    else if (action === "cost") setCost(r);
    else if (action === "lease") fillLease(r);
    else if (action === "unlease") {
      if (confirm(`Снять сведения о лизинге по заявке № ${r.number}? Платёж заявки уйдёт из платежа договора.`)) {
        api.delete(`/api/asset-requests/${r.id}/lease`).then(load).catch((err) => toast(err.message, "error"));
      }
    }
    else if (["reject", "cancel", "revise"].includes(action)) {
      const titles = { reject: ["Отклонить", "Отклонить"], cancel: ["Отменить закупку по", "Отменить закупку"], revise: ["Вернуть на доработку", "Вернуть"] };
      openForm({ title: `${titles[action][0]} заявку № ${r.number}`,
        fields: [{ name: "reason", label: action === "revise" ? "Что доработать" : "Причина", type: "textarea", required: true }], submitText: titles[action][1],
        onSubmit: async (v) => { await api.post(`/api/asset-requests/${r.id}/${action}`, { reason: v.reason }); await load(); } });
    } else if (action === "receive") receive(r);
    else if (action === "delivered") {
      openForm({ title: `Доставлено: заявка № ${r.number}`, fields: [{ name: "on", label: "Дата доставки", type: "date", required: true }],
        values: { on: new Date().toISOString().slice(0, 10) }, submitText: "Отметить",
        onSubmit: async (v) => { await api.post(`/api/asset-requests/${r.id}/delivery`, { status: "delivered", on: v.on }); await load(); } });
    }
  }

  // отметка о бюджете вручную (финансовый директор) или снова по расчёту
  function markBudget(r) {
    const calc = budgetCalc(r);
    openForm({ title: `Отметка о бюджете: заявка № ${r.number}`,
      fields: [
        { name: "mark", label: "Отметка", type: "select", options: [["", "По расчёту"], ["within", "В рамках бюджета"], ["over", "Сверх бюджета"]] },
        { name: "comment", label: "Почему вручную", type: "textarea" },
      ],
      extra: el("p", { class: "muted small" }, `Расчёт (${r.budget_cfo_name || r.cfo_name}): ${calc}.`
        + " Сверх бюджета заявку согласует CEO, в рамках — сразу финансовый директор."),
      values: { mark: r.budget_manual ? r.budget_mark : "", comment: r.budget_comment },
      submitText: "Сохранить",
      onSubmit: async (v) => {
        await api.post(`/api/asset-requests/${r.id}/budget`, { mark: v.mark || null, comment: v.comment ?? "" });
        await load();
      },
    });
  }

  // сведения о лизинге, когда появился договор (21.2): договор (выбрать или новый) и платёж по этой заявке;
  // у договора, где лизинг уже заполнен, срок, первый платёж, счёт и статья — общие
  async function fillLease(r) {
    let ref;
    let leases;
    try {
      [ref, leases] = await Promise.all([loadReferences(ctx.org().id), api.get(`/api/organizations/${ctx.org().id}/leasing`)]);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const own = leases.filter((c) => c.legal_entity_id === r.legal_entity_id);
    const label = (c) => `${c.title} — ${c.counterparty_name}` + (c.payment ? ` (${money(c.payment)} в мес. × ${c.months})` : "");
    const banks = options(ref.bankAccounts.filter((b) => b.legal_entity_id === r.legal_entity_id), (b) => `${b.name} ··${b.number.slice(-4)}`);
    const articles = ref.leafArticles.filter((a) => a.is_active && a.is_cash_flow && a.cash_flow_direction === "outflow" && !a.is_transfer);
    const dialog = openForm({
      title: `Лизинг по заявке № ${r.number}: ${r.model_name || r.name} × ${r.quantity}`,
      fields: [
        { name: "contract_id", label: "Договор лизинга", type: "select", options: [["", "— новый договор —"], ...own.map((c) => [String(c.id), label(c)])] },
        { name: "counterparty_id", label: "Лизингодатель", type: "select", options: options(ref.counterparties, (c) => c.name) },
        { name: "number", label: "№ договора" },
        { name: "contract_date", label: "Дата договора", type: "date" },
        { name: "lease_payment", label: "Платёж по этой заявке в месяц, ₽", required: true, placeholder: "0,00" },
        { name: "lease_months", label: "Срок, месяцев", type: "number" },
        { name: "pay_date", label: "Дата первого платежа", type: "date" },
        { name: "bank_account_id", label: "Расчётный счёт", type: "select", options: banks },
        { name: "article_id", label: "Статья ДДС", type: "select", options: options(articles, (a) => ref.articleLabel.get(a.id)) },
      ],
      values: { lease_payment: r.lease_payment ? money(r.lease_payment) : "", lease_months: r.lease_months ? String(r.lease_months) : "",
        article_id: String(ref.articleByKey.get("asset_purchase")?.id ?? "") },
      extra: el("p", { class: "muted small" }, "Если по договору уже заполнен лизинг другой заявки, срок и даты берутся из договора, "
        + "а платёж договора увеличивается на платёж этой заявки. В календаре — периодический платёж «Лизинг: договор № …»."),
      submitText: "Сохранить",
      onSubmit: async (v) => {
        const body = { lease_payment: parseNumber(v.lease_payment) };
        if (v.contract_id) body.contract_id = Number(v.contract_id);
        else Object.assign(body, { counterparty_id: idOrNull(v.counterparty_id), number: v.number ?? "", contract_date: v.contract_date || null });
        const existing = own.find((c) => String(c.id) === v.contract_id);
        if (!existing?.payment) {
          Object.assign(body, { lease_months: v.lease_months ? Number(v.lease_months) : null, pay_date: v.pay_date || null,
            bank_account_id: idOrNull(v.bank_account_id), article_id: idOrNull(v.article_id) });
        }
        await api.post(`/api/asset-requests/${r.id}/lease`, body);
        toast("Лизинг заполнен: платёж — в периодических платежах календаря");
        await load();
      },
    });
    const form = dialog.querySelector("form");
    const nodes = (name) => {
      const input = form.elements[name];
      return [form.querySelector(`label[for="${input.id}"]`), input].filter(Boolean);
    };
    const toggle = () => {
      const existing = own.find((c) => String(c.id) === form.elements.contract_id.value);
      ["counterparty_id", "number", "contract_date"].forEach((n) => nodes(n).forEach((x) => { x.hidden = Boolean(existing); }));
      ["lease_months", "pay_date", "bank_account_id", "article_id"].forEach((n) => nodes(n).forEach((x) => { x.hidden = Boolean(existing?.payment); }));
    };
    form.elements.contract_id.addEventListener("change", toggle);
    toggle();
  }

  // приёмка: каждая принятая штука — единица имущества в реестре (шаг 19.3)
  function receive(r) {
    const ref = ctx.ref();
    const left = r.quantity - r.received_quantity;
    openForm({
      title: `Приёмка по заявке № ${r.number}: ${r.model_name || r.name}`,
      fields: [
        { name: "received_on", label: "Дата приёмки", type: "date", required: true },
        { name: "quantity", label: `Сколько принято (осталось принять: ${left})`, type: "number", required: true },
        { name: "employee_id", label: "Кто принял", type: "select", options: options(ref.employees, (e) => e.full_name) },
        { name: "cfo_id", label: "Место (ЦФО)", type: "select", options: options(ref.cfos, (c) => ref.cfoLabel.get(c.id)) },
        { name: "warehouse_id", label: "Склад", type: "select", options: options(ref.warehouses, (w) => w.name) },
        { name: "mol_id", label: "МОЛ", type: "select", options: options(ref.employees, (e) => e.full_name) },
        { name: "status", label: "Статус", type: "select", options: [["stock", "На складе"], ["in_use", "В эксплуатации"]] },
        { name: "discrepancy", label: "Расхождения (недостача, брак, пересорт)", type: "textarea" },
      ],
      values: { received_on: new Date().toISOString().slice(0, 10), quantity: String(left), employee_id: String(r.employee_id ?? ""),
        cfo_id: String(r.cfo_id ?? ""), mol_id: String(r.employee_id ?? ""), status: "stock" },
      submitText: "Принять",
      onSubmit: async (v) => {
        const res = await api.post(`/api/asset-requests/${r.id}/receive`, {
          received_on: v.received_on, quantity: Number(v.quantity), employee_id: idOrNull(v.employee_id),
          cfo_id: idOrNull(v.cfo_id), warehouse_id: idOrNull(v.warehouse_id), mol_id: idOrNull(v.mol_id),
          status: v.status, discrepancy: v.discrepancy ?? "",
        });
        const numbers = res.receipts.at(-1)?.numbers ?? [];
        toast(`Принято: ${numbers.length} шт. (${numbers[0]}${numbers.length > 1 ? ` — ${numbers.at(-1)}` : ""}) — в реестре имущества`);
        await load();
      },
    });
  }

  async function editRequest(r) {
    const ref = ctx.ref();
    let opts;
    try {
      opts = await loadBudgetOptions();
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const dialog = openForm({
      title: r ? `Заявка на имущество № ${r.number}` : "Новая заявка на имущество",
      fields: [
        { name: "name", label: "Что нужно", required: true, placeholder: "Холодильник для магазина" },
        { name: "quantity", label: "Количество", type: "number", required: true },
        { name: "category_id", label: "Категория", type: "select", options: options(ref.categories, (c) => c.name) },
        // ЦФО обязателен: не выбран — вся компания (22.4); руководитель ЦФО выбирает ЦФО своей ветки
        { name: "cfo_id", label: "Для какого ЦФО", type: "select",
          options: options(ref.cfos, (c) => ref.cfoLabel.get(c.id), ctx.org().cfo_scope ? "— выберите —" : "— вся компания —"),
          required: Boolean(ctx.org().cfo_scope) },
        { name: "legal_entity_id", label: "Юрлицо", type: "select", options: options(ref.entities, (e) => e.short_name) },
        { name: "needed_on", label: "Когда нужно (по бюджету этого месяца проверяется заявка)", type: "date" },
        { name: "executor_id", label: "Кто закупает", type: "select",
          options: options(ref.executors ?? [], (e) => e.name, ctx.org().role === "executor_head" ? "— мой ЦФУ —" : "— сам ЦФО —") },
        { name: "estimated_price", label: "Ориентир цены за единицу, ₽", placeholder: "0,00" },
        ...costFields(opts),
        { name: "justification", label: "Зачем (обоснование)", type: "textarea" },
      ],
      values: r ? { ...Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v === null ? "" : String(v)])),
        estimated_price: r.estimated_price ? money(r.estimated_price) : "" } : { quantity: "1" },
      submitText: r ? "Сохранить" : "Создать",
      onSubmit: async (v) => {
        const body = { name: v.name, quantity: Number(v.quantity || 1), category_id: idOrNull(v.category_id),
          cfo_id: idOrNull(v.cfo_id), legal_entity_id: idOrNull(v.legal_entity_id), needed_on: v.needed_on || null,
          executor_id: idOrNull(v.executor_id),
          estimated_price: v.estimated_price ? parseNumber(v.estimated_price) : null, justification: v.justification ?? "",
          ...costBody(v) };
        if (r) await api.put(`/api/asset-requests/${r.id}`, body);
        else await api.post(`/api/organizations/${ctx.org().id}/asset-requests`, body);
        await load();
      },
    });
    wireCost(dialog.querySelector("form"), opts, String(r?.budget_article_id ?? ""));
  }

  async function decide(r) {
    let ref;
    try {
      ref = await loadReferences(ctx.org().id);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const entityId = String(r.legal_entity_id ?? ref.entities.find((e) => e.is_active)?.id ?? "");
    const contracts = (cp, le) => options(ref.contracts.filter((c) => String(c.counterparty_id) === cp && String(c.legal_entity_id) === le),
      (c) => c.name || `№ ${c.number}`, "— без договора —");
    const banks = (le) => options(ref.bankAccounts.filter((b) => String(b.legal_entity_id) === le), (b) => `${b.name} ··${b.number.slice(-4)}`);
    const articles = ref.leafArticles.filter((a) => a.is_active && a.is_cash_flow && a.cash_flow_direction === "outflow" && !a.is_transfer);
    const dialog = openForm({
      title: `Решение по заявке № ${r.number}: ${r.name} × ${r.quantity}`,
      fields: [
        { name: "model_name", label: "Наименование (модель)", placeholder: r.name },
        { name: "legal_entity_id", label: "Юрлицо, которое покупает", type: "select", required: true,
          options: ref.entities.filter((e) => e.is_active).map((e) => [String(e.id), e.short_name]) },
        { name: "counterparty_id", label: "Поставщик", type: "select", required: true, options: options(ref.counterparties, (c) => c.name) },
        { name: "contract_id", label: "Договор", type: "select", options: contracts("", entityId) },
        { name: "employee_id", label: "Ответственный", type: "select", options: options(ref.employees, (e) => e.full_name) },
        { name: "purchase_kind", label: "Вариант закупки", type: "select", options: KINDS },
        { name: "price", label: "Цена за единицу, ₽", placeholder: "0,00" },
        { name: "lease_payment", label: "Ориентир платежа лизинга в месяц, ₽", placeholder: "0,00" },
        { name: "lease_months", label: "Ориентир срока лизинга, месяцев", type: "number" },
        { name: "pay_date", label: "Дата оплаты", type: "date", required: true },
        { name: "bank_account_id", label: "Расчётный счёт", type: "select", options: banks(entityId) },
        { name: "article_id", label: "Статья ДДС", type: "select", options: options(articles, (a) => ref.articleLabel.get(a.id)) },
        { name: "expected_on", label: "Ожидаемая дата поставки", type: "date" },
      ],
      values: { model_name: r.name, legal_entity_id: entityId, purchase_kind: "prepay",
        price: r.estimated_price ? money(r.estimated_price) : "", article_id: String(ref.articleByKey.get("asset_purchase")?.id ?? "") },
      submitText: "Принять решение",
      onSubmit: async (v) => {
        const leasing = v.purchase_kind === "leasing";
        await api.post(`/api/asset-requests/${r.id}/approve`, {
          model_name: v.model_name ?? "", legal_entity_id: Number(v.legal_entity_id),
          counterparty_id: leasing ? null : idOrNull(v.counterparty_id),
          contract_id: leasing ? null : idOrNull(v.contract_id), employee_id: idOrNull(v.employee_id), purchase_kind: v.purchase_kind,
          price: v.price ? parseNumber(v.price) : null, pay_date: leasing ? null : v.pay_date || null,
          lease_payment: leasing && v.lease_payment ? parseNumber(v.lease_payment) : null,
          lease_months: leasing && v.lease_months ? Number(v.lease_months) : null,
          bank_account_id: idOrNull(v.bank_account_id), article_id: idOrNull(v.article_id), expected_on: v.expected_on || null,
        });
        toast(leasing ? "Решение принято: лизинг. Когда появится договор — «Заполнить лизинг…»" : "Решение принято: оплата в платёжном календаре");
        await load();
      },
    });
    const form = dialog.querySelector("form");
    const { legal_entity_id: entity, counterparty_id: cp, contract_id: contract, bank_account_id: bank, purchase_kind: kind } = form.elements;
    const refill = (node, items) => node.replaceChildren(...items.map(([v, t]) => el("option", { value: v }, t)));
    const nodes = (name) => {
      const input = form.elements[name];
      return [form.querySelector(`label[for="${input.id}"]`), input,
        input.nextElementSibling?.tagName === "SMALL" ? input.nextElementSibling : null].filter(Boolean);
    };
    // лизинг — только отметка: лизингодатель, договор, платёж и дата — потом, «Заполнить лизинг…» (21.2)
    const toggle = () => {
      const leasing = kind.value === "leasing";
      ["lease_payment", "lease_months"].forEach((n) => nodes(n).forEach((x) => { x.hidden = !leasing; }));
      ["counterparty_id", "contract_id", "pay_date", "bank_account_id"].forEach((n) => nodes(n).forEach((x) => { x.hidden = leasing; }));
      cp.required = !leasing;
      form.elements.pay_date.required = !leasing;
    };
    entity.addEventListener("change", () => { refill(contract, contracts(cp.value, entity.value)); refill(bank, banks(entity.value)); });
    cp.addEventListener("change", () => refill(contract, contracts(cp.value, entity.value)));
    kind.addEventListener("change", toggle);
    toggle();
  }

  async function openCard(r) {
    let item;
    let events;
    try {
      [item, events] = await Promise.all([api.get(`/api/asset-requests/${r.id}`), api.get(`/api/asset-requests/${r.id}/events`)]);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const field = (title, value) => (value ? el("tr", {}, el("th", {}, title), el("td", {}, value)) : "");
    const dialog = el("dialog", { class: "dialog wide" });
    dialog.append(
      el("h2", {}, `Заявка на имущество № ${item.number}`),
      el("table", { class: "recon request-card" }, el("tbody", {},
        field("Статус", item.status_title + (item.step_title ? ` — у: ${item.step_title}` : "")
          + (item.reject_reason ? `: ${item.reject_reason}` : "")),
        field("Когда нужно", item.needed_on ? ruDate(item.needed_on) : ""),
        field("Кто закупает", item.executor_name || (item.status !== "draft" ? "сам ЦФО" : "")),
        field("Вид затрат", item.cost_kind_title),
        field("Инвестпроект", item.project_name),
        field(item.cost_kind === "invest" ? "Статья вложений" : "Статья расходов", item.budget_article_name),
        field("Бюджет", item.budget_mark ? `${item.budget_mark_title}${item.budget_manual ? ` (вручную: ${item.budget_comment})` : ""}`
          + ` — ${budgetCalc(item)}${item.budget_cfo_name && item.cost_kind !== "invest" ? ` (${item.budget_cfo_name})` : ""}`
          + (item.budget_excess ? `, сверх на ${money(item.budget_excess)}` : "") : ""),
        field("Что нужно", `${item.name} × ${item.quantity}`), field("Категория", item.category_name),
        field("Для ЦФО", item.cfo_name), field("Юрлицо", item.legal_entity_name), field("Обоснование", item.justification),
        field("Ориентир цены", item.estimated_price ? `${money(item.estimated_price)} ₽ за ед.` : ""),
        field("Наименование (модель)", item.model_name), field("Поставщик", item.counterparty_name),
        field("Договор", item.contract_name), field("Ответственный", item.employee_name),
        field("Вариант закупки", item.purchase_kind_title + (item.lease_pending ? " — ждём договор лизинга" : "")),
        field("Договор лизинга", item.purchase_kind === "leasing" ? item.contract_name : ""),
        field("Цена за единицу", item.price ? `${money(item.price)} ₽` : ""),
        field("Лизинг", item.purchase_kind === "leasing" && item.lease_payment
          ? `${money(item.lease_payment)} ₽ × ${item.lease_months ?? "?"} мес.${item.lease_pending ? " (ориентир)" : ""}` : ""),
        field("Стоимость", item.total ? `${money(item.total)} ₽${item.purchase_kind === "leasing" ? " (с удорожанием)" : ""}` : ""),
        field("Дата оплаты", item.pay_date ? ruDate(item.pay_date) : ""),
        field("Поставка", item.delivered_on ? `доставлено ${ruDate(item.delivered_on)}` : item.expected_on ? `ожидается ${ruDate(item.expected_on)}` : ""),
        field("Подал(а)", item.created_by_name), field("Решение", item.decided_by_name))),
      item.route.length ? el("h3", {}, "Маршрут согласования") : "",
      item.route.length ? el("ol", { class: "asset-route" }, ...item.route.map((x) => el("li", { class: `route-${x.state}` },
        x.title, x.state === "done" ? " — согласовано" : x.state === "current" ? " — сейчас у него" : ""))) : "",
      item.receipts.length ? el("h3", {}, `Приёмки (принято ${item.received_quantity} из ${item.quantity})`) : "",
      item.receipts.length ? el("ul", { class: "asset-receipts" }, ...item.receipts.map((x) => el("li", {},
        `${ruDate(x.received_on)} — ${x.quantity} шт.${x.employee_name ? `, принял(а) ${x.employee_name}` : ""}: `
          + `${x.numbers[0] ?? ""}${x.numbers.length > 1 ? ` — ${x.numbers.at(-1)}` : ""}`,
        x.discrepancy ? el("div", { class: "muted small" }, `Расхождения: ${x.discrepancy}`) : "",
        el("div", { class: "asset-files" },
          ...x.files.map((f) => el("button", { type: "button", class: "link", onclick: () => api.download(`/api/asset-receipt-files/${f.id}`, f.filename)
            .catch((err) => toast(err.message, "error")) }, f.filename)),
          item.can_receive || can("assets_edit") ? receiptFile(x, dialog, item) : "",
          can("assets_edit") ? el("button", { type: "button", class: "link danger", onclick: async () => {
            if (!confirm("Отменить приёмку? Её единицы удалятся из реестра.")) return;
            try {
              await api.delete(`/api/asset-receipts/${x.id}`);
              dialog.close();
              await load();
            } catch (err) {
              toast(err.message, "error");
            }
          } }, "Отменить приёмку") : "")))) : "",
      item.payments.length ? el("h3", {}, "Оплаты в платёжном календаре") : "",
      item.payments.length ? el("ul", {}, ...item.payments.map((p) => el("li", {},
        `Заявка на оплату № ${p.number} — ${ruDate(p.pay_date)}, ${money(p.amount)} ₽, ${p.status_title}`))) : "",
      el("h3", {}, "История"),
      el("ul", { class: "asset-history" }, ...events.map((e) => el("li", {},
        `${new Date(e.at).toLocaleString("ru-RU")} — ${e.user_name} ${ACTIONS[e.action] ?? e.action}`,
        e.details ? el("div", { class: "muted small" }, e.details) : ""))),
      el("div", { class: "form-actions" }, el("button", { type: "button", class: "secondary", onclick: () => dialog.close() }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  // акт, фото к приёмке
  function receiptFile(receipt, dialog, item) {
    const input = el("input", { type: "file", hidden: true, "aria-label": "Акт или фото приёмки" });
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        await api.upload(`/api/asset-receipts/${receipt.id}/files?filename=${encodeURIComponent(file.name)}`, file);
        dialog.close();
        await openCard(item);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    return el("span", {}, input, el("button", { type: "button", class: "link", onclick: () => input.click() }, "+ акт / фото"));
  }

  return { show: async () => { render(); await load(); } };
}

// Вкладка «Заявки на имущество» (docs/TZ.md, раздел 28, «Детали 19.2»): что нужно → решение ФД (наименование,
// поставщик, ответственный, вариант закупки) → оплаты в платёжном календаре → статус поставки.
import { api } from "../api.js";
import { date as ruDate, money, parseNumber } from "../format.js";
import { el, instruction, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const STATUS_FILTER = [["", "Все"], ["draft,rejected", "Черновики и отклонённые"], ["pending", "На решении ФД"],
  ["approved,ordered,shipped", "Закупаются"], ["delivered", "Доставлены"], ["received,cancelled", "Закрытые"]];
const KINDS = [["prepay", "Предоплата"], ["deferral", "Отсрочка"], ["leasing", "Лизинг"]];
const ACTIONS = { created: "создал(а)", edited: "изменил(а)", submitted: "подал(а) на решение ФД", recalled: "отозвал(а)",
  approved: "принял(а) решение", rejected: "отклонил(а)", unapproved: "вернул(а) на решение", cancelled: "отменил(а)",
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
      instruction("Заявка на имущество: что нужно, сколько, для какого ЦФО и зачем. Подают инженер, оператор АХО, "
        + "финансовый директор. Финансовый директор принимает решение: наименование, поставщик, ответственный, вариант "
        + "закупки. Предоплата и отсрочка сразу попадают в платёжный календарь согласованной заявкой на оплату; лизинг — "
        + "периодическим платежом (заявки на 3 месяца вперёд создаются сами). Дальше оператор АХО отмечает поставку: "
        + "заказано → отгружено → доставлено. Приёмка и создание карточек — следующий шаг."),
      el("div", { class: "filters" },
        can("asset_request") ? el("button", { type: "button", onclick: () => editRequest(null) }, "+ Заявка на имущество") : "",
        el("label", { class: "inline" }, "Статус ", status),
        el("label", { class: "check" }, mineBox, " Мои")),
      listBox,
    );
  }

  async function load() {
    const p = new URLSearchParams();
    if (filter) p.set("status", filter);
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
    listBox.replaceChildren(el("table", { class: "recon requests asset-requests" },
      el("thead", {}, el("tr", {}, ...["№", "Дата", "Что нужно", "ЦФО", "Поставщик", "Закупка", "Сумма", "Поставка", "Статус", "Действие"]
        .map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows.map((r) => {
        const tr = el("tr", { "data-number": r.number },
          el("td", {}, String(r.number)),
          el("td", {}, ruDate(r.created_at.slice(0, 10))),
          el("td", {}, `${r.name} × ${r.quantity}`, r.model_name && r.model_name !== r.name ? el("div", { class: "muted small" }, r.model_name) : ""),
          el("td", {}, r.cfo_name),
          el("td", {}, r.counterparty_name),
          el("td", {}, r.purchase_kind_title, r.purchase_kind === "leasing" ? el("div", { class: "muted small" }, `${money(r.lease_payment)} × ${r.lease_months} мес.`) : ""),
          el("td", { class: "num" }, r.total ? money(r.total) : r.estimated_price ? `≈ ${money(r.estimated_price)} за ед.` : ""),
          el("td", {}, r.delivered_on ? `доставлено ${ruDate(r.delivered_on)}` : r.expected_on ? `ожидается ${ruDate(r.expected_on)}` : "",
            r.received_quantity ? el("div", { class: "muted small" }, `принято ${r.received_quantity} из ${r.quantity}`) : ""),
          el("td", {}, el("span", { class: `badge areq-${r.status}` }, r.status_title)),
          el("td", { class: "req-actions" }, actions(r)));
        tr.addEventListener("click", (e) => { if (!e.target.closest("select, button")) openCard(r); });
        return tr;
      }))));
  }

  function actions(r) {
    const list = [];
    const editable = ["draft", "rejected"].includes(r.status);
    if (editable && r.can_edit) list.push(["edit", "Изменить"], ["submit", "Подать на решение ФД"], ["delete", "Удалить"]);
    if (r.status === "pending" && r.can_edit) list.push(["recall", "Отозвать"]);
    if (r.status === "pending" && can("supervise")) list.push(["approve", "Принять решение…"], ["reject", "Отклонить"]);
    if (r.status === "approved" && can("supervise")) list.push(["unapprove", "Вернуть на решение"]);
    if (["approved", "ordered", "shipped"].includes(r.status) && can("assets_edit")) {
      if (r.status === "approved") list.push(["ordered", "Заказано"]);
      if (r.status !== "shipped") list.push(["shipped", "Отгружено"]);
      list.push(["delivered", "Доставлено…"]);
    }
    if (r.can_receive) list.push(["receive", "Принять…"]);
    if (["pending", "approved", "ordered", "shipped", "delivered"].includes(r.status) && can("supervise") && !r.received_quantity) {
      list.push(["cancel", "Отменить закупку"]);
    }
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
    else if (action === "delete") {
      if (confirm(`Удалить заявку № ${r.number}?`)) api.delete(`/api/asset-requests/${r.id}`).then(load).catch((err) => toast(err.message, "error"));
    } else if (["submit", "recall", "unapprove", "ordered", "shipped"].includes(action)) {
      post(r, ["ordered", "shipped"].includes(action) ? "delivery" : action, ["ordered", "shipped"].includes(action) ? { status: action } : undefined);
    } else if (action === "approve") decide(r);
    else if (action === "reject" || action === "cancel") {
      openForm({ title: `${action === "reject" ? "Отклонить" : "Отменить закупку по"} заявку № ${r.number}`,
        fields: [{ name: "reason", label: "Причина", type: "textarea", required: true }], submitText: action === "reject" ? "Отклонить" : "Отменить закупку",
        onSubmit: async (v) => { await api.post(`/api/asset-requests/${r.id}/${action}`, { reason: v.reason }); await load(); } });
    } else if (action === "receive") receive(r);
    else if (action === "delivered") {
      openForm({ title: `Доставлено: заявка № ${r.number}`, fields: [{ name: "on", label: "Дата доставки", type: "date", required: true }],
        values: { on: new Date().toISOString().slice(0, 10) }, submitText: "Отметить",
        onSubmit: async (v) => { await api.post(`/api/asset-requests/${r.id}/delivery`, { status: "delivered", on: v.on }); await load(); } });
    }
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

  function editRequest(r) {
    const ref = ctx.ref();
    openForm({
      title: r ? `Заявка на имущество № ${r.number}` : "Новая заявка на имущество",
      fields: [
        { name: "name", label: "Что нужно", required: true, placeholder: "Холодильник для магазина" },
        { name: "quantity", label: "Количество", type: "number", required: true },
        { name: "category_id", label: "Категория", type: "select", options: options(ref.categories, (c) => c.name) },
        { name: "cfo_id", label: "Для какого ЦФО", type: "select", options: options(ref.cfos, (c) => ref.cfoLabel.get(c.id)),
          required: Boolean(ctx.org().cfo_scope) },
        { name: "legal_entity_id", label: "Юрлицо", type: "select", options: options(ref.entities, (e) => e.short_name) },
        { name: "estimated_price", label: "Ориентир цены за единицу, ₽", placeholder: "0,00" },
        { name: "justification", label: "Зачем (обоснование)", type: "textarea" },
      ],
      values: r ? { ...Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v === null ? "" : String(v)])),
        estimated_price: r.estimated_price ? money(r.estimated_price) : "" } : { quantity: "1" },
      submitText: r ? "Сохранить" : "Создать",
      onSubmit: async (v) => {
        const body = { name: v.name, quantity: Number(v.quantity || 1), category_id: idOrNull(v.category_id),
          cfo_id: idOrNull(v.cfo_id), legal_entity_id: idOrNull(v.legal_entity_id),
          estimated_price: v.estimated_price ? parseNumber(v.estimated_price) : null, justification: v.justification ?? "" };
        if (r) await api.put(`/api/asset-requests/${r.id}`, body);
        else await api.post(`/api/organizations/${ctx.org().id}/asset-requests`, body);
        await load();
      },
    });
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
        { name: "lease_payment", label: "Ежемесячный платёж лизинга, ₽", placeholder: "0,00" },
        { name: "lease_months", label: "Срок лизинга, месяцев", type: "number" },
        { name: "pay_date", label: "Дата оплаты (у лизинга — первого платежа)", type: "date", required: true },
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
          model_name: v.model_name ?? "", legal_entity_id: Number(v.legal_entity_id), counterparty_id: Number(v.counterparty_id),
          contract_id: idOrNull(v.contract_id), employee_id: idOrNull(v.employee_id), purchase_kind: v.purchase_kind,
          price: v.price ? parseNumber(v.price) : null, pay_date: v.pay_date,
          lease_payment: leasing && v.lease_payment ? parseNumber(v.lease_payment) : null,
          lease_months: leasing && v.lease_months ? Number(v.lease_months) : null,
          bank_account_id: idOrNull(v.bank_account_id), article_id: idOrNull(v.article_id), expected_on: v.expected_on || null,
        });
        toast(leasing ? "Решение принято: лизинг добавлен в периодические платежи" : "Решение принято: оплата в платёжном календаре");
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
    const toggle = () => {
      const leasing = kind.value === "leasing";
      ["lease_payment", "lease_months"].forEach((n) => nodes(n).forEach((x) => { x.hidden = !leasing; }));
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
        field("Статус", item.status_title + (item.reject_reason ? `: ${item.reject_reason}` : "")),
        field("Что нужно", `${item.name} × ${item.quantity}`), field("Категория", item.category_name),
        field("Для ЦФО", item.cfo_name), field("Юрлицо", item.legal_entity_name), field("Обоснование", item.justification),
        field("Ориентир цены", item.estimated_price ? `${money(item.estimated_price)} ₽ за ед.` : ""),
        field("Наименование (модель)", item.model_name), field("Поставщик", item.counterparty_name),
        field("Договор", item.contract_name), field("Ответственный", item.employee_name),
        field("Вариант закупки", item.purchase_kind_title),
        field("Цена за единицу", item.price ? `${money(item.price)} ₽` : ""),
        field("Лизинг", item.purchase_kind === "leasing" ? `${money(item.lease_payment)} ₽ × ${item.lease_months} мес.` : ""),
        field("Стоимость", item.total ? `${money(item.total)} ₽${item.purchase_kind === "leasing" ? " (с удорожанием)" : ""}` : ""),
        field("Дата оплаты", item.pay_date ? ruDate(item.pay_date) : ""),
        field("Поставка", item.delivered_on ? `доставлено ${ruDate(item.delivered_on)}` : item.expected_on ? `ожидается ${ruDate(item.expected_on)}` : ""),
        field("Подал(а)", item.created_by_name), field("Решение", item.decided_by_name))),
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

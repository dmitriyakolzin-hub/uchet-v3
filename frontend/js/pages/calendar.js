// «Платёжный календарь»: заявки на оплату и ожидаемые поступления — подача, согласование, оплата (docs/TZ.md, раздел 18).
import { api } from "../api.js";
import { date, money, monthBounds, parseNumber } from "../format.js";
import { openOperation } from "../opform.js";
import { el, instruction, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const TAB_KEY = "uchet3_calendar_tab";
const STATUS = { draft: "Черновик", pending: "На согласовании", revision: "На доработке", approved: "Согласована",
  rejected: "Отклонена", paid: "Оплачена" };
const EVENT_TITLES = { created: "создал(а)", submitted: "подал(а) на согласование", approved: "согласовал(а)",
  rejected: "отклонил(а)", revision: "вернул(а) на доработку", recalled: "отозвал(а)", unapproved: "вернул(а) на согласование",
  moved: "перенёс(ла) на", amount: "изменил(а) сумму", paid: "отметил(а) оплату", unpaid: "снял(а) оплату", file: "приложил(а) файл",
  file_removed: "убрал(а) файл", edited: "изменил(а)", periodic_updated: "обновлено по договору", sent: "отправил(а) в банк",
  unsent: "снял(а) отметку «отправлено в банк»" };
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
  const filter = { date_from: from, date_to: to, status: "", direction: "", who: "", channel: "" };

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
  // «чьи»: ждут моего согласования / мои; канал: безнал или касса (кассовые заявки)
  const query = () => {
    const { who, ...rest } = filter;
    const p = new URLSearchParams(Object.entries(rest).filter(([, v]) => v));
    if (who) p.set(who, "true");
    return p.toString();
  };

  const tabs = el("div", { class: "tabs" });
  const content = el("div");
  const requestsBox = el("div", {},
    instruction("Заявка: черновик → на согласовании (по маршруту: руководитель ЦФУ или ЦФО — встречная сторона, "
      + "финансовый менеджер, CEO — инвестиции и суммы больше 1 000 000 ₽, финансовый директор) → согласована (в реестре "
      + "на оплату) → оплачена. Платёж из выписки на ту же сумму тому же контрагенту сам отметит заявку оплаченной. "
      + "Ожидаемые поступления согласования не требуют."),
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "С ", input("date", "date_from", "С даты")),
      el("label", { class: "inline" }, "по ", input("date", "date_to", "По дату")),
      select("status", "Статус", [["", "Все статусы"], ...Object.entries(STATUS)]),
      select("direction", "Вид", [["", "Выплаты и поступления"], ["out", "Выплаты"], ["in", "Поступления"]]),
      select("who", "Чьи", [["", "Все заявки"], ["to_approve", "Ждут моего согласования"], ["mine", "Мои заявки"]]),
      select("channel", "Безнал или касса", [["", "Безнал и касса"], ["bank", "Безнал"], ["cash", "Касса"]]),
      el("button", { type: "button", onclick: () => edit(null, "out") }, "Новая заявка"),
      el("button", { type: "button", class: "secondary", onclick: () => edit(null, "out", true) }, "Кассовая заявка"),
      el("button", { type: "button", class: "secondary", onclick: () => edit(null, "in") }, "Ожидаемое поступление"),
      el("button", { type: "button", class: "secondary",
        onclick: () => api.download(`/api/organizations/${org.id}/payment-requests.xlsx?${query()}`, "payment_requests.xlsx")
          .catch((err) => toast(err.message, "error")) }, "Скачать в Excel")),
    listBox, totalsBox,
  );
  const periodicBox = el("div");
  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Платёжный календарь"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    tabs, content,
  );
  const registryBox = el("div");
  const suppliersBox = el("div");
  const sheetBox = el("div");
  let tab = ["requests", "periodic", "registry", "suppliers"].includes(recall(TAB_KEY)) ? recall(TAB_KEY) : "sheet";
  // руководитель ЦФУ и руководитель ЦФО видят только заявки
  const onlyRequests = () => org.restricted || org.role === "executor_head";
  function openTab(key) {
    tab = onlyRequests() && !["requests", "periodic"].includes(key) ? "requests" : key;
    if (["registry", "suppliers"].includes(tab) && !can("post")) tab = "sheet";
    remember(TAB_KEY, tab);
    const list = onlyRequests() ? [["requests", "Заявки"], ["periodic", "Периодические платежи"]]
      : [["sheet", "Календарь"], ["requests", "Заявки"],
        ...(can("post") ? [["suppliers", "Накладные поставщиков"], ["registry", "Реестр на оплату"]] : []),
        ["periodic", "Периодические платежи"]];
    tabs.replaceChildren(...list.map(([k, title]) =>
      el("button", { type: "button", class: `tab${k === tab ? " active" : ""}`, onclick: () => openTab(k) }, title)));
    content.replaceChildren({ requests: requestsBox, periodic: periodicBox, registry: registryBox, suppliers: suppliersBox,
      sheet: sheetBox }[tab]);
    return { sheet: loadSheet, requests: load, periodic: loadPeriodic, registry: loadRegistry, suppliers: loadSuppliers }[tab]();
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

  // подавать и править — право черновиков или руководитель ЦФУ; согласовывать — тот, чей сейчас шаг (r.can_act)
  const author = () => can("draft") || org.role === "executor_head";
  // действия по заявке — выпадающим списком «Действие» (шаг 16.4)
  function actions(r) {
    const button = (text, onclick) => ({ text, onclick });
    const run = (action, body) => act(r, action, body);
    const list = [];
    if (["draft", "rejected", "revision"].includes(r.status) && author()) {
      list.push(button("Изменить", () => edit(r, r.direction)), button(r.direction === "in" ? "Подтвердить" : "На согласование", () => run("submit")));
      if (r.status === "draft") list.push(button("Удалить", () => remove(r)));
    }
    if (["pending", "approved"].includes(r.status) && can("supervise") && !r.linked_id) {
      list.push(button("Изменить", () => edit(r, r.direction)));
    }
    if (r.status === "pending") {
      if (r.can_act) {
        list.push(button("Согласовать", () => run("approve")), button("На доработку", () => reason(r, "revise")),
          button("Отклонить", () => reason(r, "reject")));
      }
      if (author()) list.push(button("Отозвать", () => run("recall")));
      if (can("post")) list.push(button("Перенести", () => move(r)));
    }
    if (r.status === "approved") {
      if (can("post")) list.push(button("Оплачено", () => pay(r)), button("Перенести", () => move(r)));
      if (can("supervise")) {
        list.push(button("Отклонить", () => reason(r, "reject")),
          button(r.direction === "in" ? "В черновик" : "Вернуть на согласование", () => run("unapprove")));
      }
    }
    if (r.status === "paid" && can("post")) list.push(button("Снять оплату", () => run("unpay")));
    list.push(button("История", () => history(r)));
    const menu = el("select", { class: "action-menu", "aria-label": `Действие по заявке № ${r.number}` },
      el("option", { value: "" }, "Действие…"), ...list.map((a, i) => el("option", { value: String(i) }, a.text)));
    menu.addEventListener("change", () => {
      const chosen = list[Number(menu.value)];
      menu.value = "";
      chosen?.onclick();
    });
    return el("td", { class: "req-actions" }, menu);
  }

  // статус: у черновика пусто; на согласовании — кто уже согласовал и у кого сейчас; согласована — кем
  const STEP_SHORT = { executor: "ЦФУ", cfo: "ЦФО", fm: "ФМ", ceo: "CEO", fd: "ФД" };
  function statusCell(r) {
    const who = (r.approved_steps ?? []).map((st) => STEP_SHORT[st] ?? st).join(", ");
    let main = null;
    let note = null;
    if (r.status === "pending") {
      main = el("span", { class: "status req-pending" }, "На согласовании");
      note = el("div", { class: r.can_act ? "my-step" : "muted", title: `Маршрут: ${r.route_titles.join(" → ")}` },
        (who ? `согласовали: ${who}; ` : "") + (r.can_act ? `ждёт вас (${r.step_title})` : `у: ${r.step_title}`));
    } else if (r.status === "approved") {
      main = el("span", { class: "status req-approved" }, who ? `Согласована (${who})` : "Согласована");
      if (r.sent_at) note = el("div", { class: "muted" }, `отправлено в банк ${new Date(r.sent_at).toLocaleDateString("ru-RU")}`);
    } else if (r.status === "paid") {
      main = el("span", { class: "status req-paid" }, `Оплачена ${date(r.paid_on)}`);
      note = r.confirmed_by_statement ? el("div", { class: "muted" }, "по выписке") : null;
    } else if (r.status !== "draft") {
      main = el("span", { class: `status req-${r.status}` }, STATUS[r.status]);
      note = r.reject_reason ? el("div", { class: "muted" }, `Причина: ${r.reject_reason}`) : null;
    }
    return el("td", {}, main, note);
  }

  // файлы заявки: скачать, приложить (автор и финансовая служба), убрать
  function filesCell(r) {
    const canAttach = r.status !== "paid" && (author() || can("post"));
    const input = el("input", { type: "file", hidden: true, accept: ".pdf,.jpg,.jpeg,.png,.xlsx,.xls,.doc,.docx",
      "aria-label": `Файл к заявке № ${r.number}` });
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        await api.upload(`/api/payment-requests/${r.id}/files?filename=${encodeURIComponent(file.name)}`, file);
        toast("Файл приложен");
        await load();
      } catch (err) {
        toast(err.message, "error");
      }
    });
    return el("td", { class: "req-files" },
      ...r.files.map((f) => el("div", {},
        el("button", { type: "button", class: "link", onclick: () => api.download(`/api/payment-request-files/${f.id}`, f.filename)
          .catch((err) => toast(err.message, "error")) }, f.filename),
        canAttach ? el("button", { type: "button", class: "link danger", title: "Убрать файл", onclick: async () => {
          if (!confirm(`Убрать файл «${f.filename}»?`)) return;
          try {
            await api.delete(`/api/payment-request-files/${f.id}`);
            await load();
          } catch (err) {
            toast(err.message, "error");
          }
        } }, " ×") : "")),
      canAttach ? el("button", { type: "button", class: "link", onclick: () => input.click() }, "+ файл") : "", input);
  }

  async function history(r) {
    try {
      const events = await api.get(`/api/payment-requests/${r.id}/events`);
      const dialog = el("dialog", { class: "dialog wide" },
        el("h2", {}, `История заявки № ${r.number}`),
        r.route_titles.length ? el("p", { class: "muted" }, `Маршрут: ${r.route_titles.join(" → ")}`) : "",
        el("ul", {}, ...events.map((e) => el("li", {},
          `${new Date(e.at).toLocaleString("ru-RU")} — ${e.user_name} ${EVENT_TITLES[e.action] ?? e.action}`
            + (e.step_title ? ` (${e.step_title})` : "") + (e.comment ? `: ${e.comment}` : "")))),
        el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")));
      document.body.append(dialog);
      dialog.addEventListener("close", () => dialog.remove());
      dialog.showModal();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function load() {
    let data;
    try {
      data = await api.get(`/api/organizations/${org.id}/payment-requests?${query()}`);
    } catch (err) {
      listBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    // в списке — главное; остальные поля заявки — в карточке (щелчок по строке) и при правке
    listBox.replaceChildren(data.rows.length
      ? el("table", { class: "recon versions requests" },
        el("thead", {}, el("tr", {}, ...["Дата оплаты", "Контрагент", "Назначение", "Сумма", "Файл", "Статус", "Действие"]
          .map((t) => el("th", {}, t)))),
        el("tbody", {}, ...data.rows.map((r) => {
          const tr = el("tr", { "data-number": r.number, title: `Заявка № ${r.number} — щёлкните, чтобы открыть`,
            class: `clickable-row${Number(r.budget?.over ?? 0) > 0 && r.status !== "paid" ? " over-budget-row" : ""}` },
          el("td", {}, date(r.pay_date), r.direction === "in" ? el("div", { class: "muted small" }, "поступление") : null),
          el("td", {}, r.counterparty_name || "—", r.group_name ? el("div", { class: "muted" }, `внутри группы → ${r.group_name}`) : null),
          el("td", {}, r.description || ""),
          el("td", { class: "num" }, money(r.amount)), filesCell(r), statusCell(r), actions(r));
          tr.addEventListener("click", (e) => {
            if (e.target.closest("button, a, input, label, select")) return;
            requestView(r.id);
          });
          return tr;
        })))
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

  function reason(r, action) {
    const titles = { reject: ["Отклонить", "Причина отказа"], revise: ["На доработку", "Что доработать"] };
    openForm({
      title: `${titles[action][0]}: заявка № ${r.number}`,
      fields: [{ name: "reason", label: titles[action][1], type: "textarea", required: true }],
      submitText: titles[action][0],
      onSubmit: async (v) => { await api.post(`/api/payment-requests/${r.id}/${action}`, v); await load(); },
    });
  }

  function pay(r, after = load) {
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
        await after();
      },
    });
  }

  // откуда платим: расчётный счёт юрлица или касса (ЦФО с кассой); руководитель ЦФО счёт не выбирает
  function sourceOptions(entityId) {
    const banks = onlyRequests() ? [] : ref.bankAccounts.filter((b) => String(b.legal_entity_id) === String(entityId))
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

  function edit(r, direction, cashRequest = false) {
    const entities = ref.entities.filter((e) => e.is_active);
    const flow = direction === "out" ? "outflow" : "inflow";
    const articles = ref.leafArticles.filter((a) => a.is_active && a.is_cash_flow && !a.is_transfer && a.cash_flow_direction === flow);
    const cfos = ref.cfos.filter(inBranch);
    const entityId = String(r?.legal_entity_id ?? entities[0]?.id ?? "");
    const contractOptions = (cpId, leId) => options(ref.contracts.filter((c) => String(c.counterparty_id) === cpId
      && String(c.legal_entity_id) === leId), (c) => c.name || `№ ${c.number}`);
    const dialog = openForm({
      title: r ? `Заявка № ${r.number}` : direction === "in" ? "Ожидаемое поступление"
        : cashRequest ? "Кассовая заявка (выдача из кассы)" : "Новая заявка на оплату",
      fields: [
        { name: "pay_date", label: direction === "in" ? "Ожидаемая дата" : "Дата оплаты", type: "date", required: true },
        { name: "legal_entity_id", label: "Юрлицо", type: "select", options: entities.map((e) => [String(e.id), e.short_name]) },
        { name: "source", label: direction === "in" ? "Куда поступит" : "Откуда платим", type: "select", options: sourceOptions(entityId),
          hint: onlyRequests() ? "Расчётный счёт выбирает финансовая служба" : "Можно выбрать при согласовании" },
        { name: "counterparty_id", label: "Контрагент", type: "select", options: options(ref.counterparties, (c) => c.name) },
        { name: "contract_id", label: "Договор", type: "select", options: contractOptions(String(r?.counterparty_id ?? ""), entityId) },
        { name: "article_id", label: "Статья ДДС", type: "select", options: options(articles, (a) => ref.articleLabel.get(a.id)) },
        { name: "cfo_id", label: "ЦФО", type: "select", options: options(cfos, (c) => ref.cfoLabel.get(c.id)),
          required: Boolean(org.restricted) },
        { name: "executor_id", label: "ЦФУ", type: "select", required: Boolean(org.executor_scope),
          options: options(org.executor_scope ? ref.executors.filter((e) => org.executor_scope.includes(e.id)) : ref.executors,
            (e) => ref.executorLabel.get(e.id)) },
        { name: "amount", label: "Сумма", required: true, placeholder: "0,00" },
        { name: "description", label: "Назначение", type: "textarea" },
      ],
      values: {
        pay_date: r?.pay_date ?? ref.today, legal_entity_id: entityId,
        source: r ? sourceValue(r) : cashRequest ? (sourceOptions(entityId).find(([v]) => v.startsWith("cash:"))?.[0] ?? "") : "",
        counterparty_id: String(r?.counterparty_id ?? ""), contract_id: String(r?.contract_id ?? ""),
        article_id: String(r?.article_id ?? ""), cfo_id: String(r?.cfo_id ?? (org.cfo_scope ?? "")),
        executor_id: String(r?.executor_id ?? (org.executor_scope?.[0] ?? "")), amount: r ? money(r.amount) : "",
        description: r?.description ?? "",
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

  // --- периодические платежи по договорам: условия и заявки текущего и двух следующих месяцев (шаг 14.3) ---
  async function loadPeriodic() {
    let rows;
    try {
      rows = await api.get(`/api/organizations/${org.id}/periodic-payments`);
    } catch (err) {
      periodicBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    const months = [...new Map(rows.flatMap((r) => r.months).map((m) => [m.period, m])).keys()].sort();
    const monthTitle = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
    const editable = can("post");
    periodicBox.replaceChildren(
      instruction("Периодический платёж — сумма в определённое число месяца: ежемесячно, раз в квартал или раз в год. "
        + "По договору (здесь или в «Справочники» → «Договоры» → «периодический») или без него — зарплата, налоги, "
        + "кредит. По условиям сама создаётся заявка на нужное число (выходной — рабочий день раньше) на текущий и два "
        + "следующих месяца; согласует её финансовый директор. Добавить и изменить — финансовый менеджер, финансовый "
        + "директор, администратор."),
      editable ? el("div", { class: "filters" },
        el("button", { type: "button", onclick: () => periodicForm(null) }, "+ Периодический платёж")) : "",
      rows.length ? el("table", { class: "recon versions periodic" },
        el("thead", {}, el("tr", {}, ...["Платёж", "Назначение", "Сумма", "Правило", "ЦФО / ЦФУ",
          ...months.map(monthTitle), ...(editable ? [""] : [])].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...rows.map((r) => el("tr", { "data-periodic": r.name || r.counterparty_name },
          el("td", {}, r.name || r.counterparty_name || "—",
            r.name && r.counterparty_name ? el("div", { class: "muted" }, r.counterparty_name) : null,
            r.contract_name ? el("div", { class: "muted" }, `Договор ${r.contract_name}`) : null),
          el("td", {}, r.description || r.article_name),
          el("td", { class: "num" }, money(r.amount)),
          el("td", {}, `${r.frequency_title}, ${r.day}-го`,
            el("div", { class: "muted small" }, `с ${date(r.valid_from)}${r.valid_to ? ` по ${date(r.valid_to)}` : ""}`)),
          el("td", {}, [r.cfo_name, r.executor_name].filter(Boolean).join(" · ")),
          ...months.map((period) => {
            const m = r.months.find((x) => x.period === period);
            return el("td", {}, m ? [date(m.pay_date), el("div", { class: `status req-${m.status}` }, m.status_title),
              m.paid_on ? el("div", { class: "muted" }, `оплачено ${date(m.paid_on)}`) : ""] : "");
          }),
          editable ? el("td", { class: "req-actions" },
            el("button", { type: "button", class: "link", onclick: () => periodicForm(r) }, "Изменить"),
            el("button", { type: "button", class: "link", onclick: async () => {
              if (!confirm("Удалить условия периодического платежа?")) return;
              try {
                await api.delete(`/api/contract-payments/${r.id}`);
                await loadPeriodic();
              } catch (err) {
                toast(err.message, "error");
              }
            } }, "Удалить")) : ""))))
        : el("p", { class: "muted" }, "Периодических платежей пока нет."));
  }

  // периодический платёж: по договору или без него (зарплата, налоги, кредит)
  function periodicForm(r) {
    const entities = ref.entities.filter((e) => e.is_active || e.id === r?.legal_entity_id);
    const entityId = String(r?.legal_entity_id ?? entities[0]?.id ?? "");
    const articles = ref.leafArticles.filter((a) => a.is_cash_flow && a.cash_flow_direction === "outflow" && !a.is_transfer);
    const banks = (le) => options(ref.bankAccounts.filter((b) => String(b.legal_entity_id) === le), (b) => `${b.name} ··${b.number.slice(-4)}`);
    const contracts = (cp, le) => [["", "— без договора —"], ...ref.contracts
      .filter((c) => String(c.counterparty_id) === cp && String(c.legal_entity_id) === le)
      .map((c) => [String(c.id), c.name || `№ ${c.number}`])];
    const byContract = Boolean(r?.contract_id);
    const dialog = openForm({
      title: r ? "Периодический платёж" : "Новый периодический платёж",
      fields: [
        { name: "name", label: "Название", placeholder: "Зарплата, НДФЛ, кредит Сбер…" },
        { name: "legal_entity_id", label: "Юрлицо", type: "select", options: entities.map((e) => [String(e.id), e.short_name]) },
        { name: "counterparty_id", label: "Контрагент", type: "select", options: options(ref.counterparties, (c) => c.name) },
        { name: "contract_id", label: "Договор", type: "select", options: contracts(String(r?.counterparty_id ?? ""), entityId),
          hint: byContract ? "Платёж по договору: юрлицо, контрагент и договор не меняются" : "" },
        { name: "frequency", label: "Правило", type: "select",
          options: [["month", "Ежемесячно"], ["quarter", "Раз в квартал"], ["year", "Раз в год"]] },
        { name: "day", label: "Число месяца", type: "number", required: true, hint: "Выходной или праздник — ближайший рабочий день раньше" },
        { name: "valid_from", label: "Действует с", type: "date", required: true,
          hint: "Квартал и год считаются от этого месяца" },
        { name: "valid_to", label: "Действует по", type: "date" },
        { name: "amount", label: "Сумма", required: true },
        { name: "bank_account_id", label: "Расчётный счёт", type: "select", options: banks(entityId) },
        { name: "article_id", label: "Статья ДДС", type: "select", options: options(articles, (a) => ref.articleLabel.get(a.id) ?? a.name) },
        { name: "cfo_id", label: "ЦФО", type: "select", options: options(ref.cfos, (c) => ref.cfoLabel.get(c.id)) },
        { name: "executor_id", label: "ЦФУ", type: "select", options: options(ref.executors, (e) => ref.executorLabel.get(e.id)) },
        { name: "description", label: "Назначение", type: "textarea" },
      ],
      values: r ? { name: r.name, legal_entity_id: entityId, counterparty_id: String(r.counterparty_id ?? ""),
        contract_id: String(r.contract_id ?? ""), frequency: r.frequency, day: String(r.day), valid_from: r.valid_from,
        valid_to: r.valid_to ?? "", amount: String(r.amount).replace(".", ","), bank_account_id: String(r.bank_account_id ?? ""),
        article_id: String(r.article_id ?? ""), cfo_id: String(r.cfo_id ?? ""), executor_id: String(r.executor_id ?? ""),
        description: r.description }
        : { legal_entity_id: entityId, frequency: "month", day: "10", valid_from: isoDay(new Date()).slice(0, 8) + "01" },
      submitText: "Сохранить",
      onSubmit: async (v) => {
        const amount = parseNumber(v.amount);
        if (amount === null) throw new Error("Сумма — число");
        const body = { name: v.name, frequency: v.frequency, day: Number(v.day), valid_from: v.valid_from,
          valid_to: v.valid_to || null, amount, bank_account_id: idOrNull(v.bank_account_id), article_id: idOrNull(v.article_id),
          cfo_id: idOrNull(v.cfo_id), executor_id: idOrNull(v.executor_id), description: v.description,
          legal_entity_id: Number(v.legal_entity_id), counterparty_id: idOrNull(v.counterparty_id),
          contract_id: idOrNull(v.contract_id) };
        if (r) await api.put(`/api/contract-payments/${r.id}`, body);
        else await api.post(`/api/organizations/${org.id}/periodic-payments`, body);
        await loadPeriodic();
      },
    });
    const form = dialog.querySelector("form");
    if (byContract) for (const name of ["legal_entity_id", "counterparty_id", "contract_id"]) form.elements[name].disabled = true;
    const refresh = () => {
      refill(form.elements.contract_id, contracts(form.elements.counterparty_id.value, form.elements.legal_entity_id.value),
        form.elements.contract_id.value);
      refill(form.elements.bank_account_id, banks(form.elements.legal_entity_id.value), form.elements.bank_account_id.value);
    };
    form.elements.counterparty_id.addEventListener("change", refresh);
    form.elements.legal_entity_id.addEventListener("change", refresh);
  }

  // --- календарь по форме владельца («лист 1»): дни колонками, статьи → контрагент + договор (шаг 14.6) ---
  const DAY_NAMES = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const shiftDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return isoDay(d); };
  const sheetFilter = { date_from: shiftDays(-2), date_to: shiftDays(28), legal_entity_id: "", include_pending: false,
    with_statements: "true" };
  const sheetTable = el("div", { class: "sheet-scroll" });
  const sheetInput = (key, label) => {
    const node = el("input", { type: "date", value: sheetFilter[key], "aria-label": label });
    node.addEventListener("change", () => { sheetFilter[key] = node.value; loadSheet(); });
    return node;
  };
  const sheetFrom = sheetInput("date_from", "Календарь с");
  const sheetTo = sheetInput("date_to", "Календарь по");
  const sheetEntity = el("select", { "aria-label": "Юрлицо календаря" });
  sheetEntity.addEventListener("change", () => { sheetFilter.legal_entity_id = sheetEntity.value; loadSheet(); });
  const sheetPending = el("input", { type: "checkbox", "aria-label": "С заявками на согласовании" });
  sheetPending.addEventListener("change", () => { sheetFilter.include_pending = sheetPending.checked; loadSheet(); });
  // «без выписок» — только введённое вручную (операции не из выписки, заявки, ручные остатки и переводы)
  const sheetMode = el("select", { "aria-label": "Данные календаря" },
    el("option", { value: "true" }, "С выписками"), el("option", { value: "false" }, "Без выписок (только вручную)"));
  sheetMode.addEventListener("change", () => { sheetFilter.with_statements = sheetMode.value; loadSheet(); });
  const sheetQuery = () => new URLSearchParams(Object.entries(sheetFilter).filter(([, v]) => v !== "" && v !== false)).toString();
  sheetBox.append(
    instruction("Колонки — дни. Блоки «Деньги на начало», «Поступления», «Технические операции» "
      + "(переводы между счетами, инкассация, снятие в кассу), «Оплаты», «Деньги на конец» раскрываются на юрлица, безнал "
      + "и наличные, счета и кассы; ниже — «Расшифровка платежей» до статьи, контрагента и договора. В ячейке зелёным — "
      + "факт из выписки (журнал), серым — план календаря (заявки, ведомости, регламентные, инвестпроекты; просроченные — "
      + "на сегодня). Щелчок по любой цифре — из чего она сложилась: операции, заявки, переводы, остатки; поправить "
      + "подтянутую цифру (сумму и дату заявки) могут финансовый директор и администратор. «+» у счёта в «Поступлениях» — "
      + "ожидаемое поступление, «+» у «Технических операций» — плановый перевод между счетами и кассами; остаток "
      + "по банк-клиенту вводится в окне ячейки «Деньги на начало». «Без выписок» — только то, что введено вручную."),
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "С ", sheetFrom),
      el("label", { class: "inline" }, "по ", sheetTo),
      el("button", { type: "button", class: "secondary", onclick: () => {
        // «Сегодня»: период с сегодняшнего дня на месяц вперёд
        sheetFilter.date_from = sheetFrom.value = shiftDays(0);
        sheetFilter.date_to = sheetTo.value = shiftDays(30);
        loadSheet();
      } }, "Сегодня"),
      sheetEntity, sheetMode, el("label", { class: "check" }, sheetPending, " с заявками на согласовании"),
      el("button", { type: "button", class: "secondary", onclick: () => expandAll(true) }, "Развернуть всё"),
      el("button", { type: "button", class: "secondary", onclick: () => expandAll(false) }, "Свернуть"),
      el("button", { type: "button", class: "secondary",
        onclick: () => api.download(`/api/organizations/${org.id}/payment-calendar/sheet.xlsx?${sheetQuery()}`,
          "payment_calendar.xlsx").catch((err) => toast(err.message, "error")) }, "Скачать в Excel")),
    sheetTable,
  );

  // раскрытые строки (ключи) — помним между обновлениями и заходами
  const SHEET_OPEN_KEY = "uchet3_calendar_open";
  let sheetOpen = new Set();
  try { sheetOpen = new Set(JSON.parse(localStorage.getItem(SHEET_OPEN_KEY) || "[]")); } catch { /* не страшно */ }
  const saveOpen = () => { try { localStorage.setItem(SHEET_OPEN_KEY, JSON.stringify([...sheetOpen])); } catch { /* не страшно */ } };
  let sheetData = null;

  function pairNode(p, signed, negative) {
    if (!p) return "";
    const sign = (v) => (signed && Number(v) > 0 ? "+" : "");
    const parts = [];
    if (p.fact !== null && p.fact !== undefined) {
      parts.push(el("div", { class: `fact${negative && Number(p.fact) < 0 ? " neg" : ""}` }, sign(p.fact) + money(p.fact)));
    }
    if (p.plan !== null && p.plan !== undefined) {
      parts.push(el("div", { class: `plan${negative && Number(p.plan) < 0 ? " neg" : ""}` }, sign(p.plan) + money(p.plan)));
    }
    return parts;
  }

  async function loadSheet() {
    sheetEntity.replaceChildren(...[["", "Все юрлица"], ...ref.entities.map((e) => [String(e.id), e.short_name])]
      .map(([v, t]) => el("option", { value: v, selected: v === sheetFilter.legal_entity_id }, t)));
    try {
      sheetData = await api.get(`/api/organizations/${org.id}/payment-calendar/sheet?${sheetQuery()}`);
    } catch (err) {
      sheetTable.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    renderSheet();
  }

  function renderSheet() {
    const data = sheetData;
    const byKey = new Map(data.rows.map((r) => [r.key, r]));
    const parents = new Set(data.rows.map((r) => r.parent).filter(Boolean));
    const visible = (row) => {
      for (let p = row.parent; p; p = byKey.get(p)?.parent) if (!sheetOpen.has(p)) return false;
      return true;
    };
    const dayClass = (d) => {
      const wd = new Date(`${d}T00:00:00`).getDay();
      return [wd === 0 || wd === 6 ? "weekend" : "", d === data.today ? "today" : ""].filter(Boolean).join(" ");
    };
    const head = el("tr", {}, el("th", { class: "sticky" }, ""),
      ...data.days.map((d) => el("th", { class: dayClass(d) }, d.slice(8, 10) + "." + d.slice(5, 7),
        el("div", { class: "small" }, DAY_NAMES[new Date(`${d}T00:00:00`).getDay()]))));
    const body = [];
    for (const row of data.rows) {
      if (!visible(row)) continue;
      const hasChildren = parents.has(row.key);
      const open = sheetOpen.has(row.key);
      const toggle = hasChildren ? el("button", { type: "button", class: "tree-toggle", "aria-expanded": String(open),
        "aria-label": `${open ? "Свернуть" : "Раскрыть"}: ${row.title}`,
        onclick: () => { if (open) sheetOpen.delete(row.key); else sheetOpen.add(row.key); saveOpen(); renderSheet(); } },
      open ? "▾" : "▸") : el("span", { class: "tree-toggle" }, "");
      const addButton = row.add === "inflow" && can("draft")
        ? el("button", { type: "button", class: "link add-ref", "aria-label": `Добавить поступление: ${row.title}`,
          onclick: () => inflowForm(row) }, "+")
        : row.add === "transfer" && can("post")
          ? el("button", { type: "button", class: "link add-ref", "aria-label": "Добавить плановый перевод",
            onclick: () => transferForm(null) }, "+ перевод")
          : "";
      const tr = el("tr", { class: `sheet-${row.kind}`, "data-key": row.key, "data-level": row.level },
        el("td", { class: "sticky", style: `padding-left: ${6 + row.level * 16}px`, title: row.subtitle ? `${row.title}, ${row.subtitle}` : row.title },
          toggle, " ", row.title, row.subtitle ? el("span", { class: "muted" }, ` · ${row.subtitle}`) : "", " ", addButton));
      if (row.kind === "section") {
        tr.append(el("td", { colspan: data.days.length }));
        body.push(tr);
        continue;
      }
      const negative = row.key.startsWith("close") || row.key.startsWith("open");
      for (const d of data.days) {
        const cell = row.cells[d];
        const movable = row.kind === "line" && can("supervise") ? (cell?.items ?? []).filter((i) => i.movable) : [];
        const balance = row.balance && row.key.startsWith("open:");
        const clickable = cell || balance;  // провалиться можно в любую цифру
        const td = el("td", { class: `num ${dayClass(d)}${clickable ? " clickable" : ""}`, "data-day": d,
          draggable: movable.length ? "true" : undefined,
          onclick: clickable ? () => drill(row, d) : undefined },
        pairNode(cell, row.signed, negative));
        if (movable.length) {
          td.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", JSON.stringify(movable.map((i) => i.source_id))));
        }
        if (row.kind === "line") {
          td.addEventListener("dragover", (e) => { if (can("supervise")) e.preventDefault(); });
          td.addEventListener("drop", (e) => {
            e.preventDefault();
            let ids = [];
            try { ids = JSON.parse(e.dataTransfer.getData("text/plain")); } catch { return; }
            moveRequests(ids, d);
          });
        }
        tr.append(td);
      }
      body.push(tr);
    }
    sheetTable.replaceChildren(el("table", { class: "pay-sheet" }, el("thead", {}, head), el("tbody", {}, ...body)));
  }

  function expandAll(open) {
    if (!sheetData) return;
    if (open) {
      for (const r of sheetData.rows) if (r.parent) sheetOpen.add(r.parent);
    } else {
      sheetOpen.clear();
    }
    saveOpen();
    renderSheet();
  }

  async function moveRequests(ids, day) {
    try {
      for (const id of ids) {
        const r = await api.get(`/api/payment-requests/${id}`);
        if (r.pay_date === day) continue;
        await api.post(`/api/payment-requests/${id}/move`, { pay_date: day, bank_account_id: r.bank_account_id, cash_cfo_id: r.cash_cfo_id });
      }
      toast(`Перенесено на ${date(day)}`);
    } catch (err) {
      toast(err.message, "error");
    }
    await loadSheet();
  }

  // --- провал в цифру: из чего сложилась; поправить подтянутое — финансовый директор и администратор ---
  const sheetRows = () => sheetData?.rows ?? [];
  const descendants = (row) => {
    const result = [];
    const walk = (key) => {
      for (const r of sheetRows()) if (r.parent === key) { result.push(r); walk(r.key); }
    };
    walk(row.key);
    return result;
  };
  const viewOnly = () => (can("supervise") ? org.permissions : org.permissions.filter((p) => !["draft", "post"].includes(p)));
  const rowPath = (row) => {
    const names = [];
    for (let r = row; r; r = sheetRows().find((x) => x.key === r.parent)) names.unshift(r.title);
    return names.join(" → ");
  };
  const pairText = (c) => [c?.fact !== null && c?.fact !== undefined ? `факт ${money(c.fact)}` : "",
    c?.plan !== null && c?.plan !== undefined ? `план ${money(c.plan)}` : ""].filter(Boolean).join(", ") || "—";

  function drill(row, day) {
    const close = () => dialog.close();
    const body = [];
    const balanceBlock = row.key.startsWith("open") || row.key.startsWith("close");
    if (balanceBlock) {
      // остатки: по каждому счёту / кассе внутри строки — по журналу (факт) и вручную / прогноз (план)
      const sources = row.kind === "source" ? [row] : descendants(row).filter((r) => r.kind === "source");
      body.push(el("table", { class: "recon" }, el("tbody", {}, ...sources.map((src) => {
        const parts = [el("td", {}, src.title), el("td", {}, pairText(src.cells[day]))];
        if (row.key.startsWith("close")) {
          const piece = (prefix) => sheetRows().find((r) => r.kind === "source" && r.source === src.source && r.key.startsWith(prefix));
          const val = (r) => (r?.cells[day] ? Number(r.cells[day].fact ?? 0) + Number(r.cells[day].plan ?? 0) : 0);
          const techs = sheetRows().filter((r) => r.kind === "source" && r.source === src.source && r.key.startsWith("tech:"));
          const tech = techs.reduce((sum, r) => sum + val(r), 0);
          parts.push(el("td", { class: "muted small" }, `начало ${money(val(piece("open:")).toFixed(2))} + поступления `
            + `${money(val(piece("in:")).toFixed(2))} − оплаты ${money(val(piece("out:")).toFixed(2))}`
            + (tech ? ` ${tech > 0 ? "+" : "−"} технические ${money(Math.abs(tech).toFixed(2))}` : "")));
        }
        if (src.balance && row.key.startsWith("open") && can("post")) {
          parts.push(el("td", {}, el("button", { type: "button", class: "link", onclick: () => { close(); balanceForm(src, day); } },
            "Остаток по банк-клиенту")));
        }
        return el("tr", {}, ...parts);
      }))));
      body.push(el("p", { class: "muted small" }, "Факт — по журналу (выписки и операции), план — остаток, введённый "
        + "вручную, или прогноз: остаток на конец предыдущего дня."));
    } else {
      // поступления, оплаты, технические: все операции и плановые платежи под этой цифрой
      const leaves = [row, ...descendants(row)].filter((r) => r.cells[day] && ((r.cells[day].ops?.length ?? 0) || (r.cells[day].items?.length ?? 0)));
      if (!leaves.length) body.push(el("p", { class: "muted" }, "Нет операций и плановых платежей."));
      for (const leaf of leaves) {
        const cell = leaf.cells[day];
        body.push(el("h3", {}, rowPath(leaf) + (leaf.subtitle ? ` · ${leaf.subtitle}` : "")));
        if (cell.ops?.length) {
          body.push(el("ul", {}, ...cell.ops.map((o) => el("li", {},
            el("button", { type: "button", class: "link", onclick: async () => {
              close();
              openOperation({ ref, org, op: await api.get(`/api/operations/${o.operation_id}`), perms: viewOnly(), onChanged: loadSheet });
            } }, `Операция № ${o.number}`), ` — ${money(o.amount)} (факт)`))));
        }
        if (cell.items?.length) {
          body.push(el("ul", {}, ...cell.items.map((i) => el("li", {},
            `${i.title} — ${money(i.amount)} (план)`, i.overdue ? el("span", { class: "over-budget" }, ` просрочен, ${date(i.date)}`) : "",
            i.source === "request" ? el("button", { type: "button", class: "link", onclick: () => { close(); requestView(i.source_id); } }, " Заявка") : "",
            i.amount_editable && can("supervise") ? el("button", { type: "button", class: "link", onclick: () => { close(); amountForm(i); } }, " Сумма") : "",
            i.movable && can("supervise") ? el("button", { type: "button", class: "link", onclick: async () => {
              close();
              move(await api.get(`/api/payment-requests/${i.source_id}`), loadSheet);
            } }, " Перенести") : "",
            i.source === "transfer" && can("post") ? el("button", { type: "button", class: "link", onclick: () => { close(); transferForm(i.source_id); } }, " Изменить") : "",
            i.source === "transfer" && can("post") ? el("button", { type: "button", class: "link", onclick: async () => {
              if (!confirm("Удалить плановый перевод?")) return;
              close();
              try { await api.delete(`/api/calendar-transfers/${i.source_id}`); } catch (err) { toast(err.message, "error"); }
              await loadSheet();
            } }, " Удалить") : ""))));
        }
      }
    }
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, `${date(day)}: ${rowPath(row)}`),
      el("p", {}, pairText(row.cells[day])), ...body,
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: close }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  function balanceForm(row, day) {
    const current = row.cells[day];
    openForm({
      title: `Остаток на начало ${date(day)}: ${row.title}`,
      fields: [{ name: "amount", label: "Остаток по банк-клиенту, ₽",
        hint: `По журналу: ${current?.fact !== null && current?.fact !== undefined ? money(current.fact) : "—"}. Пусто — убрать ручной остаток` }],
      submitText: "Сохранить",
      onSubmit: async (v) => {
        const amount = v.amount === "" ? null : parseNumber(v.amount);
        if (v.amount !== "" && amount === null) throw new Error("Остаток — число, например 1 250 000,00");
        await api.put(`/api/organizations/${org.id}/calendar-balances`, { ...row.balance, day, amount });
        await loadSheet();
      },
    });
  }

  // ожидаемое поступление на счёт или в кассу (заявка-поступление, согласования не требует)
  function inflowForm(row) {
    const articles = ref.leafArticles.filter((a) => a.is_cash_flow && a.cash_flow_direction === "inflow" && !a.is_transfer);
    const dialog = openForm({
      title: `Ожидаемое поступление: ${row.title}`,
      fields: [
        { name: "pay_date", label: "Дата", type: "date", required: true },
        { name: "amount", label: "Сумма", required: true },
        { name: "counterparty_id", label: "Контрагент", type: "select", options: options(ref.counterparties, (c) => c.name) },
        { name: "contract_id", label: "Договор", type: "select", options: [["", "— без договора —"]] },
        { name: "article_id", label: "Статья ДДС", type: "select", options: options(articles, (a) => ref.articleLabel.get(a.id) ?? a.name) },
        { name: "description", label: "Назначение", type: "textarea" },
      ],
      values: { pay_date: sheetFilter.date_from > isoDay(new Date()) ? sheetFilter.date_from : isoDay(new Date()) },
      submitText: "Добавить",
      onSubmit: async (v) => {
        const amount = parseNumber(v.amount);
        if (amount === null) throw new Error("Сумма — число");
        const req = await api.post(`/api/organizations/${org.id}/payment-requests`, {
          direction: "in", pay_date: v.pay_date, legal_entity_id: row.entity_id, amount, description: v.description,
          bank_account_id: row.source_ref?.bank_account_id ?? null, cash_cfo_id: row.source_ref?.cash_cfo_id ?? null,
          counterparty_id: idOrNull(v.counterparty_id), contract_id: idOrNull(v.contract_id), article_id: idOrNull(v.article_id) });
        await api.post(`/api/payment-requests/${req.id}/submit`);
        toast(`Поступление добавлено (заявка № ${req.number})`);
        await loadSheet();
      },
    });
    const cp = dialog.querySelector("[name=counterparty_id]");
    const contract = dialog.querySelector("[name=contract_id]");
    cp.addEventListener("change", () => refill(contract, [["", "— без договора —"], ...ref.contracts
      .filter((c) => String(c.counterparty_id) === cp.value && c.legal_entity_id === row.entity_id)
      .map((c) => [String(c.id), c.name || `№ ${c.number}`])], ""));
  }

  // плановый перевод: откуда → куда (счета и кассы из «Денег на начало»)
  async function transferForm(id) {
    const current = id ? await api.get(`/api/calendar-transfers/${id}`).catch((err) => { toast(err.message, "error"); return null; }) : null;
    if (id && !current) return;
    const sources = sheetRows().filter((r) => r.kind === "source" && r.key.startsWith("open:") && r.source_ref);
    const many = new Set(sources.map((r) => r.entity_id)).size > 1;
    const label = (r) => (many ? `${ref.entities.find((e) => e.id === r.entity_id)?.short_name ?? ""} · ` : "") + r.title;
    const value = (r) => (r.source_ref.bank_account_id ? `bank:${r.source_ref.bank_account_id}` : `cash:${r.source_ref.cash_cfo_id}`);
    const opts = [["", "— выберите —"], ...[...new Map(sources.map((r) => [value(r), label(r)])).entries()]];
    const sideValue = (t, side) => (t?.[`${side}_bank_account_id`] ? `bank:${t[`${side}_bank_account_id`]}`
      : t?.[`${side}_cash_cfo_id`] ? `cash:${t[`${side}_cash_cfo_id`]}` : "");
    const sideBody = (v, side) => ({ [`${side}_bank_account_id`]: v.startsWith("bank:") ? Number(v.slice(5)) : null,
      [`${side}_cash_cfo_id`]: v.startsWith("cash:") ? Number(v.slice(5)) : null });
    openForm({
      title: id ? "Плановый перевод" : "Новый плановый перевод",
      fields: [
        { name: "from", label: "Откуда (счёт или касса)", type: "select", required: true, options: opts },
        { name: "to", label: "Куда (счёт или касса)", type: "select", required: true, options: opts },
        { name: "day", label: "Дата", type: "date", required: true },
        { name: "amount", label: "Сумма", required: true },
        { name: "description", label: "Комментарий" },
      ],
      values: current ? { from: sideValue(current, "from"), to: sideValue(current, "to"), day: current.day,
        amount: String(current.amount).replace(".", ","), description: current.description }
        : { day: isoDay(new Date()) },
      submitText: "Сохранить",
      onSubmit: async (v) => {
        const amount = parseNumber(v.amount);
        if (amount === null) throw new Error("Сумма — число");
        const body = { ...sideBody(v.from, "from"), ...sideBody(v.to, "to"), day: v.day, amount, description: v.description };
        if (id) await api.put(`/api/calendar-transfers/${id}`, body);
        else await api.post(`/api/organizations/${org.id}/calendar-transfers`, body);
        await loadSheet();
      },
    });
  }

  // карточка заявки из календаря; периодический платёж — с условиями договора
  async function requestView(id) {
    let r;
    try {
      r = await api.get(`/api/payment-requests/${id}`);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    let periodic = null;
    if (r.periodic_id && r.contract_id) {
      try {
        const terms = await api.get(`/api/contracts/${r.contract_id}/payments`);
        periodic = terms.find((t) => t.id === r.periodic_id) ?? null;
      } catch { /* без условий */ }
    }
    const field = (title, value) => (value ? el("tr", {}, el("th", {}, title), el("td", {}, value)) : null);
    const month = r.period ? new Date(`${r.period}T00:00:00`).toLocaleDateString("ru-RU", { month: "long", year: "numeric" }) : "";
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, `Заявка № ${r.number}`),
      el("table", { class: "recon request-card" }, el("tbody", {},
        field("Статус", STATUS[r.status] + (r.status === "pending" && r.step_title ? ` (у: ${r.step_title})` : "")),
        field("Вид", DIRECTION[r.direction]),
        field("Дата оплаты", date(r.pay_date)),
        field("Сумма", money(r.amount)),
        field("Юрлицо", r.legal_entity_name),
        field("Счёт / касса", r.source_name),
        field("Контрагент", r.counterparty_name),
        field("Договор", r.contract_name),
        field("Статья ДДС", r.article_name),
        field("Бюджет", r.budget ? `остаток по статье ${money(r.budget.remaining ?? 0)}`
          + (Number(r.budget.over) > 0 ? `, сверх бюджета ${money(r.budget.over)}` : "") : ""),
        field("ЦФО / ЦФУ", [r.cfo_name, r.executor_name].filter(Boolean).join(" · ")),
        field("Назначение", r.description),
        field("Маршрут", r.route_titles.join(" → ")),
        field("Создал(а)", r.created_by_name),
        field("Согласовал(а)", r.approved_by_name),
        field("Оплачена", r.paid_on ? `${date(r.paid_on)}${r.operation_number ? `, операция № ${r.operation_number}` : ""}` : ""),
        field("Периодический платёж", r.source === "periodic"
          ? `за ${month}` + (periodic ? `: с ${date(periodic.valid_from)}${periodic.valid_to ? ` по ${date(periodic.valid_to)}` : ""}, `
            + `${periodic.day}-го числа, ${money(periodic.amount)}` : "") : ""),
        field("Файлы", r.files.length ? el("span", {}, ...r.files.map((f) => el("button", { type: "button", class: "link",
          onclick: () => api.download(`/api/payment-request-files/${f.id}`, f.filename).catch((err) => toast(err.message, "error")) },
        f.filename, " "))) : ""))),
      el("div", { class: "form-actions" },
        (["draft", "rejected", "revision"].includes(r.status) && author()) || (["pending", "approved"].includes(r.status)
          && can("supervise") && !r.linked_id)
          ? el("button", { type: "button", class: "secondary", onclick: () => { dialog.close(); edit(r, r.direction); } }, "Изменить")
          : "",
        el("button", { type: "button", class: "secondary", onclick: () => history(r) }, "История"),
        r.operation_id ? el("button", { type: "button", class: "secondary", onclick: async () => {
          dialog.close();
          openOperation({ ref, org, op: await api.get(`/api/operations/${r.operation_id}`), perms: viewOnly(), onChanged: loadSheet });
        } }, "Операция оплаты") : "",
        el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  function amountForm(i) {
    openForm({
      title: `Сумма: ${i.title}`,
      fields: [{ name: "amount", label: "Новая сумма", required: true }, { name: "comment", label: "Почему (в историю)" }],
      values: { amount: String(i.amount).replace(".", ",") },
      submitText: "Сохранить",
      onSubmit: async (v) => {
        const amount = parseNumber(v.amount);
        if (amount === null) throw new Error("Сумма — число");
        await api.post(`/api/payment-requests/${i.source_id}/amount`, { amount, comment: v.comment });
        await loadSheet();
      },
    });
  }

  // --- накладные поставщиков: долг по накладным, срок оплаты, лимит; оплата планируется заявкой ---
  const supFilter = { legal_entity_id: "", open_only: true, search: "" };
  const supList = el("div");
  const supTotals = el("p", { class: "muted" });
  const supEntity = el("select", { "aria-label": "Юрлицо-покупатель" });
  supEntity.addEventListener("change", () => { supFilter.legal_entity_id = supEntity.value; loadSuppliers(); });
  const supOpen = el("input", { type: "checkbox", checked: true, "aria-label": "Только неоплаченные" });
  supOpen.addEventListener("change", () => { supFilter.open_only = supOpen.checked; loadSuppliers(); });
  const supSearch = el("input", { type: "search", placeholder: "Контрагент", "aria-label": "Поиск контрагента" });
  supSearch.addEventListener("input", () => { supFilter.search = supSearch.value.trim().toLowerCase(); renderSuppliers(); });
  const supFile = el("input", { type: "file", hidden: true, accept: ".xlsx", "aria-label": "Файл накладных" });
  supFile.addEventListener("change", async () => {
    const file = supFile.files[0];
    supFile.value = "";
    if (!file) return;
    try {
      const r = await api.upload(`/api/organizations/${org.id}/supplier-invoices/import?legal_entity_id=${supFilter.legal_entity_id}`, file);
      showSupplierImport(file.name, r);
      await loadSuppliers();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  suppliersBox.append(
    instruction("Накладные загружаются из Excel в формате листа «ПОСТАВЩИКИ» (тип документа, дата, №, сумма, "
      + "контрагент) для выбранного юрлица. Срок оплаты — дата накладной + отсрочка из договора, лимит задолженности — "
      + "тоже в договоре. «Запланировать оплату» создаёт заявку (финансовый менеджер → финансовый директор): сумма "
      + "закрывает отмеченные накладные (или все) от старых к новым, можно оплатить частично. Поставщик — юрлицо группы: "
      + "оплата без согласования, у получателя появится поступление."),
    el("div", { class: "filters" }, supEntity, supSearch,
      el("label", { class: "check" }, supOpen, " только неоплаченные"),
      el("button", { type: "button", onclick: () => {
        if (!supFilter.legal_entity_id) {
          toast("Выберите юрлицо-покупателя", "error");
          return;
        }
        supFile.click();
      } }, "Загрузить из Excel"),
      el("button", { type: "button", class: "secondary",
        onclick: () => api.download(`/api/organizations/${org.id}/supplier-invoices/template.xlsx`, "supplier_invoices.xlsx")
          .catch((err) => toast(err.message, "error")) }, "Скачать шаблон"),
      el("button", { type: "button", class: "secondary", onclick: () => invoiceForm(null, null) }, "+ Накладная"),
      el("button", { type: "button", class: "secondary", onclick: () => {
        for (const g of supData?.groups ?? []) supExpanded.add(`${g.legal_entity_id}:${g.counterparty_id}`);
        renderSuppliers();
      } }, "Развернуть всё"),
      el("button", { type: "button", class: "secondary", onclick: () => { supExpanded.clear(); renderSuppliers(); } }, "Свернуть"),
      supFile),
    supTotals, supList,
  );
  let supData = null;
  const supExpanded = new Set();  // раскрытые контрагенты (юрлицо:контрагент)

  function showSupplierImport(fileName, r) {
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, `Загрузка накладных: ${fileName}`),
      el("p", {}, `Строк: ${r.rows}. Загружено: ${r.created}. Уже были: ${r.duplicates}.`
        + (r.zero ? ` С нулевой суммой (пропущены): ${r.zero}.` : "")),
      r.missing.length ? el("div", {},
        el("p", { class: "error" }, "Не найдены в справочнике контрагентов (добавьте и загрузите файл снова — загруженное не задвоится):"),
        el("ul", {}, ...r.missing.map(([name, count]) => el("li", {}, `${name} — строк: ${count}`)))) : "",
      r.errors.length ? el("div", {}, el("p", { class: "error" }, "Строки с ошибками:"),
        el("ul", {}, ...r.errors.slice(0, 100).map(([row, text]) => el("li", {}, `строка ${row}: ${text}`)))) : "",
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async function loadSuppliers() {
    supEntity.replaceChildren(...[["", "Все юрлица"], ...ref.entities.map((e) => [String(e.id), e.short_name])]
      .map(([v, t]) => el("option", { value: v, selected: v === supFilter.legal_entity_id }, t)));
    if (!supFilter.legal_entity_id && ref.entities.length === 1) {
      supFilter.legal_entity_id = String(ref.entities[0].id);
      supEntity.value = supFilter.legal_entity_id;
    }
    const p = new URLSearchParams({ open_only: supFilter.open_only });
    if (supFilter.legal_entity_id) p.set("legal_entity_id", supFilter.legal_entity_id);
    try {
      supData = await api.get(`/api/organizations/${org.id}/supplier-invoices?${p}`);
    } catch (err) {
      supList.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    renderSuppliers();
  }

  function renderSuppliers() {
    const groups = supData.groups.filter((g) => !supFilter.search || g.counterparty_name.toLowerCase().includes(supFilter.search));
    supTotals.textContent = `Долг: ${money(supData.debt)} · просрочено: ${money(supData.overdue)} · не запланировано: ${money(supData.remaining)}`;
    if (!groups.length) {
      supList.replaceChildren(el("p", { class: "muted" }, "Накладных нет."));
      return;
    }
    const body = [];
    for (const g of groups) {
      const groupKey = `${g.legal_entity_id}:${g.counterparty_id}`;
      const open = supExpanded.has(groupKey);
      const toggle = el("button", { type: "button", class: "tree-toggle", "aria-expanded": String(open),
        "aria-label": `${open ? "Свернуть" : "Раскрыть"} накладные: ${g.counterparty_name}`,
        onclick: () => { if (open) supExpanded.delete(groupKey); else supExpanded.add(groupKey); renderSuppliers(); } },
      open ? "▾" : "▸");
      const boxes = g.invoices.map((inv) => el("input", { type: "checkbox", "data-id": inv.id,
        "aria-label": `Накладная № ${inv.number}`, disabled: Number(inv.remaining) <= 0 }));
      const terms = [g.deferral_days !== null ? `отсрочка ${g.deferral_days} дн.` : "",
        g.debt_limit ? `лимит ${money(g.debt_limit)}` : ""].filter(Boolean).join(" · ");
      body.push(el("tr", { class: `week-row${g.over_limit ? " gap-row" : ""}`, "data-counterparty": g.counterparty_name },
        el("td", { colspan: 3 }, toggle, " ", g.counterparty_name, ` · ${g.legal_entity_name}`,
          el("span", { class: "muted small" }, ` · накладных: ${g.invoices.length}`),
          terms ? el("div", { class: "small" }, terms + (g.over_limit ? " — долг больше лимита" : "")) : null,
          g.group_name ? el("div", { class: "small" }, `юрлицо группы (${g.group_name}) — без согласования`) : null),
        el("td", { class: "num" }, money(g.debt)),
        el("td", { class: "num" }, Number(g.overdue) > 0 ? el("span", { class: "gap-note" }, `просрочено ${money(g.overdue)}`) : ""),
        el("td", {}),
        el("td", { class: "num" }, money(g.remaining)),
        el("td", {}, Number(g.remaining) > 0
          ? el("button", { type: "button", class: "link", onclick: () => planSupplier(g, boxes) }, "Запланировать оплату") : "")));
      if (!open) continue;  // свёрнуто по контрагенту
      g.invoices.forEach((inv, i) => body.push(el("tr", { "data-invoice": inv.number },
        el("td", {}, boxes[i]),
        el("td", {}, `${inv.kind === "advance" ? "Аванс" : "Накладная"} № ${inv.number}`, el("div", { class: "muted" }, `от ${date(inv.doc_date)}`)),
        el("td", {}, `срок ${date(inv.due_date)}`, inv.overdue_days ? el("div", { class: "gap-note" }, `просрочено ${inv.overdue_days} дн.`) : null),
        el("td", { class: "num" }, money(inv.amount)),
        el("td", { class: "num" }, Number(inv.paid) > 0 ? `оплачено ${money(inv.paid)}` : ""),
        el("td", {}, ...inv.payments.map((p) => el("div", { class: "small" },
          `заявка № ${p.number} · ${date(p.pay_date)} · ${money(p.amount)} · ${p.status_title}`))),
        el("td", { class: "num" }, money(inv.remaining)),
        el("td", {}, el("button", { type: "button", class: "link", onclick: () => invoiceForm(inv, g) }, "Изменить"),
          inv.payments.length ? "" : el("button", { type: "button", class: "link", onclick: async () => {
          if (!confirm(`Удалить накладную № ${inv.number}?`)) return;
          try {
            await api.delete(`/api/supplier-invoices/${inv.id}`);
            await loadSuppliers();
          } catch (err) {
            toast(err.message, "error");
          }
        } }, "Удалить")))));
    }
    supList.replaceChildren(el("table", { class: "recon calendar-grid registry suppliers" },
      el("thead", {}, el("tr", {}, ...["", "Документ", "Срок оплаты", "Сумма", "Оплачено", "Заявки на оплату", "Остаток", ""]
        .map((t) => el("th", {}, t)))),
      el("tbody", {}, ...body)));
  }

  // накладная вручную: добавить или поправить (финансовый менеджер, финансовый директор, администратор)
  function invoiceForm(inv, g) {
    const entities = ref.entities.filter((e) => e.is_active || e.id === g?.legal_entity_id);
    const entityId = String(g?.legal_entity_id ?? supFilter.legal_entity_id ?? entities[0]?.id ?? "");
    const contractOptions = (cpId, leId) => [["", "— по умолчанию (договор поставщика) —"], ...ref.contracts
      .filter((c) => String(c.counterparty_id) === cpId && String(c.legal_entity_id) === leId)
      .map((c) => [String(c.id), c.name || `№ ${c.number}`])];
    const dialog = openForm({
      title: inv ? `Накладная № ${inv.number}` : "Новая накладная",
      fields: [
        { name: "legal_entity_id", label: "Юрлицо-покупатель", type: "select", required: true,
          options: entities.map((e) => [String(e.id), e.short_name]) },
        { name: "counterparty_id", label: "Поставщик", type: "select", required: true,
          options: options(ref.counterparties, (c) => (c.inn ? `${c.name} (${c.inn})` : c.name), "— выберите —") },
        { name: "contract_id", label: "Договор", type: "select",
          options: contractOptions(String(g?.counterparty_id ?? ""), entityId) },
        { name: "kind", label: "Документ", type: "select", options: [["invoice", "Накладная"], ["advance", "Аванс"]] },
        { name: "number", label: "№", required: true },
        { name: "doc_date", label: "Дата", type: "date", required: true },
        { name: "amount", label: "Сумма", required: true },
      ],
      values: inv ? { legal_entity_id: entityId, counterparty_id: String(g.counterparty_id), contract_id: String(inv.contract_id ?? ""),
        kind: inv.kind, number: inv.number, doc_date: inv.doc_date, amount: String(inv.amount).replace(".", ",") }
        : { legal_entity_id: entityId, kind: "invoice", doc_date: isoDay(new Date()) },
      submitText: "Сохранить",
      onSubmit: async (v) => {
        const amount = parseNumber(v.amount);
        if (amount === null) throw new Error("Сумма — число");
        const body = { legal_entity_id: Number(v.legal_entity_id), counterparty_id: Number(v.counterparty_id),
          contract_id: idOrNull(v.contract_id), kind: v.kind, number: v.number, doc_date: v.doc_date, amount };
        if (inv) await api.put(`/api/supplier-invoices/${inv.id}`, body);
        else await api.post(`/api/organizations/${org.id}/supplier-invoices`, body);
        supExpanded.add(`${body.legal_entity_id}:${body.counterparty_id}`);
        await loadSuppliers();
      },
    });
    const form = dialog.querySelector("form");
    const refresh = () => refill(form.elements.contract_id, contractOptions(form.elements.counterparty_id.value,
      form.elements.legal_entity_id.value), form.elements.contract_id.value);
    form.elements.counterparty_id.addEventListener("change", refresh);
    form.elements.legal_entity_id.addEventListener("change", refresh);
  }

  function planSupplier(g, boxes) {
    const ids = boxes.filter((b) => b.checked).map((b) => Number(b.dataset.id));
    const chosen = g.invoices.filter((inv) => !ids.length || ids.includes(inv.id));
    const total = chosen.reduce((sum, inv) => sum + Number(inv.remaining), 0);
    const banks = ref.bankAccounts.filter((b) => b.legal_entity_id === g.legal_entity_id)
      .map((b) => [String(b.id), `${b.name} ··${b.number.slice(-4)}`]);
    openForm({
      title: `Оплата: ${g.counterparty_name}`,
      fields: [
        { name: "pay_date", label: "Дата оплаты", type: "date", required: true },
        { name: "amount", label: "Сумма", hint: `Пусто — весь остаток ${ids.length ? "отмеченных" : "всех"} накладных `
          + `(${money(total.toFixed(2))}); меньше — закроет накладные от старых к новым` },
        { name: "bank_account_id", label: "Откуда платим", type: "select", options: [["", "— выберет финансовая служба —"], ...banks] },
      ],
      submitText: "Создать заявку",
      onSubmit: async (v) => {
        if (v.amount && parseNumber(v.amount) === null) throw new Error("Сумма — число, например 15 000,00");
        const req = await api.post(`/api/organizations/${org.id}/supplier-payments`, {
          legal_entity_id: g.legal_entity_id, counterparty_id: g.counterparty_id, pay_date: v.pay_date,
          amount: v.amount ? parseNumber(v.amount) : null, invoice_ids: ids, bank_account_id: idOrNull(v.bank_account_id) });
        toast(`Заявка № ${req.number}: ${STATUS[req.status].toLowerCase()}`);
        await loadSuppliers();
      },
    });
  }

  // --- реестр на оплату: согласованные выплаты по дням и счетам, выгрузка платёжек в банк-клиент (1С) ---
  const regFilter = { date_from: "", date_to: "", include_sent: true };
  const regList = el("div");
  const regInput = (key, label) => {
    const node = el("input", { type: "date", value: regFilter[key], "aria-label": label });
    node.addEventListener("change", () => { regFilter[key] = node.value; loadRegistry(); });
    return node;
  };
  const sentBox = el("input", { type: "checkbox", checked: true, "aria-label": "С отправленными в банк" });
  sentBox.addEventListener("change", () => { regFilter.include_sent = sentBox.checked; loadRegistry(); });
  const regQuery = () => new URLSearchParams(Object.entries(regFilter).filter(([, v]) => v !== "")).toString();
  const picked = new Set();
  const pickedIds = () => {
    if (!picked.size) toast("Отметьте заявки в реестре", "error");
    return [...picked];
  };
  const regRun = async (fn) => {
    try {
      await fn();
      await loadRegistry();
    } catch (err) {
      toast(err.message, "error");
    }
  };
  registryBox.append(
    instruction("Согласованные и ещё не оплаченные выплаты по дням и расчётным счетам (касса — отдельно). "
      + "«Остаток» — остаток на счёте сегодня минус платежи этого счёта в реестре раньше; красным — не хватает денег. "
      + "«Выгрузить в банк» — файл платёжных поручений для банк-клиента (формат 1С), заявки отмечаются «отправлено в банк». "
      + "Для платёжки у контрагента нужны расчётный счёт и банк (карточка контрагента; из выписки подставляются сами). "
      + "Оплаченной заявку отметит выписка."),
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "С ", regInput("date_from", "Реестр с даты")),
      el("label", { class: "inline" }, "по ", regInput("date_to", "Реестр по дату")),
      el("label", { class: "check" }, sentBox, " с отправленными в банк"),
      el("button", { type: "button", onclick: () => regRun(async () => {
        const ids = pickedIds();
        if (!ids.length) return;
        await api.download(`/api/organizations/${org.id}/payment-registry/export`, "1c_to_kl.txt", { ids });
        toast(`Выгружено платёжек: ${ids.length}`);
      }) }, "Выгрузить в банк (1С)"),
      el("button", { type: "button", class: "secondary", onclick: () => regRun(async () => {
        const ids = pickedIds();
        if (ids.length) await api.post(`/api/organizations/${org.id}/payment-registry/sent`, { ids, sent: true });
      }) }, "Отметить отправленными"),
      el("button", { type: "button", class: "secondary", onclick: () => regRun(async () => {
        const ids = pickedIds();
        if (ids.length) await api.post(`/api/organizations/${org.id}/payment-registry/sent`, { ids, sent: false });
      }) }, "Снять отметку"),
      el("button", { type: "button", class: "secondary",
        onclick: () => api.download(`/api/organizations/${org.id}/payment-registry.xlsx?${regQuery()}`, "payment_registry.xlsx")
          .catch((err) => toast(err.message, "error")) }, "Скачать в Excel")),
    regList,
  );

  async function loadRegistry() {
    let data;
    try {
      data = await api.get(`/api/organizations/${org.id}/payment-registry?${regQuery()}`);
    } catch (err) {
      regList.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    picked.clear();
    if (!data.groups.length) {
      regList.replaceChildren(el("p", { class: "muted" }, "Согласованных неоплаченных выплат нет."));
      return;
    }
    const check = (r) => {
      const box = el("input", { type: "checkbox", "aria-label": `Заявка № ${r.number} в выгрузку`,
        disabled: r.problems.length > 0 || r.cash_cfo_id !== null });
      box.addEventListener("change", () => { if (box.checked) picked.add(r.id); else picked.delete(r.id); });
      return box;
    };
    const body = [];
    for (const g of data.groups) {
      const boxes = g.rows.map(check);
      const all = el("input", { type: "checkbox", "aria-label": `Все заявки ${date(g.pay_date)} ${g.title}`,
        disabled: boxes.every((b) => b.disabled) });
      all.addEventListener("change", () => boxes.forEach((b) => {
        if (b.disabled) return;
        b.checked = all.checked;
        b.dispatchEvent(new Event("change"));
      }));
      body.push(el("tr", { class: `week-row${g.shortage !== "0.00" ? " gap-row" : ""}` },
        el("td", {}, all),
        el("td", { colspan: 4 }, `${date(g.pay_date)}${g.overdue ? " (просрочено)" : ""} · ${g.title}`),
        el("td", {}, g.available === null ? "" : `остаток ${money(g.available)}`
          + (g.shortage !== "0.00" ? ` — не хватает ${money(g.shortage)}` : "")),
        el("td", { class: "num" }, money(g.total)), el("td", {})));
      g.rows.forEach((r, i) => body.push(el("tr", { "data-number": r.number },
        el("td", {}, boxes[i]),
        el("td", {}, `№ ${r.number}`),
        el("td", {}, r.counterparty_name || "—", r.contract_name ? el("div", { class: "muted" }, `Договор ${r.contract_name}`) : null),
        el("td", {}, r.article_name, el("div", { class: "muted" }, [r.cfo_name, r.executor_name].filter(Boolean).join(" · "))),
        el("td", {}, r.description,
          ...r.problems.map((p) => el("div", { class: "gap-note" }, p)),
          ...r.warnings.map((w) => el("div", { class: "muted" }, w))),
        el("td", {}, r.sent_at ? `отправлено ${new Date(r.sent_at).toLocaleDateString("ru-RU")}` : ""),
        el("td", { class: "num" }, money(r.amount)),
        el("td", { class: "req-actions" },
          el("button", { type: "button", class: "link", onclick: () => move(r, loadRegistry) }, "Перенести"),
          el("button", { type: "button", class: "link", onclick: () => pay(r, loadRegistry) }, "Оплачено"),
          el("button", { type: "button", class: "link", onclick: () => history(r) }, "История")))));
    }
    regList.replaceChildren(el("table", { class: "recon calendar-grid registry" },
      el("thead", {}, el("tr", {}, ...["", "№", "Контрагент", "Статья / ЦФО", "Назначение", "В банк", "Сумма", ""]
        .map((t) => el("th", {}, t)))),
      el("tbody", {}, ...body)),
    el("p", { class: "muted" }, `Всего: ${data.count} на ${money(data.total)}`));
  }

  await openTab(tab);
}

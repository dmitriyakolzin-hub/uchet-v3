// Форма операции журнала: шапка, проводки, аналитика по выбранным счетам, шаблон статьи.
import { api } from "./api.js";
import { money, parseNumber, quantity } from "./format.js";
import { el, toast } from "./ui.js";

const KIND_TITLES = {
  bank_account: "Расчётный счёт",
  counterparty: "Контрагент",
  contract: "Договор",
  employee: "Сотрудник",
  nomenclature: "Номенклатура",
  warehouse: "Склад",
  cfo: "ЦФО",
  cash_flow_item: "Статья ДДС",
  pl_item: "Статья ОПиУ",
};
const STATUS_TITLES = { draft: "Черновик", posted: "Проведена", cancelled: "Аннулирована" };
const ACTION_TITLES = {
  created: "создал(а)", updated: "изменил(а)", posted: "провёл(а)",
  unposted: "вернул(а) в черновик", cancelled: "аннулировал(а)", restored: "восстановил(а)",
};

function select(options, value, attrs = {}) {
  const node = el("select", attrs);
  for (const [code, label] of options) {
    node.append(el("option", { value: code, selected: String(value ?? "") === code }, label));
  }
  return node;
}

/**
 * ref — справочники организации (см. loadReferences в pages/journal.js)
 * op — операция или null (новая); perms — права в организации; onChanged — после сохранения/действия
 */
export function openOperation({ ref, org, op, perms, onChanged }) {
  const canDraft = perms.includes("draft");
  const canPost = perms.includes("post");
  const editable = (op === null || op.status === "draft") && canDraft;
  const accountsById = new Map(ref.accounts.map((a) => [a.id, a]));
  const postableOptions = [["", "— счёт —"], ...ref.accounts
    .filter((a) => !a.has_children && a.is_active)
    .map((a) => [String(a.id), `${a.code} ${a.name}`])];

  // --- Шапка ---
  const entitySelect = select(
    [["", "— юрлицо —"], ...ref.entities.filter((e) => e.is_active || e.id === op?.legal_entity_id).map((e) => [String(e.id), e.short_name])],
    op?.legal_entity_id ?? (ref.entities.length === 1 ? ref.entities[0].id : ""),
    { name: "legal_entity_id" },
  );
  const dateInput = el("input", { type: "date", name: "op_date", required: true });
  dateInput.value = op?.op_date ?? ref.today;
  const articleSelect = select(
    [["", "— без статьи —"], ...ref.articles.filter((a) => a.is_active || a.id === op?.article_id).map((a) => [String(a.id), a.name])],
    op?.article_id ?? "",
    { name: "article_id" },
  );
  const description = el("textarea", { name: "description", rows: 2 });
  description.value = op?.description ?? "";

  const postingsBox = el("div", { class: "postings" });
  const errorBox = el("ul", { class: "error-list", role: "alert" });
  const historyBox = el("div", { class: "history" });

  // --- Проводка ---
  function optionsFor(kind, side, posting) {
    const entityId = Number(entitySelect.value) || null;
    const counterpartyId = Number(posting.querySelector(`[data-side="${side}"][data-kind="counterparty"]`)?.value) || null;
    const active = (items, current) => items.filter((i) => i.is_active || i.id === current);
    switch (kind) {
      case "bank_account":
        return ref.bankAccounts.filter((b) => !entityId || b.legal_entity_id === entityId).map((b) => [String(b.id), `${b.name} (${b.number.slice(-4)})`]);
      case "counterparty":
        return ref.counterparties.map((c) => [String(c.id), c.inn ? `${c.name} (${c.inn})` : c.name]);
      case "contract":
        return ref.contracts
          .filter((c) => (!counterpartyId || c.counterparty_id === counterpartyId) && (!entityId || c.legal_entity_id === entityId))
          .map((c) => [String(c.id), `№ ${c.number}${c.contract_date ? " от " + c.contract_date.split("-").reverse().join(".") : ""} — ${ref.counterpartyName.get(c.counterparty_id) ?? ""}`]);
      case "employee":
        return ref.employees.map((e) => [String(e.id), e.full_name]);
      case "nomenclature":
        return ref.nomenclature.map((n) => [String(n.id), n.article ? `${n.name} (${n.article})` : n.name]);
      case "warehouse":
        return ref.warehouses.map((w) => [String(w.id), w.name]);
      case "cfo":
        return ref.cfos.map((c) => [String(c.id), ref.cfoLabel.get(c.id)]);
      case "cash_flow_item":
        return active(ref.articles, null).filter((a) => a.is_cash_flow).map((a) => [String(a.id), a.name]);
      case "pl_item":
        return active(ref.articles, null).filter((a) => a.is_pl).map((a) => [String(a.id), a.name]);
      default:
        return [];
    }
  }

  function renderAnalytics(posting, side, values) {
    const box = posting.querySelector(`.analytics[data-side="${side}"]`);
    const accountId = Number(posting.querySelector(`[name="${side}_account"]`).value) || null;
    const account = accountsById.get(accountId);
    const kinds = account ? account.analytics : [];
    box.replaceChildren(
      ...kinds.map((kind) => {
        const control = select([["", `— ${KIND_TITLES[kind].toLowerCase()} —`], ...optionsFor(kind, side, posting)], values[kind] ?? "", {
          "data-side": side, "data-kind": kind, "aria-label": `${KIND_TITLES[kind]} (${side === "debit" ? "дебет" : "кредит"})`,
        });
        // договоры зависят от контрагента — перерисовываем при его смене
        if (kind === "counterparty") control.addEventListener("change", () => renderAnalytics(posting, side, collectAnalytics(posting, side)));
        return el("label", { class: "analytic" }, KIND_TITLES[kind], control);
      }),
    );
    updateQuantityVisibility(posting);
  }

  function collectAnalytics(posting, side) {
    const result = {};
    for (const s of posting.querySelectorAll(`select[data-side="${side}"]`)) {
      if (s.value) result[s.dataset.kind] = Number(s.value);
    }
    return result;
  }

  function updateQuantityVisibility(posting) {
    const quantitative = ["debit", "credit"].some((side) => accountsById.get(Number(posting.querySelector(`[name="${side}_account"]`).value))?.is_quantitative);
    posting.querySelector(".qty").hidden = !quantitative;
  }

  function addPosting(p = {}) {
    const amount = el("input", { name: "amount", inputmode: "decimal", placeholder: "0,00" });
    amount.value = p.amount ? money(p.amount).replace(/ /g, " ") : "";
    const currency = select(ref.currencies.filter((c) => c.is_active || c.id === p.currency_id).map((c) => [String(c.id), c.code]), p.currency_id ?? ref.rubId, { name: "currency" });
    const qty = el("input", { name: "quantity", inputmode: "decimal" });
    qty.value = p.quantity ? quantity(p.quantity) : "";
    const rub = el("input", {
      name: "amount_rub", inputmode: "decimal",
      placeholder: p.amount_rub && !p.amount_rub_manual ? `${money(p.amount_rub)} по курсу` : "по курсу",
    });
    rub.value = p.amount_rub_manual ? money(p.amount_rub).replace(/ /g, " ") : "";
    const rubLabel = el("label", { class: "rub" }, "Сумма в ₽ (если не по курсу)", rub);
    const syncRub = () => { rubLabel.hidden = Number(currency.value) === ref.rubId; };
    currency.addEventListener("change", syncRub);

    const posting = el(
      "fieldset",
      { class: "posting" },
      el("legend", {}, "Проводка"),
      el(
        "div",
        { class: "posting-row" },
        el("label", {}, "Дебет", select(postableOptions, p.debit_account_id ?? "", { name: "debit_account" })),
        el("label", {}, "Кредит", select(postableOptions, p.credit_account_id ?? "", { name: "credit_account" })),
        el("label", { class: "amount" }, "Сумма", amount),
        el("label", { class: "currency" }, "Валюта", currency),
        el("label", { class: "qty" }, "Количество", qty),
        rubLabel,
      ),
      el(
        "div",
        { class: "posting-row" },
        el("div", { class: "analytics", "data-side": "debit" }),
        el("div", { class: "analytics", "data-side": "credit" }),
      ),
      editable ? el("button", { type: "button", class: "link remove", onclick: () => { posting.remove(); renumber(); } }, "Удалить проводку") : null,
    );
    for (const side of ["debit", "credit"]) {
      posting.querySelector(`[name="${side}_account"]`).addEventListener("change", () => {
        renderAnalytics(posting, side, collectAnalytics(posting, side));
        fillArticleAnalytics(posting);
      });
    }
    postingsBox.append(posting);
    renderAnalytics(posting, "debit", p.debit ?? {});
    renderAnalytics(posting, "credit", p.credit ?? {});
    syncRub();
    renumber();
    return posting;
  }

  function renumber() {
    [...postingsBox.querySelectorAll(".posting legend")].forEach((l, i) => { l.textContent = `Проводка ${i + 1}`; });
  }

  // Статья операции подставляется в аналитику «Статья ДДС / ОПиУ», если она там пуста
  function fillArticleAnalytics(posting) {
    const article = ref.articles.find((a) => a.id === Number(articleSelect.value));
    if (!article) return;
    for (const s of posting.querySelectorAll("select[data-kind]")) {
      if (s.value) continue;
      if ((s.dataset.kind === "cash_flow_item" && article.is_cash_flow) || (s.dataset.kind === "pl_item" && article.is_pl)) {
        s.value = String(article.id);
      }
    }
  }

  articleSelect.addEventListener("change", () => {
    const article = ref.articles.find((a) => a.id === Number(articleSelect.value));
    if (!article) return;
    const postings = [...postingsBox.querySelectorAll(".posting")];
    const blank = postings.find((p) => !p.querySelector('[name="debit_account"]').value && !p.querySelector('[name="credit_account"]').value);
    if (article.debit_account_id && (postings.length === 0 || blank)) {
      const target = blank ?? addPosting();
      target.querySelector('[name="debit_account"]').value = String(article.debit_account_id);
      target.querySelector('[name="credit_account"]').value = String(article.credit_account_id);
      renderAnalytics(target, "debit", {});
      renderAnalytics(target, "credit", {});
    }
    postingsBox.querySelectorAll(".posting").forEach(fillArticleAnalytics);
  });
  entitySelect.addEventListener("change", () => {
    for (const posting of postingsBox.querySelectorAll(".posting")) {
      for (const side of ["debit", "credit"]) renderAnalytics(posting, side, collectAnalytics(posting, side));
    }
  });

  function collect() {
    return {
      legal_entity_id: Number(entitySelect.value) || null,
      op_date: dateInput.value,
      article_id: Number(articleSelect.value) || null,
      description: description.value.trim(),
      postings: [...postingsBox.querySelectorAll(".posting")].map((p) => {
        const isRub = Number(p.querySelector('[name="currency"]').value) === ref.rubId;
        const qtyVisible = !p.querySelector(".qty").hidden;
        return {
          debit_account_id: Number(p.querySelector('[name="debit_account"]').value) || null,
          credit_account_id: Number(p.querySelector('[name="credit_account"]').value) || null,
          amount: parseNumber(p.querySelector('[name="amount"]').value),
          currency_id: Number(p.querySelector('[name="currency"]').value),
          amount_rub: isRub ? null : parseNumber(p.querySelector('[name="amount_rub"]').value),
          quantity: qtyVisible ? parseNumber(p.querySelector('[name="quantity"]').value) : null,
          debit: collectAnalytics(p, "debit"),
          credit: collectAnalytics(p, "credit"),
        };
      }),
    };
  }

  function showErrors(err) {
    errorBox.replaceChildren(...String(err.message).split("; ").map((m) => el("li", {}, m)));
    errorBox.scrollIntoView({ block: "nearest" });
  }

  async function run(action) {
    errorBox.replaceChildren();
    buttons.forEach((b) => { b.disabled = true; });
    try {
      await action();
      dialog.close();
      await onChanged();
    } catch (err) {
      showErrors(err);
    } finally {
      buttons.forEach((b) => { b.disabled = false; });
    }
  }

  async function save(postNow) {
    const body = collect();
    if (!body.legal_entity_id) throw new Error("Выберите юрлицо");
    const suffix = postNow ? "?post=true" : "";
    const saved = op
      ? await api.put(`/api/operations/${op.id}${suffix}`, body)
      : await api.post(`/api/organizations/${org.id}/operations${suffix}`, body);
    toast(postNow ? `Операция № ${saved.number} проведена` : `Черновик № ${saved.number} сохранён`);
  }

  async function transition(action, message) {
    await api.post(`/api/operations/${op.id}/${action}`);
    toast(message);
  }

  async function showHistory() {
    const events = await api.get(`/api/operations/${op.id}/history`);
    historyBox.replaceChildren(
      el("h3", {}, "История"),
      el("ul", {}, ...events.map((e) => el("li", {}, `${new Date(e.at).toLocaleString("ru-RU")} — ${e.user_name} ${ACTION_TITLES[e.action] ?? e.action}`))),
    );
  }

  // --- Кнопки ---
  const buttons = [];
  const button = (label, onclick, cls = "") => {
    const b = el("button", { type: "button", class: cls, onclick });
    b.textContent = label;
    buttons.push(b);
    return b;
  };
  const actions = el("div", { class: "form-actions" });
  if (op) actions.append(button("История", showHistory, "secondary push-left"));
  if (editable && op) {
    actions.append(button("Удалить черновик", () => {
      if (confirm(`Удалить черновик № ${op.number}?`)) run(async () => { await api.delete(`/api/operations/${op.id}`); toast("Черновик удалён"); });
    }, "secondary danger"));
  }
  if (op?.status === "posted" && canPost) {
    actions.append(
      button("Вернуть в черновик", () => run(() => transition("unpost", "Операция возвращена в черновик")), "secondary"),
      button("Аннулировать", () => run(() => transition("cancel", "Операция аннулирована")), "secondary danger"),
    );
  }
  if (op?.status === "draft" && canPost && !editable) {
    actions.append(button("Аннулировать", () => run(() => transition("cancel", "Операция аннулирована")), "secondary danger"));
  }
  if (op?.status === "cancelled" && canPost) {
    actions.append(button("Восстановить как черновик", () => run(() => transition("restore", "Операция восстановлена")), "secondary"));
  }
  actions.append(button(editable ? "Отмена" : "Закрыть", () => dialog.close(), "secondary"));
  if (editable) {
    actions.append(button("Сохранить черновик", () => run(() => save(false)), canPost ? "secondary" : ""));
    if (canPost) actions.append(button("Сохранить и провести", () => run(() => save(true))));
  }

  const title = op ? `Операция № ${op.number} — ${STATUS_TITLES[op.status]}` : "Новая операция";
  const dialog = el(
    "dialog",
    { class: "dialog wide" },
    el("h2", {}, title),
    el(
      "div",
      { class: "op-head" },
      el("label", {}, "Юрлицо", entitySelect),
      el("label", {}, "Дата", dateInput),
      el("label", { class: "grow" }, "Статья", articleSelect),
    ),
    el("label", { class: "block" }, "Описание", description),
    postingsBox,
    editable ? el("button", { type: "button", class: "link", onclick: () => addPosting() }, "+ Добавить проводку") : null,
    errorBox,
    historyBox,
    actions,
  );
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());

  for (const p of op?.postings ?? []) addPosting(p);
  if (!op) addPosting();
  if (!editable) dialog.querySelectorAll("input, select, textarea").forEach((n) => { n.disabled = true; });

  dialog.showModal();
  return dialog;
}

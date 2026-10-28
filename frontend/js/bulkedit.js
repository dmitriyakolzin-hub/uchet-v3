// Массовое изменение операций журнала: замены полей во всех выбранных операциях (docs/TZ.md, раздел 20, «Детали 10.1»).
import { api } from "./api.js";
import { el, toast } from "./ui.js";

const SIDES = [["both", "дебет и кредит"], ["debit", "дебет"], ["credit", "кредит"]];
const MODES = [["replace", "заменить целиком"], ["append", "дописать в конец"], ["substitute", "заменить часть текста"]];

/** Поля для замены и списки значений по справочникам организации. */
function fieldList(ref) {
  const label = (a) => ref.articleLabel.get(a.id);
  const postable = ref.accounts.filter((a) => !a.has_children && a.is_active).map((a) => [a.id, `${a.code} ${a.name}`]);
  return [
    { key: "article", title: "Статья операции", header: true, options: ref.leafArticles.filter((a) => a.is_active).map((a) => [a.id, label(a)]) },
    { key: "description", title: "Описание", header: true, text: true },
    { key: "op_date", title: "Дата", header: true, date: true },
    { key: "account", title: "Счёт проводки", options: postable, oldRequired: true },
    { key: "counterparty", title: "Контрагент", options: ref.counterparties.map((c) => [c.id, c.name]) },
    { key: "contract", title: "Договор", options: ref.contracts.map((c) => [c.id, `${c.name || `№ ${c.number}`} (${ref.counterpartyName.get(c.counterparty_id) ?? ""})`]) },
    { key: "cash_flow_item", title: "Статья ДДС", options: ref.leafArticles.filter((a) => a.is_cash_flow).map((a) => [a.id, label(a)]) },
    { key: "pl_item", title: "Статья ОПиУ", options: ref.leafArticles.filter((a) => a.is_pl).map((a) => [a.id, label(a)]) },
    { key: "cfo", title: "ЦФО", options: ref.cfos.map((c) => [c.id, ref.cfoLabel.get(c.id)]) },
    { key: "executor", title: "ЦФУ", options: ref.executors.map((e) => [e.id, ref.executorLabel.get(e.id)]) },
    { key: "employee", title: "Сотрудник", options: ref.employees.map((e) => [e.id, e.full_name]) },
    { key: "bank_account", title: "Расчётный счёт", options: ref.bankAccounts.map((b) => [b.id, `${b.name} ··${b.number.slice(-4)}`]) },
    { key: "nomenclature", title: "Номенклатура", options: ref.nomenclature.map((n) => [n.id, n.name]) },
    { key: "warehouse", title: "Склад", options: ref.warehouses.map((w) => [w.id, w.name]) },
    { key: "fixed_asset", title: "Основное средство", options: ref.fixedAssets.map((a) => [a.id, a.name]) },
  ];
}

const select = (items, label, value = "") => el("select", { "aria-label": label },
  ...items.map(([v, t]) => el("option", { value: String(v), selected: String(v) === String(value) }, t)));

export function openBulkEdit({ ref, org, ops, onDone }) {
  const fields = fieldList(ref);
  const byKey = new Map(fields.map((f) => [f.key, f]));
  const rowsBox = el("div", { class: "bulk-rows" });
  const resultBox = el("div");
  const numbers = new Map(ops.map((o) => [o.id, o.number]));
  const rows = [];

  function addRow() {
    const fieldSelect = select(fields.map((f) => [f.key, f.title]), "Что менять");
    const box = el("div", { class: "bulk-row" });
    const row = { fieldSelect, box, read: null };
    const remove = el("button", { type: "button", class: "link", title: "Убрать замену", onclick: () => {
      rows.splice(rows.indexOf(row), 1);
      box.remove();
    } }, "✕");

    function build() {
      const f = byKey.get(fieldSelect.value);
      const parts = [fieldSelect];
      if (f.text) {
        const mode = select(MODES, "Как менять описание");
        const old = el("input", { type: "text", placeholder: "какой текст заменить", "aria-label": "Заменяемый текст", hidden: true });
        const value = el("input", { type: "text", placeholder: "новый текст", "aria-label": "Новый текст" });
        mode.addEventListener("change", () => { old.hidden = mode.value !== "substitute"; });
        parts.push(mode, old, value);
        row.read = () => ({ field: f.key, mode: mode.value, old: old.value || null, new: value.value });
      } else if (f.date) {
        const value = el("input", { type: "date", "aria-label": "Новая дата" });
        parts.push(el("span", {}, "→"), value);
        row.read = () => {
          if (!value.value) throw new Error("Укажите новую дату");
          return { field: f.key, new: value.value };
        };
      } else {
        const side = f.header ? null : select(SIDES, "Сторона проводки");
        const old = f.header ? null : select([...(f.oldRequired ? [["", "— какой счёт —"]] : [["", "— где угодно —"]]), ...f.options],
          f.oldRequired ? "Какой счёт заменить" : "Только где сейчас");
        const value = select([["", "— новое значение —"], ...f.options], "Новое значение");
        if (side) parts.push(side, el("span", { class: "muted" }, f.oldRequired ? "заменить" : "где сейчас"), old);
        parts.push(el("span", {}, "→"), value);
        row.read = () => {
          if (!value.value) throw new Error(`«${f.title}»: выберите новое значение`);
          if (f.oldRequired && !old.value) throw new Error("Выберите, какой счёт заменить");
          return { field: f.key, new: Number(value.value), ...(side ? { side: side.value } : {}),
            ...(old && old.value ? { old: Number(old.value) } : {}) };
        };
      }
      box.replaceChildren(...parts, remove);
    }
    fieldSelect.addEventListener("change", build);
    build();
    rows.push(row);
    rowsBox.append(box);
  }

  async function send(dryRun) {
    let changes;
    try {
      if (!rows.length) throw new Error("Добавьте хотя бы одну замену");
      changes = rows.map((r) => r.read());
    } catch (err) {
      resultBox.replaceChildren(el("p", { class: "error" }, err.message));
      return null;
    }
    try {
      return await api.post(`/api/organizations/${org.id}/operations/bulk-edit`,
        { ids: ops.map((o) => o.id), changes, dry_run: dryRun });
    } catch (err) {
      resultBox.replaceChildren(el("p", { class: "error" }, err.message));
      return null;
    }
  }

  function show(result) {
    const head = result.dry_run
      ? `Изменится операций: ${result.changed}, без изменений: ${result.unchanged}, не изменится из-за ошибок: ${result.failed}`
      : `Изменено операций: ${result.changed}, без изменений: ${result.unchanged}, с ошибками: ${result.failed}`;
    const errors = result.results.filter((r) => !r.ok);
    const sample = result.results.filter((r) => r.changed).slice(0, 5);
    resultBox.replaceChildren(...[
      el("p", { class: result.failed ? "error" : "" }, head),
      errors.length ? el("ul", { class: "error-list" }, ...errors.map((r) => el("li", {}, `№ ${r.number ?? numbers.get(r.id) ?? r.id}: ${r.error}`))) : null,
      sample.length ? el("details", {}, el("summary", {}, `${result.dry_run ? "Что изменится" : "Что изменилось"} (первые 5 операций)`),
        el("ul", {}, ...sample.map((r) => el("li", {}, `№ ${r.number}: ${r.details.join("; ")}`)))) : null,
    ].filter(Boolean));
  }

  const check = el("button", { type: "button", class: "secondary", onclick: async () => {
    const result = await send(true);
    if (result) show(result);
  } }, "Посчитать");
  const apply = el("button", { type: "button", onclick: async () => {
    if (!confirm(`Применить замены к выбранным операциям (${ops.length} шт.)?`)) return;
    apply.disabled = true;
    const result = await send(false);
    apply.disabled = false;
    if (!result) return;
    show(result);
    toast(`Изменено операций: ${result.changed}`);
    await onDone();
  } }, "Применить");

  const dialog = el("dialog", { class: "dialog wide" },
    el("h2", {}, `Изменить выбранные операции: ${ops.length} шт.`),
    el("p", { class: "muted" }, "Выберите, что заменить. В проводках значение ставится только там, где у счёта есть такая аналитика; "
      + "«где сейчас» — менять только конкретное значение. Проведённые операции после изменения проверяются: "
      + "не прошедшие проверку остаются как были. Сначала нажмите «Посчитать» — ничего не изменится, покажем итог."),
    rowsBox,
    el("button", { type: "button", class: "link", onclick: addRow }, "+ ещё замена"),
    resultBox,
    el("div", { class: "form-actions" }, el("button", { type: "button", class: "secondary", onclick: () => dialog.close() }, "Закрыть"),
      check, apply));
  addRow();
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
}

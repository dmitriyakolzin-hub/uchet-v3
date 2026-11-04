// Примечания к цифрам отчётов и к операциям (docs/TZ.md, раздел 23): правый щелчок (на телефоне — долгое нажатие)
// по ячейке — окно примечаний этой цифры; у цифры с открытым примечанием — уголок цвета метки.
import { api } from "./api.js";
import { el, toast } from "./ui.js";

const DEFAULT_COLOR = "#f79009";

export async function loadLabels(orgId) {
  return (await api.get(`/api/organizations/${orgId}/note-labels`)).filter((l) => l.is_active);
}

const when = (iso) => new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });

/**
 * Окно примечаний одного места: список (с правкой, «решено», удалением) и форма нового.
 * base — поля нового примечания (report, row_key, column_key, фильтры…); notes — уже найденные для этого места.
 */
export function openNotes({ org, labels, title, base, notes, onChanged }) {
  let changed = false;
  const labelById = new Map(labels.map((l) => [l.id, l]));
  const list = el("div", { class: "note-list" });
  const errorBox = el("p", { class: "error", role: "alert" });

  const labelSelect = (value, aria) => {
    const s = el("select", { "aria-label": aria }, el("option", { value: "" }, "— без метки —"),
      ...labels.map((l) => el("option", { value: l.id, selected: l.id === value }, l.name)));
    return s;
  };
  const act = async (fn) => {
    errorBox.textContent = "";
    try {
      await fn();
      changed = true;
      renderList();
    } catch (err) {
      errorBox.textContent = err.message;
    }
  };

  function renderList() {
    list.replaceChildren(...(notes.length ? notes.map((n) => {
      const label = labelById.get(n.label_id);
      const text = el("p", { class: `note-text${n.status === "resolved" ? " muted" : ""}` }, n.text);
      const editBox = el("div");
      const buttons = el("div", { class: "row-actions" },
        el("button", { type: "button", class: "link", onclick: () => act(async () => {
          Object.assign(n, await api.patch(`/api/notes/${n.id}`, { status: n.status === "open" ? "resolved" : "open" }));
        }) }, n.status === "open" ? "Решено" : "Вернуть в работу"),
        n.can_edit ? el("button", { type: "button", class: "link", onclick: () => {
          const area = el("textarea", { rows: 3, "aria-label": "Текст примечания" });
          area.value = n.text;
          const label2 = labelSelect(n.label_id, "Метка примечания");
          editBox.replaceChildren(area, label2, el("button", { type: "button", onclick: () => act(async () => {
            const body = { text: area.value.trim(), ...(label2.value ? { label_id: Number(label2.value) } : { clear_label: true }) };
            Object.assign(n, await api.patch(`/api/notes/${n.id}`, body));
          }) }, "Сохранить"));
        } }, "Изменить") : "",
        n.can_edit ? el("button", { type: "button", class: "link danger", onclick: () => act(async () => {
          if (!confirm("Удалить примечание?")) return;
          await api.delete(`/api/notes/${n.id}`);
          notes = notes.filter((x) => x.id !== n.id);
        }) }, "Удалить") : "");
      return el("div", { class: "note-item" },
        el("div", { class: "note-head" },
          label ? el("span", { class: "note-label", style: `background:${label.color}` }, label.name) : "",
          el("span", { class: "muted small" }, `${n.author}, ${when(n.created_at)}${n.status === "resolved" ? " — решено" : ""}`)),
        text, editBox, buttons);
    }) : [el("p", { class: "muted" }, "Примечаний пока нет.")]));
  }

  const area = el("textarea", { rows: 3, "aria-label": "Новое примечание", placeholder: "Текст примечания" });
  const newLabel = labelSelect(null, "Метка нового примечания");
  const add = el("button", { type: "button", onclick: () => act(async () => {
    if (!area.value.trim()) throw new Error("Напишите текст примечания");
    const created = await api.post(`/api/organizations/${org.id}/notes`, {
      ...base, text: area.value.trim(), ...(newLabel.value ? { label_id: Number(newLabel.value) } : {}) });
    notes = [created, ...notes];
    area.value = "";
  }) }, "Добавить примечание");

  const dialog = el("dialog", { class: "dialog wide" },
    el("h2", {}, `Примечания: ${title}`), list,
    el("h3", {}, "Новое примечание"), area, el("div", { class: "filters" }, newLabel, add), errorBox,
    el("div", { class: "form-actions" }, el("button", { type: "button", class: "secondary", onclick: () => dialog.close() }, "Закрыть")));
  document.body.append(dialog);
  dialog.addEventListener("close", async () => {
    dialog.remove();
    if (changed) await onChanged?.();
  });
  renderList();
  dialog.showModal();
  area.focus();
}

/**
 * Примечания в таблице отчёта. report — код отчёта; variant() — вид внутри отчёта (например, kind план/факта);
 * ctx — контекст страницы «Отчёты»; cellTitle(cell) — подпись колонки; skip(field) — колонки без примечаний.
 * Место примечания: строка (key) + колонка (field) + юрлицо, ЦФО, ЦФУ отчёта; у колонок без месяца — ещё и период.
 */
export function tableNotes(table, { report, ctx, variant = () => "", columnTitle, rowTitle, monthly = (f) => /^\d{4}-\d{2}/.test(f),
  skip = (f) => f === "title" || f?.startsWith("_") }) {
  let notes = [];
  let labels = [];
  let labelsOrg = null;

  const scope = () => {
    const s = ctx.filters.scope();
    return { legal_entity_id: s.legal_entity_id ? Number(s.legal_entity_id) : null,
      cfo_id: s.cfo_id ? Number(s.cfo_id) : null, executor_id: s.executor_id ? Number(s.executor_id) : null };
  };
  const period = () => ({ date_from: ctx.filters.dateFrom(), date_to: ctx.filters.dateTo() });
  const restrictedCfo = () => ctx.org().cfo_scope ?? null;

  function sameScope(n) {
    const s = scope();
    // руководителю ЦФО без выбранного ЦФО отчёт строится по его ветке — примечание запомнило корень ветки
    const cfo = s.cfo_id ?? restrictedCfo();
    return n.legal_entity_id === s.legal_entity_id && n.cfo_id === cfo && n.executor_id === s.executor_id;
  }
  function samePeriod(n, field) {
    if (monthly(field)) return true;
    const p = period();
    return n.date_from === p.date_from && n.date_to === p.date_to;
  }
  const notesFor = (key, field) => notes.filter((n) => n.row_key === String(key) && n.column_key === field
    && n.variant === variant() && sameScope(n) && samePeriod(n, field));

  function allRows(rows) {
    return rows.flatMap((r) => [r, ...allRows(r.getTreeChildren?.() ?? [])]);
  }
  function mark() {
    for (const row of allRows(table.getRows())) {
      const key = row.getData().key;
      for (const cell of row.getCells()) {
        const field = cell.getField();
        const element = cell.getElement();
        const found = key === undefined || skip(field) ? [] : notesFor(key, field).filter((n) => n.status === "open");
        element.classList.toggle("has-note", found.length > 0);
        if (found.length) {
          const color = labels.find((l) => l.id === found[0].label_id)?.color ?? DEFAULT_COLOR;
          element.style.setProperty("--note-color", color);
          element.title = found.map((n) => n.text).join("\n—\n");
        } else if (element.title && element.classList.contains("note-titled")) {
          element.title = "";
        }
        element.classList.toggle("note-titled", found.length > 0);
      }
    }
  }

  async function refresh() {
    const org = ctx.org();
    if (labelsOrg !== org.id) {
      labels = await loadLabels(org.id);
      labelsOrg = org.id;
    }
    notes = await api.get(`/api/organizations/${org.id}/notes?report=${report}`);
    mark();
  }

  function open(cell) {
    const field = cell.getField();
    const row = cell.getRow().getData();
    if (skip(field) || row.key === undefined) return;
    const colTitle = columnTitle(field);
    const title = `${rowTitle(row)}, ${colTitle}`;
    openNotes({
      org: ctx.org(), labels, title, notes: notesFor(row.key, field),
      base: { report, variant: variant(), row_key: String(row.key), row_title: rowTitle(row).slice(0, 500),
        column_key: field, column_title: colTitle.slice(0, 100), ...scope(), ...period() },
      onChanged: () => refresh(),
    });
  }

  // Переход к примечанию из списка: раскрыть строку, прокрутить к ней и подсветить ячейку
  function focus(key, field) {
    const row = allRows(table.getRows()).find((r) => String(r.getData().key) === String(key));
    if (!row) {
      toast("Этой строки сейчас нет в отчёте (нет сумм за период или другой фильтр)", "error");
      return;
    }
    for (let parent = row.getTreeParent?.(); parent; parent = parent.getTreeParent?.()) parent.treeExpand();
    table.scrollToRow(row, "center", false).catch(() => {});
    const cell = row.getCells().find((c) => c.getField() === field);
    const element = cell?.getElement() ?? row.getElement();
    element.classList.add("note-focus");
    element.scrollIntoView?.({ block: "nearest", inline: "center" });
    setTimeout(() => element.classList.remove("note-focus"), 4000);
  }

  table.on("cellContext", (e, cell) => { e.preventDefault(); open(cell); });
  table.on("cellTapHold", (_e, cell) => open(cell));
  table.on("renderComplete", () => mark());
  table.on("dataTreeRowExpanded", () => mark());
  return { refresh, open, focus };
}

// --- Список примечаний организации (кнопка «Примечания (N)» рядом с выбором организации, шаг 13.2) ---

export const REPORT_TITLES = { osv: "ОСВ", dds: "ДДС", pl: "ОПиУ", management: "Управленческий", balance: "Баланс",
  debts: "Дебиторка и кредиторка", stock: "Склад", planfact: "План/факт", investments: "Инвестиции", plan: "Бюджет",
  operation: "Журнал" };

// примечание, к которому нужно перейти на странице «Отчёты» (переход со страницы журнала)
let pending = null;
export function takePendingNote() {
  const note = pending;
  pending = null;
  return note;
}
export function setPendingNote(note) { pending = note; }

const where = (n) => {
  const variant = n.report === "planfact" ? (n.variant === "cf" ? " ДДС" : " ОПиУ") : "";
  if (n.report === "operation") return n.row_title;
  return `${REPORT_TITLES[n.report] ?? n.report}${variant}: ${n.row_title}${n.column_title ? `, ${n.column_title}` : ""}`;
};

/**
 * Кнопка «Примечания (N)»: N — открытые примечания организации. getOrg() — выбранная организация;
 * onGo(note) — перейти к цифре или операции.
 */
export function notesButton({ getOrg, onGo }) {
  const button = el("button", { type: "button", class: "secondary notes-button" }, "Примечания");
  let labels = [];

  async function refresh() {
    try {
      const open = await api.get(`/api/organizations/${getOrg().id}/notes?status=open`);
      button.textContent = open.length ? `Примечания (${open.length})` : "Примечания";
      button.classList.toggle("has-open", open.length > 0);
    } catch {
      button.textContent = "Примечания";
    }
  }

  async function show() {
    const org = getOrg();
    labels = await loadLabels(org.id);
    const labelById = new Map(labels.map((l) => [l.id, l]));
    const statusSelect = el("select", { "aria-label": "Статус примечаний" },
      ...[["open", "Открытые"], ["resolved", "Решённые"], ["", "Все"]].map(([v, t]) => el("option", { value: v }, t)));
    const labelSelect = el("select", { "aria-label": "Метка" }, el("option", { value: "" }, "Все метки"),
      ...labels.map((l) => el("option", { value: l.id }, l.name)));
    const mine = el("input", { type: "checkbox", "aria-label": "Только мои" });
    const listBox = el("div");

    async function load() {
      const p = new URLSearchParams();
      if (statusSelect.value) p.set("status", statusSelect.value);
      if (labelSelect.value) p.set("label_id", labelSelect.value);
      if (mine.checked) p.set("mine", "true");
      const notes = await api.get(`/api/organizations/${org.id}/notes?${p}`);
      listBox.replaceChildren(notes.length ? el("table", { class: "plain plan-cells notes-table" },
        el("thead", {}, el("tr", {}, ...["Метка", "Где", "Примечание", "Автор", "Дата", ""].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...notes.map((n) => {
          const label = labelById.get(n.label_id);
          return el("tr", { class: n.status === "resolved" ? "muted" : "" },
            el("td", {}, label ? el("span", { class: "note-label", style: `background:${label.color}` }, label.name) : ""),
            el("td", {}, el("button", { type: "button", class: "link", onclick: () => { dialog.close(); onGo(n); } }, where(n))),
            el("td", { class: "note-text" }, n.text),
            el("td", {}, n.author),
            el("td", {}, when(n.created_at)),
            el("td", {}, el("button", { type: "button", class: "link", onclick: async () => {
              try {
                await api.patch(`/api/notes/${n.id}`, { status: n.status === "open" ? "resolved" : "open" });
                await load();
                await refresh();
              } catch (err) {
                toast(err.message, "error");
              }
            } }, n.status === "open" ? "Решено" : "Вернуть")));
        })))
        : el("p", { class: "muted" }, "Примечаний нет."));
    }
    for (const input of [statusSelect, labelSelect, mine]) input.addEventListener("change", () => load());
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, `Примечания: ${org.name}`),
      el("div", { class: "filters" }, statusSelect, labelSelect, el("label", { class: "inline check-inline" }, mine, "мои")),
      listBox,
      el("div", { class: "form-actions" }, el("button", { type: "button", class: "secondary", onclick: () => dialog.close() }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    await load();
    dialog.showModal();
  }

  button.addEventListener("click", () => show().catch((err) => toast(err.message, "error")));
  refresh();
  return { button, refresh };
}

/** Блок примечаний в форме операции журнала. */
export function operationNotes({ org, op }) {
  const box = el("div", { class: "op-notes" });
  let labels = [];
  async function render() {
    const notes = await api.get(`/api/organizations/${org.id}/notes?operation_id=${op.id}`);
    const open = notes.filter((n) => n.status === "open").length;
    box.replaceChildren(el("button", { type: "button", class: "link", onclick: async () => {
      try {
        labels = labels.length ? labels : await loadLabels(org.id);
        openNotes({ org, labels, title: `операция № ${op.number}`, notes,
          base: { report: "operation", operation_id: op.id }, onChanged: () => render() });
      } catch (err) {
        toast(err.message, "error");
      }
    } }, notes.length ? `Примечания: ${notes.length}${open ? ` (открытых ${open})` : ""}` : "Добавить примечание"));
  }
  render().catch(() => box.replaceChildren());
  return box;
}

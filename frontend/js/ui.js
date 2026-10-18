// Общие элементы интерфейса: создание элементов, формы в диалоге, уведомления, таблицы.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function toast(message, kind = "ok") {
  const box = el("div", { class: `toast toast-${kind}`, role: "status" }, message);
  document.body.append(box);
  setTimeout(() => box.remove(), kind === "error" ? 6000 : 3000);
}

function fieldInput(field, value) {
  const common = { name: field.name, id: `f-${field.name}`, required: field.required };
  if (field.type === "checkbox") {
    return el("input", { ...common, type: "checkbox", checked: Boolean(value) });
  }
  if (field.type === "select") {
    const select = el("select", common);
    for (const [code, title] of field.options) {
      select.append(el("option", { value: code, selected: code === (value ?? "") }, title));
    }
    return select;
  }
  if (field.type === "textarea") {
    const area = el("textarea", { ...common, rows: 3 });
    area.value = value ?? "";
    return area;
  }
  const input = el("input", {
    ...common,
    type: field.type || "text",
    autocomplete: field.type === "password" ? "new-password" : "off",
    placeholder: field.placeholder,
  });
  input.value = value ?? "";
  return input;
}

/**
 * Форма в диалоговом окне.
 * fields: [{name, label, type: text|password|select|checkbox|textarea, options, required, hint}]
 * extra: дополнительный элемент под полями (например, таблица ролей).
 * onSubmit(values) — вернуть промис; ошибка показывается в форме, окно не закрывается.
 */
export function openForm({ title, fields, values = {}, extra = null, submitText = "Сохранить", onSubmit }) {
  const errorBox = el("p", { class: "error", role: "alert" });
  const form = el("form", { class: "form", method: "dialog" });
  for (const field of fields) {
    const input = fieldInput(field, values[field.name]);
    if (field.type === "checkbox") {
      form.append(el("label", { class: "check" }, input, " ", field.label));
    } else {
      form.append(el("label", { for: input.id }, field.label, field.required ? " *" : ""), input);
      if (field.hint) form.append(el("small", { class: "muted" }, field.hint));
    }
  }
  if (extra) form.append(extra);
  const submit = el("button", { type: "submit" }, submitText);
  const cancel = el("button", { type: "button", class: "secondary" }, "Отмена");
  form.append(errorBox, el("div", { class: "form-actions" }, cancel, submit));

  const dialog = el("dialog", { class: "dialog" }, el("h2", {}, title), form);
  document.body.append(dialog);
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => dialog.remove());

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    const result = {};
    for (const field of fields) {
      const input = form.elements[field.name];
      if (field.type === "checkbox") result[field.name] = input.checked;
      else result[field.name] = input.value.trim();
    }
    submit.disabled = true;
    try {
      await onSubmit(result, form);
      dialog.close();
    } catch (err) {
      errorBox.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  });

  dialog.showModal();
  form.querySelector("input, select, textarea")?.focus();
  return dialog;
}

export function yesNo(cell) {
  return cell.getValue() ? "да" : "нет";
}

// Колонка с кнопкой «Изменить»; allowed(row) — показывать ли кнопку в строке.
export function editButton(onClick, allowed = () => true) {
  return {
    title: "",
    field: "_edit",
    width: 48,
    hozAlign: "center",
    headerSort: false,
    formatter: (cell) => (allowed(cell.getRow().getData()) ? "✎" : ""),
    cellClick: (_e, cell) => {
      const row = cell.getRow().getData();
      if (allowed(row)) onClick(row);
    },
    tooltip: (_e, cell) => (allowed(cell.getRow().getData()) ? "Изменить" : ""),
  };
}

// Таблица Tabulator (подключён обычным скриптом из frontend/vendor/tabulator).
// table.ready — промис, который выполняется, когда таблица построена и в неё можно загружать данные.
export function makeTable(container, options) {
  const table = new window.Tabulator(container, {
    layout: "fitColumns",
    placeholder: "Нет данных",
    height: "auto",
    ...options,
  });
  table.ready = new Promise((resolve) => table.on("tableBuilt", resolve));
  return table;
}

/** Значение null для пустой строки — для необязательных полей вроде КПП. */
export function orNull(value) {
  return value === "" ? null : value;
}

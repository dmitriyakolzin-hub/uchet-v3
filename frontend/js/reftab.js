// Общая вкладка справочника: таблица + кнопки «Добавить» / ✎ + форма в окне.
import { api } from "./api.js";
import { el, makeTable, openForm, toast } from "./ui.js";

/**
 * box — куда рисовать; options:
 *   title       — заголовок вкладки («Контрагенты»)
 *   addText     — подпись кнопки добавления
 *   listUrl     — GET и POST списка; itemUrl(id) — PATCH записи
 *   columns     — колонки Tabulator (без кнопки изменения)
 *   fields(row) — поля формы; row = null при создании. Флаги поля:
 *                 numeric (select с id → число), nullable ("" → null), createOnly (только при создании)
 *   canEdit     — показывать ли кнопки изменения
 *   tree        — показывать деревом: prepare(list) должен вернуть корни с _children
 *   prepare     — преобразование списка перед показом
 * Возвращает { reload, create(values) } — create открывает форму добавления с заполненными полями.
 */
export async function referenceTab(box, options) {
  const { title, addText, listUrl, itemUrl, columns, fields, canEdit, tree = false, prepare = (x) => x } = options;

  const tableBox = el("div");
  box.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("h2", {}, title),
      canEdit ? el("button", { type: "button", onclick: () => edit(null) }, addText) : null,
    ),
    tableBox,
  );

  const allColumns = [
    ...columns,
    {
      title: "Активен", field: "is_active", width: 105,
      formatter: (c) => (c.getValue() ? "да" : "архив"),
    },
  ];
  if (canEdit) {
    allColumns.push({
      title: "", field: "_edit", width: 48, hozAlign: "center", headerSort: false,
      formatter: () => "✎", cellClick: (_e, cell) => edit(cell.getRow().getData()),
    });
  }
  const table = makeTable(tableBox, {
    ...(tree ? { dataTree: true, dataTreeStartExpanded: true, dataTreeChildIndent: 18 } : {}),
    columns: allColumns,
    rowFormatter: (row) => row.getElement().classList.toggle("row-archived", !row.getData().is_active),
  });

  async function load() {
    await table.setData(prepare(await api.get(listUrl)));
  }

  function toBody(formFields, values) {
    const body = {};
    for (const f of formFields) {
      let value = values[f.name];
      if (f.numeric) value = value === "" ? null : Number(value);
      else if (f.nullable && value === "") value = null;
      body[f.name] = value;
    }
    return body;
  }

  function edit(row, preset = {}) {
    let formFields = fields(row, preset).filter((f) => !(row && f.createOnly));
    if (row) formFields = [...formFields, { name: "is_active", label: "Активен (снимите, чтобы убрать в архив)", type: "checkbox" }];
    const values = row
      ? Object.fromEntries(formFields.map((f) => [f.name, row[f.name] === null ? "" : row[f.name]]))
      : {
          ...Object.fromEntries(formFields.filter((f) => f.default !== undefined).map((f) => [f.name, f.default])),
          ...preset,
        };
    for (const f of formFields) if (f.numeric && values[f.name] !== undefined) values[f.name] = String(values[f.name]);
    openForm({
      title: row ? title : addText,
      fields: formFields,
      values,
      onSubmit: async (formValues) => {
        const body = toBody(formFields, formValues);
        if (row) await api.patch(itemUrl(row.id), body);
        else await api.post(listUrl, body);
        toast("Сохранено");
        await load();
      },
    });
  }

  await table.ready;
  await load();
  return { reload: load, create: (preset) => edit(null, preset) };
}

// Варианты для select: [[id, подпись], ...]; архивные записи помечаются
export function optionsOf(items, label, { empty } = {}) {
  const opts = items.map((i) => [String(i.id), i.is_active === false ? `${label(i)} (архив)` : label(i)]);
  return empty ? [["", empty], ...opts] : opts;
}

export function lookup(items, label) {
  const map = new Map(items.map((i) => [i.id, label(i)]));
  return (cell) => map.get(cell.getValue()) ?? "";
}

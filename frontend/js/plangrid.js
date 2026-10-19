// Сетка ввода плана версии бюджета: строки — статьи, колонки — месяцы; ячейка = плановая операция.
import { api } from "./api.js";
import { money, parseNumber, sumMoney } from "./format.js";
import { el, makeTable, toast } from "./ui.js";

const KINDS = { pl: "План ОПиУ (по ЦФО)", cf: "План ДДС" };

// Ввод «1 500,5» → «1500.5» до того, как значение попадёт в ячейку и итоги; неверный ввод — прежнее значение
function normalizeInput(value, _data, _type, _params, cell) {
  const raw = parseNumber(value);
  if (raw === null) return null;
  if (/^\d+(\.\d{1,2})?$/.test(raw)) return raw;
  toast("Введите сумму: число, не больше 2 знаков после запятой", "error");
  return cell ? cell.getValue() : null;
}

function moneyOrEmpty(cell) {
  const v = cell.getValue();
  return v === null || v === undefined || v === "" || Number(v) === 0 ? "" : money(v);
}

export async function openPlanGrid(view, { org, budget, version, onBack }) {
  const cfos = await api.get(`/api/organizations/${org.id}/cfos`);
  const names = new Map(cfos.map((c) => [String(c.id), c.name]));
  const label = (c) => c.path.split(".").map((id) => names.get(id)).join(" / ");
  // руководителю ЦФО — только его ветка
  const inBranch = (c) => !org.cfo_scope || c.path.split(".").includes(String(org.cfo_scope));
  const choices = cfos.filter((c) => c.is_active && inBranch(c)).sort((a, b) => label(a).localeCompare(label(b)));

  let kind = "pl";
  let cfoId = choices[0]?.id ?? null;
  let data = null;
  // у ЦФО с подчинёнными по умолчанию — свод по ветке (просмотр), ввод — в «Свой план»
  const hasChildren = (id) => cfos.some((c) => c.parent_id === id);
  let mode = hasChildren(cfoId) ? "branch" : "own";

  const kindTabs = el("div", { class: "tabs" });
  const modeTabs = el("div", { class: "tabs small-tabs" });
  const cfoSelect = el("select", { class: "inline-select", "aria-label": "ЦФО плана" },
    ...choices.map((c) => el("option", { value: c.id }, label(c))));
  const cfoLabel = el("label", { class: "inline" }, "ЦФО ", cfoSelect);
  const fileInput = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": "Файл плана" });
  const uploadButton = el("button", { type: "button", class: "secondary", onclick: () => { fileInput.value = ""; fileInput.click(); } }, "Загрузить из Excel");
  const status = el("p", { class: "muted" });
  const tableBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" },
      el("h1", {}, `${budget.title} — ${version.name}`),
      el("button", { type: "button", class: "secondary", onclick: onBack }, "← К бюджетам")),
    kindTabs,
    el("div", { class: "filters" }, cfoLabel, modeTabs,
      el("button", { type: "button", class: "secondary", onclick: () => download() }, "Скачать в Excel"),
      uploadButton, fileInput),
    status,
    tableBox,
  );

  let table = null;
  function newTable() {
    table?.destroy();
    const box = el("div");
    tableBox.replaceChildren(box);
    table = makeTable(box, {
      index: "key",
      groupBy: "group",
      groupHeader: (value) => value,
      columnCalcs: "group",
      ...(mode === "branch" ? { dataTree: true, dataTreeStartExpanded: false, dataTreeChildIndent: 18 } : {}),
      columns: [{ title: "Статья", field: "name" }],
    });
    table.on("cellEdited", onEdited);
    return table.ready ?? Promise.resolve();
  }

  function renderTabs() {
    const tabs = Object.entries(KINDS).filter(([k]) => k === "pl" || !org.restricted);
    kindTabs.replaceChildren(...tabs.map(([k, title]) =>
      el("button", { type: "button", class: `tab${k === kind ? " active" : ""}`, onclick: () => { kind = k; renderTabs(); load(); } }, title)));
    cfoLabel.hidden = kind !== "pl";
    const showModes = kind === "pl" && hasChildren(cfoId);
    modeTabs.hidden = !showModes;
    modeTabs.replaceChildren(...(showModes ? [["branch", "Свод по ветке"], ["own", "Свой план"]].map(([m, title]) =>
      el("button", { type: "button", class: `tab${m === mode ? " active" : ""}`, onclick: () => { mode = m; renderTabs(); load(); } }, title)) : []));
  }

  const sumCalc = (values) => sumMoney(values);

  async function load() {
    if (kind === "pl" && !cfoId) {
      status.textContent = "В организации нет ЦФО — сначала заведите дерево ЦФО в «Справочниках».";
      tableBox.replaceChildren();
      return;
    }
    const branch = kind === "pl" && mode === "branch" && hasChildren(cfoId);
    const params = new URLSearchParams({ kind, ...(kind === "pl" ? { cfo_id: cfoId } : {}), mode: branch ? "branch" : "own" });
    data = await api.get(`/api/budget-versions/${version.id}/grid?${params}`);
    await newTable();
    uploadButton.hidden = !data.editable;
    status.textContent = data.mode === "branch"
      ? "Свод по ветке: план этого ЦФО вместе со всеми подчинёнными — только просмотр. Нажмите ⊞ у статьи, "
        + "чтобы увидеть, из каких ЦФО сложилась сумма. Вводить план — в «Свой план» или в сетке нижестоящего ЦФО."
      : data.editable
      ? "Введите сумму в ячейку и нажмите Enter — она сразу сохраняется. Пустая ячейка или 0 — плана нет. "
        + "Суммы — положительные: доход или расход определяется статьёй."
      : version.status === "draft"
        ? "Только просмотр: недостаточно прав."
        : "Только просмотр: версия утверждена или в архиве. Для пересмотра создайте новую версию.";
    table.setColumns([
      { title: "Статья", field: "name", minWidth: 240, widthGrow: 3, frozen: true, headerSort: false },
      ...data.months.map((m) => ({
        title: m.title, field: m.key, hozAlign: "right", minWidth: 105, headerSort: false,
        editor: data.editable ? "input" : false, editorParams: { selectContents: true }, mutatorEdit: normalizeInput, formatter: moneyOrEmpty,
        bottomCalc: sumCalc, bottomCalcFormatter: moneyOrEmpty,
      })),
      { title: "Итого", field: "total", hozAlign: "right", minWidth: 120, headerSort: false, cssClass: "strong",
        formatter: moneyOrEmpty, bottomCalc: sumCalc, bottomCalcFormatter: moneyOrEmpty },
    ]);
    const rows = data.rows.map((r) => ({
      key: String(r.article_id), article_id: r.article_id, name: r.name, group: r.group, ...r.values,
      total: sumMoney(Object.values(r.values)),
      ...(r.parts?.length ? { _children: r.parts.map((p) => ({ key: `${r.article_id}:${p.cfo_id}`, name: p.name,
        group: r.group, ...p.values, total: sumMoney(Object.values(p.values)) })) } : {}),
    }));
    await table.setData(rows);
  }

  async function onEdited(cell) {
    const month = data.months.find((m) => m.key === cell.getField());
    if (!month) return;
    const row = cell.getRow();
    const raw = cell.getValue() ?? null; // уже приведено normalizeInput
    try {
      await api.put(`/api/budget-versions/${version.id}/cells`, {
        kind, cfo_id: kind === "pl" ? cfoId : null,
        cells: [{ article_id: row.getData().article_id, month: month.month, amount: raw }],
      });
      const values = data.months.map((m) => (m.key === month.key ? raw : row.getData()[m.key]));
      await row.update({ [month.key]: raw, total: sumMoney(values) });
      table.recalc();
    } catch (err) {
      toast(err.message, "error");
      cell.restoreOldValue();
    }
  }

  async function download() {
    try {
      if (data?.mode === "branch") {
        await api.download(`/api/budget-versions/${version.id}/branch.xlsx?cfo_id=${cfoId}`,
          `plan_svod_${budget.year}_v${version.number}.xlsx`);
        return;
      }
      await api.download(`/api/budget-versions/${version.id}/plan.xlsx?kind=${kind}`,
        `${kind === "pl" ? "plan_pl" : "plan_dds"}_${budget.year}_v${version.number}.xlsx`);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    fileInput.value = "";
    try {
      const r = await api.upload(`/api/budget-versions/${version.id}/plan?kind=${kind}`, file);
      const lines = [`Строк в файле: ${r.rows}`, `Изменено ячеек: ${r.changed}`];
      const dialog = el("dialog", { class: "dialog wide" },
        el("h2", {}, `План загружен: ${file.name}`),
        el("ul", {}, ...lines.map((l) => el("li", {}, l))),
        ...(r.errors.length ? [el("h3", {}, `Не загружено строк: ${r.errors.length}`),
          el("ul", { class: "error-list" }, ...r.errors.map((e) => el("li", {}, `Строка ${e.row}: ${e.error}`)))] : []),
        el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Понятно")));
      document.body.append(dialog);
      dialog.addEventListener("close", () => dialog.remove());
      dialog.showModal();
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  cfoSelect.addEventListener("change", () => {
    cfoId = Number(cfoSelect.value);
    mode = hasChildren(cfoId) ? "branch" : "own";
    renderTabs();
    load();
  });
  renderTabs();
  await load();
}

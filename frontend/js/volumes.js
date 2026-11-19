// План объёмов (docs/TZ.md, раздел 29, «Детали 20.6»): точка + товар + продажа / на дисконт / утиль по месяцам;
// «Рассчитать план» протягивает цепочку и пишет в план ОПиУ ячейки «из объёмов».
import { api } from "./api.js";
import { money, parseNumber } from "./format.js";
import { el, makeTable, openForm, toast } from "./ui.js";

const KINDS = [["sale", "Продажа"], ["discount", "На дисконт"], ["waste", "Утиль"]];
const REVENUE_KEY = "uchet3_volumes_revenue";

function quantityCell(cell) {
  const v = cell.getValue();
  return v === null || v === undefined || v === "" ? "" : Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}

/** Вкладка «План объёмов» в сетке бюджета. */
export async function renderVolumes(box, { org, budget, version }) {
  const [cfos, goods, articles] = await Promise.all([
    api.get(`/api/organizations/${org.id}/cfos`), api.get(`/api/organizations/${org.id}/nomenclature`),
    api.get(`/api/organizations/${org.id}/articles`)]);
  const byId = new Map(cfos.map((c) => [String(c.id), c]));
  const names = (c) => c.path.split(".").map((id) => byId.get(id)?.name).join(" / ");
  const link = (c) => c.path.split(".").reverse().map((id) => byId.get(id)?.chain_link).find(Boolean);
  const inBranch = (c) => !org.cfo_scope || c.path.split(".").includes(String(org.cfo_scope));
  const points = cfos.filter((c) => c.is_active && ["trade", "discount"].includes(link(c)) && inBranch(c))
    .sort((a, b) => names(a).localeCompare(names(b)));
  const discounts = cfos.filter((c) => c.is_active && link(c) === "discount").sort((a, b) => names(a).localeCompare(names(b)));
  const items = goods.filter((g) => g.is_active && g.route).sort((a, b) => a.name.localeCompare(b.name));
  const parents = new Set(articles.map((a) => a.parent_id).filter(Boolean));
  const revenues = articles.filter((a) => a.is_active && a.pl_section === "revenue" && !parents.has(a.id) && a.debit_account_id);

  let data = await api.get(`/api/budget-versions/${version.id}/volumes`);
  const canCalc = data.editable && !org.cfo_scope;
  const status = el("p", { class: "muted" });
  const tableBox = el("div");
  const fileInput = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": "Файл плана объёмов" });
  let saved = "";
  try { saved = localStorage.getItem(REVENUE_KEY) ?? ""; } catch { /* без памяти браузера */ }
  const revenueSelect = el("select", { "aria-label": "Статья выручки", class: "inline-select" },
    ...revenues.map((a) => el("option", { value: a.id, selected: String(a.id) === saved || (!saved && a.name === "Выручка от продаж") },
      a.code ? `${a.code} ${a.name}` : a.name)));
  box.replaceChildren(
    el("div", { class: "filters" },
      data.editable ? el("button", { type: "button", onclick: () => addRow() }, "+ строка") : "",
      el("button", { type: "button", class: "secondary", onclick: () => api.download(`/api/budget-versions/${version.id}/volumes.xlsx`,
        `plan_volumes_${budget.year}_v${version.number}.xlsx`).catch((err) => toast(err.message, "error")) }, "Скачать в Excel"),
      data.editable ? el("button", { type: "button", class: "secondary", onclick: () => { fileInput.value = ""; fileInput.click(); } }, "Загрузить из Excel") : "",
      fileInput,
      canCalc ? el("label", { class: "inline" }, "Выручка — статья ", revenueSelect) : "",
      canCalc ? el("button", { type: "button", onclick: () => calculate() }, "Рассчитать план") : ""),
    status, tableBox);
  status.textContent = "Строка — точка (ЦФО торговли или дисконта), товар с маршрутом и вид: продажа клиентам, на дисконт "
    + "или утиль; в ячейках — количество. «Рассчитать план» записывает в план ОПиУ выручку точек и внутренние обороты "
    + "по цепочке (точка ← логистика ← производство, доготовка — по составу) по ценам без НДС на 1-е число месяца. "
    + "Посчитанные строки в сетке ОПиУ помечены «(из объёмов)» и руками не правятся — меняйте объёмы и пересчитывайте."
    + (data.editable ? "" : " Только просмотр.");

  const table = makeTable(tableBox, {
    index: "key",
    columns: [
      { title: "Точка", field: "cfo_name", minWidth: 200, frozen: true, headerSort: false },
      { title: "Товар", field: "name", minWidth: 180, frozen: true, headerSort: false },
      { title: "Вид", field: "kind_title", width: 110, headerSort: false },
      { title: "Дисконт", field: "discount_name", minWidth: 120, headerSort: false },
      ...data.months.map((m) => ({
        title: m.title, field: m.key, hozAlign: "right", minWidth: 90, headerSort: false, formatter: quantityCell,
        editor: data.editable ? "input" : false, editorParams: { selectContents: true },
        mutatorEdit: (v) => (v === "" || v === null ? null : parseNumber(String(v))),
      })),
    ],
  });
  table.on("cellEdited", async (cell) => {
    const month = data.months.find((m) => m.key === cell.getField());
    if (!month) return;
    const r = cell.getRow().getData();
    try {
      await api.put(`/api/budget-versions/${version.id}/volumes`, { cells: [{ cfo_id: r.cfo_id, nomenclature_id: r.nomenclature_id,
        kind: r.kind, discount_cfo_id: r.discount_cfo_id, month: month.month, quantity: cell.getValue() || null }] });
    } catch (err) {
      toast(err.message, "error");
      cell.restoreOldValue();
    }
  });
  const toRow = (r) => ({ key: `${r.cfo_id}:${r.nomenclature_id}:${r.kind}:${r.discount_cfo_id ?? 0}`, ...r, ...r.values });
  async function load() {
    data = await api.get(`/api/budget-versions/${version.id}/volumes`);
    await table.setData(data.rows.map(toRow));
  }
  await table.ready;
  await table.setData(data.rows.map(toRow));

  function addRow() {
    if (!points.length || !items.length) {
      toast(!points.length ? "Нет точек: у ЦФО торговли и дисконта укажите «Звено» в «Справочниках» → «ЦФО»"
        : "Нет товаров с маршрутом: укажите маршрут в «Справочниках» → «Номенклатура»", "error");
      return;
    }
    const dialog = openForm({
      title: "Строка плана объёмов",
      fields: [
        { name: "cfo_id", label: "Точка", type: "select", options: points.map((c) => [String(c.id), names(c)]) },
        { name: "nomenclature_id", label: "Товар", type: "select", options: items.map((g) => [String(g.id), g.article ? `${g.article} ${g.name}` : g.name]) },
        { name: "kind", label: "Вид", type: "select", options: KINDS },
        { name: "discount_cfo_id", label: "На какой дисконт", type: "select",
          options: [["", "—"], ...discounts.map((c) => [String(c.id), names(c)])] },
      ],
      submitText: "Добавить строку",
      onSubmit: async (v) => {
        const row = { cfo_id: Number(v.cfo_id), nomenclature_id: Number(v.nomenclature_id), kind: v.kind,
          discount_cfo_id: v.kind === "discount" && v.discount_cfo_id ? Number(v.discount_cfo_id) : null };
        if (v.kind === "discount" && !row.discount_cfo_id) throw new Error("Выберите дисконт");
        const point = byId.get(String(row.cfo_id));
        const item = items.find((g) => g.id === row.nomenclature_id);
        await table.addRow(toRow({ ...row, cfo_name: names(point), name: item.name, article: item.article,
          kind_title: KINDS.find(([k]) => k === v.kind)[1],
          discount_name: row.discount_cfo_id ? names(byId.get(String(row.discount_cfo_id))) : "", values: {} }));
        toast("Строка добавлена — введите количество по месяцам");
      },
    });
    const form = dialog.querySelector("form");
    const toggle = () => { form.elements.discount_cfo_id.closest("label, div")?.toggleAttribute("hidden", form.elements.kind.value !== "discount"); };
    form.elements.kind.addEventListener("change", toggle);
  }

  async function calculate() {
    try { localStorage.setItem(REVENUE_KEY, revenueSelect.value); } catch { /* без памяти браузера */ }
    try {
      const r = await api.post(`/api/budget-versions/${version.id}/volumes/calculate`, { revenue_article_id: Number(revenueSelect.value) });
      if (r.missing.length) {
        const dialog = el("dialog", { class: "dialog wide" },
          el("h2", {}, "План не рассчитан: не хватает цен"),
          el("p", { class: "muted" }, "Добавьте цены в «Справочниках» → «Номенклатура» → «цены» (или маршрут / производство / состав) и нажмите «Рассчитать план» ещё раз."),
          el("ul", { class: "error-list" }, ...r.missing.map((m) => el("li", {}, m))),
          el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Понятно")));
        document.body.append(dialog);
        dialog.addEventListener("close", () => dialog.remove());
        dialog.showModal();
        return;
      }
      toast(`План рассчитан: изменено ячеек — ${r.cells}, выручка точек — ${money(r.total_revenue)} ₽`);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    fileInput.value = "";
    try {
      const r = await api.upload(`/api/budget-versions/${version.id}/volumes`, file);
      const dialog = el("dialog", { class: "dialog wide" },
        el("h2", {}, `План объёмов загружен: ${file.name}`),
        el("ul", {}, el("li", {}, `Строк в файле: ${r.rows}`), el("li", {}, `Изменено ячеек: ${r.changed}`)),
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
}

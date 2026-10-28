// Управленческий отчёт по форме организации: статьи и показатели в порядке загруженного справочника
// (docs/TZ.md, раздел 19, «Детали 9.4»); факт, план или план и факт по месяцам.
import { api } from "../api.js";
import { money } from "../format.js";
import { el, makeTable, openForm, toast } from "../ui.js";
import { nestRows, openDrill, TREE_OPTIONS } from "./common.js";

const MODES = [["fact", "Факт"], ["plan", "План"], ["plan_fact", "План и факт"]];
const PARTS = { fact: [["fact", "Факт"]], plan: [["plan", "План"]], plan_fact: [["plan", "План"], ["fact", "Факт"], ["dev", "Откл."]] };

function escape(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
}

export function managementReport({ box, ctx }) {
  let mode = "fact";
  let data = null;
  const modeTabs = el("div", { class: "tabs" });
  const hint = el("p", { class: "muted" });
  const tableBox = el("div");
  box.replaceChildren(modeTabs, hint, tableBox);
  const canEdit = () => ctx.org().permissions.includes("edit_reference");

  const table = makeTable(tableBox, {
    index: "key",
    ...TREE_OPTIONS,
    dataTreeStartExpanded: true,
    columns: [{ title: "Строка", field: "title" }],
    columnDefaults: { headerWordWrap: true },
    rowFormatter: (row) => {
      const r = row.getData();
      row.getElement().classList.toggle("row-total", r.kind === "header");
      row.getElement().classList.toggle("row-key", r.kind === "indicator");
    },
  });

  function renderTabs() {
    modeTabs.replaceChildren(...MODES.map(([k, title]) => el("button", {
      type: "button", class: `tab${k === mode ? " active" : ""}`, onclick: () => { mode = k; renderTabs(); load(); },
    }, title)));
  }

  table.on("cellClick", (e, cell) => {
    const r = cell.getRow().getData();
    if (cell.getField() === "title") {
      if (canEdit() && r.kind !== "header" && !e.target.closest(".tabulator-data-tree-control")) editRow(r);
      return;
    }
    const [colKey, part] = cell.getField().split("|");
    const col = data?.columns.find((c) => c.key === colKey);
    if (!col || !r.drill || part !== "fact" || !Number(cell.getValue() ?? 0)) return;
    openDrill({
      org: ctx.org(), ref: ctx.ref(), filters: ctx.filters, onChanged: () => ctx.reload(),
      title: `${r.code ?? ""} ${r.title}, ${col.title.toLowerCase()}`,
      extra: {
        period: r.drill.period, a_tree: r.drill.a_tree, opening: r.drill.opening,
        date_from: col.date_from, date_to: col.date_to,
        ...(r.drill.account_id ? { account_id: r.drill.account_id } : {}),
        ...(r.drill.exclude_corr_ids?.length ? { exclude_corr_account_id: r.drill.exclude_corr_ids } : {}),
      },
    });
  });

  function editRow(r) {
    openForm({
      title: `${r.code ?? ""} ${r.title}`,
      fields: [
        ...(r.kind === "indicator" ? [{ name: "formula", label: "Формула",
          placeholder: "например, 3.2 + 1.3", hint: "Коды строк формы, «+», «−» и скобки. Пусто — строка не рассчитывается." }] : []),
        { name: "invert", label: "Обратный знак (показывать сумму с противоположным знаком)", type: "checkbox" },
      ],
      values: { formula: r.formula, invert: r.invert },
      onSubmit: async (v) => {
        await api.put(`/api/report-form-rows/${r.id}`, { formula: v.formula ?? "", invert: v.invert });
        toast("Сохранено");
        await load();
      },
    });
  }

  async function load() {
    data = await api.get(`/api/organizations/${ctx.org().id}/reports/management?${ctx.filters.params({ mode })}`);
    if (!data.form_loaded) {
      hint.textContent = "Форма отчёта не загружена: загрузите справочник статей из Excel на странице «Статьи ДДС и ОПиУ» "
        + "(загрузка сохраняет и строки показателей).";
      await table.setData([]);
      return;
    }
    hint.textContent = "Строки — в порядке загруженного справочника. Статьи: ОПиУ — по начислению, статьи только ДДС — по деньгам; "
      + "доходы, расходы, поступления и выплаты — положительными числами. Показатели считаются по формулам"
      + (canEdit() ? " (щёлкните по названию строки, чтобы поправить формулу или знак)" : "")
      + ". Щёлкните по сумме факта статьи — откроются проводки.";
    const parts = PARTS[mode];
    const cellText = (c) => {
      const v = c.getValue();
      const r = c.getRow().getData();
      const text = v === null || v === undefined || Number(v) === 0 ? "" : money(v);
      return text && r.drill && c.getField().endsWith("|fact") ? `<span class="drill">${text}</span>` : text;
    };
    const sub = (c, part, title) => ({ title, field: `${c.key}|${part}`, hozAlign: "right", minWidth: 105, headerSort: false,
      formatter: cellText, cssClass: part === "dev" ? "dev-cell" : c.key === "total" ? "strong" : "" });
    table.setColumns([
      { title: "Строка", field: "title", minWidth: 300, widthGrow: 3, headerSort: false, frozen: true,
        formatter: (c) => {
          const r = c.getRow().getData();
          const code = r.code ? `<span class="muted">${escape(r.code)}</span> ` : "";
          const error = r.error ? ` <span class="over-budget" title="${escape(r.error)}">⚠ ${escape(r.error)}</span>` : "";
          const formula = r.kind === "indicator" && r.formula ? ` <span class="muted small">= ${escape(r.formula)}</span>` : "";
          return `<span style="padding-left:${r._indent}px">${code}${escape(r.title)}${formula}${error}</span>`;
        } },
      ...data.columns.map((c) => (parts.length === 1
        ? sub(c, parts[0][0], c.key === "total" ? "Итого" : c.title)
        : { title: c.key === "total" ? "Итого" : c.title, columns: parts.map(([part, title]) => sub(c, part, title)) })),
    ]);
    const rows = data.rows.map((r) => {
      const flat = { ...r, level: r.level };
      for (const [key, v] of Object.entries(r.values)) for (const [part, x] of Object.entries(v)) flat[`${key}|${part}`] = x;
      return flat;
    });
    await table.setData(nestRows(rows));
  }

  return {
    ready: table.ready.then(renderTabs),
    load,
    reset: async () => {},
    excel: () => api.download(`/api/organizations/${ctx.org().id}/reports/management.xlsx?${ctx.filters.params({ mode })}`,
      `management_${ctx.filters.dateFrom()}_${ctx.filters.dateTo()}.xlsx`),
  };
}

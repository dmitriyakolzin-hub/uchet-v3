// План/факт: ОПиУ или ДДС; по месяцам план, факт, отклонение; в итоге — % выполнения.
import { api } from "../api.js";
import { money } from "../format.js";
import { el, makeTable } from "../ui.js";
import { openDrill } from "./common.js";

const BOLD = new Set(["title", "section", "total"]);

function escape(text) {
  return String(text).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
}

export function planFactReport({ box, ctx }) {
  let kind = "pl";
  let versionId = "";
  let data = null;
  const kindTabs = el("div", { class: "tabs" });
  const versionSelect = el("select", { class: "inline-select wide-select", "aria-label": "План" });
  const hint = el("p", { class: "muted" });
  const tableBox = el("div");
  box.replaceChildren(
    kindTabs,
    el("div", { class: "filters" }, el("label", { class: "inline" }, "План: ", versionSelect)),
    hint, tableBox,
  );

  const table = makeTable(tableBox, {
    index: "key",
    columns: [{ title: "Статья", field: "title" }],
    columnDefaults: { headerWordWrap: true },
    rowFormatter: (row) => {
      const r = row.getData();
      row.getElement().classList.toggle("row-total", BOLD.has(r.kind));
      row.getElement().classList.toggle("row-key", r.kind === "total");
    },
  });

  function renderTabs() {
    kindTabs.replaceChildren(...[["pl", "ОПиУ"], ["cf", "ДДС"]].map(([k, title]) =>
      el("button", { type: "button", class: `tab${k === kind ? " active" : ""}`, onclick: () => { kind = k; renderTabs(); load(); } }, title)));
  }

  async function fillVersions() {
    const years = new Set();
    for (let y = Number(ctx.filters.dateFrom().slice(0, 4)); y <= Number(ctx.filters.dateTo().slice(0, 4)); y += 1) years.add(y);
    const budgets = (await Promise.all([...years].map((y) =>
      api.get(`/api/organizations/${ctx.org().id}/budgets?year=${y}`)))).flat();
    const STATUS = { draft: "черновик", approved: "утверждена", archived: "архив" };
    versionSelect.replaceChildren(
      el("option", { value: "" }, "Актуальный утверждённый бюджет (месяц → квартал → год)"),
      ...budgets.flatMap((b) => b.versions.map((v) =>
        el("option", { value: v.id, selected: String(v.id) === versionId }, `${b.title}: ${v.name} (${STATUS[v.status]})`))),
    );
    if (![...versionSelect.options].some((o) => o.value === versionId)) versionId = "";
  }

  const cellValue = (r, colKey, part) => r.values?.[colKey]?.[part];

  table.on("cellClick", (_e, cell) => {
    const [colKey, part] = cell.getField().split("|");
    const r = cell.getRow().getData();
    const col = data?.columns.find((c) => c.key === colKey);
    if (!col || !r.drill || !["plan", "fact"].includes(part) || Number(cellValue(r, colKey, part) ?? 0) === 0) return;
    const planVersion = part === "plan"
      ? (col.version_id ?? (versionId ? Number(versionId) : null))
      : null;
    if (part === "plan" && !planVersion) return; // итог по разным версиям — расшифровка по месяцам
    const m = data.money_account_ids;
    openDrill({
      org: ctx.org(), ref: ctx.ref(), filters: ctx.filters, onChanged: () => ctx.reload(),
      title: `${r.title}, ${col.title.toLowerCase()}, ${part === "plan" ? "план" : "факт"}`,
      extra: {
        period: r.drill.period, a: r.drill.a, opening: r.drill.opening,
        date_from: col.date_from, date_to: col.date_to,
        ...(m.length ? { account_id: m } : {}),
        ...(r.drill.exclude_corr ? { exclude_corr_account_id: m } : {}),
        ...(planVersion ? { plan_version_id: planVersion } : {}),
      },
    });
  });

  async function load() {
    const params = ctx.filters.params({ kind, ...(versionId ? { version_id: versionId } : {}) });
    data = await api.get(`/api/organizations/${ctx.org().id}/reports/plan-fact?${params}`);
    const noPlan = data.columns.filter((c) => c.key !== "total" && !c.version_id).map((c) => c.title.toLowerCase());
    hint.textContent = "План и факт считаются одинаково: факт — проведённые операции, план — плановые операции бюджета. "
      + "Отклонение = факт − план; расходы — со знаком минус. Щёлкните по сумме плана или факта — откроются операции."
      + (noPlan.length ? ` Нет утверждённого плана: ${noPlan.join(", ")}.` : "");
    const money0 = (c) => {
      const v = c.getValue();
      const r = c.getRow().getData();
      const [, part] = c.getField().split("|");
      const text = v === null || v === undefined || Number(v) === 0 ? "" : money(v);
      return text && r.drill && ["plan", "fact"].includes(part) ? `<span class="drill">${text}</span>` : text;
    };
    const sub = (c, part, title) => ({
      title, field: `${c.key}|${part}`, hozAlign: "right", minWidth: 100, headerSort: false, formatter: money0,
      cssClass: part === "dev" ? "dev-cell" : "",
    });
    table.setColumns([
      { title: "Статья", field: "title", minWidth: 240, widthGrow: 3, headerSort: false, frozen: true,
        formatter: (c) => `<span style="padding-left:${c.getRow().getData().level * 18}px">${escape(c.getValue())}</span>` },
      ...data.columns.map((c) => ({
        title: c.key === "total" ? "Итого" : c.title,
        columns: [sub(c, "plan", "План"), sub(c, "fact", "Факт"), sub(c, "dev", "Откл."),
          ...(c.key === "total" ? [{ title: "%", field: "total|pct", hozAlign: "right", width: 70, headerSort: false,
            formatter: (cell) => (cell.getValue() === null || cell.getValue() === undefined ? "" : `${String(cell.getValue()).replace(".", ",")}%`) }] : [])],
      })),
    ]);
    const rows = data.rows.map((r) => {
      const flat = { ...r };
      for (const [key, v] of Object.entries(r.values)) for (const [part, x] of Object.entries(v)) flat[`${key}|${part}`] = x;
      return flat;
    });
    await table.setData(rows);
  }

  versionSelect.addEventListener("change", () => { versionId = versionSelect.value; load(); });

  return {
    ready: table.ready.then(renderTabs),
    load: async () => { await fillVersions(); await load(); },
    reset: async () => { versionId = ""; },
    excel: () => api.download(
      `/api/organizations/${ctx.org().id}/reports/plan-fact.xlsx?${ctx.filters.params({ kind, ...(versionId ? { version_id: versionId } : {}) })}`,
      `plan_fact_${kind}_${ctx.filters.dateFrom()}_${ctx.filters.dateTo()}.xlsx`),
  };
}

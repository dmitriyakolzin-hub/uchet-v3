// Инвестиции: по каждому проекту — лимит, план, вложено (за период и с начала), оплачено, остаток лимита.
import { api } from "../api.js";
import { money } from "../format.js";
import { el, makeTable } from "../ui.js";
import { moneyCell, openDrill } from "./common.js";

// колонка → расшифровка факта: вложения — дебет 08 по ОС проекта, оплаты — дебет 60 по договорам проекта
const DRILL = {
  fact: { part: "fact", account: "08", period: "turnover", title: "вложено за период" },
  fact_total: { part: "fact", account: "08", period: "closing", title: "вложено с начала" },
  paid: { part: "paid", account: "60", period: "turnover", title: "оплачено за период" },
};

export function investmentsReport({ box, ctx }) {
  let versionId = "";
  let data = null;
  const versionSelect = el("select", { class: "inline-select wide-select", "aria-label": "План инвестиций" });
  const hint = el("p", { class: "muted" });
  const tableBox = el("div");
  box.replaceChildren(el("div", { class: "filters" }, el("label", { class: "inline" }, "План: ", versionSelect)), hint, tableBox);

  const plain = (c) => (Number(c.getValue()) ? money(c.getValue()) : "");
  const col = (field, title, drill = false) => ({
    title, field, hozAlign: "right", minWidth: 120, headerSort: false,
    formatter: drill ? (c) => (c.getRow().getData().project_id ? moneyCell(c) : plain(c)) : plain,
  });
  const table = makeTable(tableBox, {
    index: "key",
    columnDefaults: { headerWordWrap: true },
    columns: [
      { title: "Инвестпроект", field: "name", minWidth: 220, widthGrow: 3, headerSort: false },
      col("limit", "Лимит"), col("plan", "План за период"), col("fact", "Вложено за период", true),
      col("fact_total", "Вложено с начала", true), col("paid", "Оплачено за период", true),
      col("left", "Остаток лимита"),
    ],
  });
  table.on("cellClick", (_e, c) => {
    const r = c.getRow().getData();
    const d = DRILL[c.getField()];
    if (!d || !r.project_id || Number(c.getValue()) === 0) return;
    openDrill({
      org: ctx.org(), ref: ctx.ref(), filters: ctx.filters, onChanged: () => ctx.reload(),
      title: `${r.name}: ${d.title}`,
      extra: { period: d.period, side: "debit", account_id: data.accounts[d.account], project_id: r.project_id, project_part: d.part },
    });
  });

  async function fillVersions() {
    const years = new Set();
    for (let y = Number(ctx.filters.dateFrom().slice(0, 4)); y <= Number(ctx.filters.dateTo().slice(0, 4)); y += 1) years.add(y);
    const budgets = (await Promise.all([...years].map((y) => api.get(`/api/organizations/${ctx.org().id}/budgets?year=${y}`)))).flat();
    const STATUS = { draft: "черновик", approved: "утверждена", archived: "архив" };
    versionSelect.replaceChildren(
      el("option", { value: "" }, "Актуальный утверждённый бюджет (месяц → квартал → год)"),
      ...budgets.flatMap((b) => b.versions.map((v) =>
        el("option", { value: v.id, selected: String(v.id) === versionId }, `${b.title}: ${v.name} (${STATUS[v.status]})`))),
    );
    if (![...versionSelect.options].some((o) => o.value === versionId)) versionId = "";
  }

  const params = () => ctx.filters.params(versionId ? { version_id: versionId } : {});

  async function load() {
    data = await api.get(`/api/organizations/${ctx.org().id}/reports/investments?${params()}`);
    const titles = [...new Set(Object.values(data.plan_versions).map((v) => data.version_titles[v]))];
    hint.textContent = "Вложено — дебет 08 по основным средствам проекта (поле «Инвестпроект» в карточке ОС); оплачено — "
      + "дебет 60 по договорам проекта. Остаток лимита = лимит − вложено с начала проекта. Щёлкните по сумме — откроются проводки. "
      + (titles.length ? `План: ${titles.join("; ")}.` : "Утверждённого плана инвестиций на этот период нет.");
    await table.setData([...data.rows.map((r) => ({ key: String(r.project_id), ...r })), { key: "total", name: "Итого", ...data.total }]);
    table.getRow("total")?.getElement().classList.add("row-total");
  }

  versionSelect.addEventListener("change", () => { versionId = versionSelect.value; ctx.reload(); });

  return {
    ready: table.ready,
    load: async () => { await fillVersions(); await load(); },
    reset: async () => { versionId = ""; },
    excel: () => api.download(`/api/organizations/${ctx.org().id}/reports/investments.xlsx?${params()}`,
      `investments_${ctx.filters.dateFrom()}_${ctx.filters.dateTo()}.xlsx`),
  };
}

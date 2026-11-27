// «Управленческий» → «План по методам» (docs/TZ.md, раздел 31, «Детали 23.2»): месяц, версия бюджета; колонки
// методов (у статьи — только колонка её метода), «Сверху ИТОГ», бюджет версии по ЦФО и «Бюджет − ИТОГ».
import { api } from "../api.js";
import { PLAN_METHODS } from "../articles.js";
import { money } from "../format.js";
import { el, toast } from "../ui.js";

const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
const VERSION_STATUS = { draft: "черновик", approved: "утверждена", archived: "архив" };

function nextMonth() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() + 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function methodPlan({ box, ctx }) {
  let month = nextMonth();
  let versionId = null;
  let data = null;
  const monthInput = el("input", { type: "month", value: month, "aria-label": "Месяц плана" });
  const versionSelect = el("select", { "aria-label": "Версия бюджета" });
  const hint = el("p", { class: "muted" });
  const tableBox = el("div", { class: "method-plan-box" });
  box.replaceChildren(
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "Месяц ", monthInput),
      el("label", { class: "inline" }, "Версия бюджета ", versionSelect)),
    hint, tableBox);

  monthInput.addEventListener("change", async () => {
    if (!monthInput.value) return;
    month = monthInput.value;
    await loadVersions();
    await load();
  });
  versionSelect.addEventListener("change", () => { versionId = Number(versionSelect.value) || null; load(); });

  async function loadVersions() {
    const year = Number(month.slice(0, 4));
    const m = Number(month.slice(5, 7));
    const budgets = (await api.get(`/api/organizations/${ctx.org().id}/budgets?year=${year}`))
      .filter((b) => b.months.includes(m));
    const options = [];
    for (const b of budgets) for (const v of b.versions) options.push({ v, title: `${b.title} · ${v.name} (${VERSION_STATUS[v.status]})` });
    // по умолчанию — черновик (план вводят в нём), иначе утверждённая
    const preferred = options.find((o) => o.v.id === versionId) ?? options.find((o) => o.v.status === "draft")
      ?? options.find((o) => o.v.status === "approved") ?? options[0];
    versionId = preferred?.v.id ?? null;
    versionSelect.replaceChildren(...(options.length
      ? options.map((o) => el("option", { value: o.v.id, selected: o.v.id === versionId }, o.title))
      : [el("option", { value: "" }, "— нет бюджета на этот месяц —")]));
  }

  const cell = (value, cls = "") => el("td", { class: `num ${cls}`.trim() },
    value === null || value === undefined ? "·" : Number(value) === 0 ? "" : money(value));

  function render() {
    if (!data.form_loaded) {
      hint.textContent = "Форма отчёта не загружена: загрузите справочник статей из Excel на странице «Статьи ДДС и ОПиУ».";
      tableBox.replaceChildren();
      return;
    }
    const [y, m] = data.month.split("-");
    hint.textContent = `План на ${MONTHS[Number(m) - 1].toLowerCase()} ${y}. У каждой статьи — только колонка её метода `
      + "(голубая); «Сверху ИТОГ» пока равен ей (фиксация ИТОГ — следующий шаг). «Бюджет» — плановые операции версии "
      + "по ЦФО, по нему идёт план/факт; «Бюджет − ИТОГ» — расхождение. Методы статей — «Статьи ДДС и ОПиУ», "
      + "проценты и суммы — «Бюджеты» → «Нормативы».";
    const methodCols = data.columns.slice(0, 6);
    const head = el("tr", {}, el("th", {}, "Строка"), el("th", {}, "Метод"),
      ...data.columns.map((c) => el("th", { class: c.key === "itog" ? "num itog" : "num" }, c.title)));
    const body = data.rows.map((r) => {
      const title = el("td", { style: `padding-left:${8 + r.level * 16}px` },
        r.code ? el("span", { class: "muted" }, `${r.code} `) : "", r.title,
        r.error ? el("span", { class: "over-budget", title: r.error }, ` ⚠ ${r.error}`) : "");
      if (r.kind === "header") return el("tr", { class: "row-total" }, title, el("td", { colspan: data.columns.length + 1 }));
      const values = r.values ?? {};
      const diff = values.diff;
      return el("tr", { class: r.kind === "indicator" ? "row-key" : "" },
        title,
        el("td", {}, r.method ? el("span", { class: "tag" }, PLAN_METHODS[r.method]) : "",
          r.note ? el("span", { class: "muted small" }, ` ${r.note}`) : ""),
        ...methodCols.map((c) => cell(values[c.key], values[c.key] !== null && values[c.key] !== undefined ? "own-method" : "")),
        cell(values.itog, "itog"),
        cell(values.budget),
        cell(diff, diff === null || diff === undefined ? "" : Number(diff) > 0 ? "good" : Number(diff) < 0 ? "bad" : ""));
    });
    tableBox.replaceChildren(el("div", { class: "table-scroll" },
      el("table", { class: "recon method-plan" }, el("thead", {}, head), el("tbody", {}, ...body))));
  }

  async function load() {
    if (!versionId) {
      hint.textContent = "На этот месяц нет бюджета — создайте бюджет на странице «Бюджеты».";
      tableBox.replaceChildren();
      data = null;
      return;
    }
    try {
      data = await api.get(`/api/organizations/${ctx.org().id}/reports/method-plan?${params()}`);
      render();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  const params = () => ctx.filters.params({ month, version_id: versionId });

  return {
    async load() {
      await loadVersions();
      await load();
    },
    excel: () => (versionId
      ? api.download(`/api/organizations/${ctx.org().id}/reports/method-plan.xlsx?${params()}`, `method_plan_${month}.xlsx`)
      : toast("Выберите версию бюджета", "error")),
  };
}

// «Управленческий» → «План по методам» (docs/TZ.md, раздел 31, «Детали 23.2»): месяц, версия бюджета; колонки
// методов (у статьи — только колонка её метода), «Сверху ИТОГ», бюджет версии по ЦФО и «Бюджет − ИТОГ».
import { api } from "../api.js";
import { PLAN_METHODS } from "../articles.js";
import { money, parseNumber } from "../format.js";
import { openPlanCells } from "../plancells.js";
import { el, instruction, openForm, resizableColumns, toast } from "../ui.js";

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
  const hintText = el("p");
  const hint = instruction(hintText);
  const warning = el("p", { class: "muted" });
  // «Сверху ИТОГ» (шаг 23.4): фиксация плана, история
  const fixInfo = el("span", { class: "muted" });
  const fixButton = el("button", { type: "button", onclick: () => fixPlan() }, "Зафиксировать план");
  const historyButton = el("button", { type: "button", class: "secondary", onclick: () => showHistory() }, "История ИТОГ");
  const tableBox = el("div", { class: "method-plan-box" });
  box.replaceChildren(
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "Месяц ", monthInput),
      el("label", { class: "inline" }, "Версия бюджета ", versionSelect),
      fixInfo, fixButton, historyButton),
    hint, warning, tableBox);

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

  const canSupervise = () => ctx.org().permissions.includes("supervise");
  const cfoId = () => (ctx.filters.scope().cfo_id ? Number(ctx.filters.scope().cfo_id) : null);
  const itogEditable = () => canSupervise() && data?.itog_scope && data.version_status !== "archived";

  function editItog(r) {
    openForm({
      title: `Сверху ИТОГ: ${`${r.code ?? ""} ${r.title}`.trim()}`,
      fields: [{ name: "amount", label: "ИТОГ на месяц, ₽", placeholder: "пусто — по методу" }],
      values: { amount: r.itog_manual ? money(r.values.itog) : "" },
      extra: el("p", { class: "muted small" }, `По методу: ${money(r.values[r.method] ?? 0)}. `
        + "Пустое поле — ИТОГ снова равен методу. ИТОГ — утверждённая цифра статьи, вниз по ЦФО не делится."),
      submitText: "Сохранить",
      onSubmit: async (v) => {
        await api.put(`/api/budget-versions/${data.version_id}/itog`, { month: data.month, cfo_id: cfoId(),
          article_id: r.itog_article_id, amount: v.amount ? parseNumber(v.amount) : null });
        await load();
      },
    });
  }

  async function fixPlan() {
    if (!data) return;
    if (!confirm("Зафиксировать план месяца? Суммы методов 1–4 (нормативный, средний, фактический, расчётный) "
      + "запишутся в бюджет на свои ЦФО вместо прежних, ИТОГ статей сохранится как утверждённые цифры.")) return;
    try {
      const r = await api.post(`/api/budget-versions/${data.version_id}/itog/fix`, { month: data.month, cfo_id: cfoId() });
      toast(`План зафиксирован: записано ячеек ${r.written}, очищено ${r.cleared}`);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function showHistory() {
    if (!data) return;
    const params = new URLSearchParams({ month: data.month });
    if (cfoId()) params.set("cfo_id", cfoId());
    const events = await api.get(`/api/budget-versions/${data.version_id}/itog-events?${params}`);
    const ACTIONS = { edit: "изменил(а) ИТОГ", reset: "вернул(а) ИТОГ к методу", fix: "зафиксировал(а) план" };
    const box = el("dialog", { class: "dialog wide" }, el("h2", {}, "История «Сверху ИТОГ»"),
      events.length ? el("ul", { class: "history" }, ...events.map((e) => el("li", {},
        `${new Date(e.at).toLocaleString("ru-RU")} — ${e.user_name} ${ACTIONS[e.action] ?? e.action}`
        + (e.article_name ? `: ${e.article_name}` : "")
        + (e.action === "edit" ? `, ${e.old_amount === null ? "по методу" : money(e.old_amount)} → ${money(e.new_amount)}` : "")
        + (e.details ? ` (${e.details})` : "")))) : el("p", { class: "muted" }, "Изменений пока нет."),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => box.close() }, "Закрыть")));
    box.addEventListener("close", () => box.remove());
    document.body.append(box);
    box.showModal();
  }

  // своя колонка метода показывает и ноль (видно, что посчитано и вышло 0), остальные — пусто (шаг 24.3)
  const cell = (value, cls = "") => el("td", { class: `num ${cls}`.trim() },
    value === null || value === undefined ? "·" : Number(value) === 0 && !cls.includes("own-method") ? "" : money(value));

  function render() {
    fixButton.hidden = !itogEditable();
    historyButton.hidden = !data.itog_scope;
    fixInfo.textContent = !data.itog_scope ? "ИТОГ — без фильтра юрлица и ЦФУ"
      : data.fixed_at ? `План зафиксирован ${new Date(data.fixed_at).toLocaleString("ru-RU")} (${data.fixed_by})`
        : "План не зафиксирован";
    if (!data.form_loaded) {
      hint.hidden = true;
      warning.textContent = "Форма отчёта не загружена: загрузите справочник статей из Excel на странице «Статьи ДДС и ОПиУ».";
      tableBox.replaceChildren();
      return;
    }
    const [y, m] = data.month.split("-");
    hint.hidden = false;
    warning.textContent = "";
    hintText.textContent = `План на ${MONTHS[Number(m) - 1].toLowerCase()} ${y}. У каждой статьи — только колонка её метода `
      + "(голубая); «Сверху ИТОГ» по умолчанию равен ей, финансовый директор правит его щелчком (✎ — вручную). "
      + "«Зафиксировать план» записывает суммы методов 1–4 в бюджет по ЦФО и сохраняет ИТОГ. «Бюджет» — плановые операции версии "
      + "по ЦФО, по нему идёт план/факт; «Бюджет − ИТОГ» — расхождение. Щёлкните в колонке «Снизу ЦФО» или «Снизу ЦФУ», "
      + "чтобы ввести план своего ЦФО / ЦФУ. Методы статей — «Статьи ДДС и ОПиУ», "
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
        ...methodCols.map((c) => {
          const td = cell(values[c.key], values[c.key] !== null && values[c.key] !== undefined ? "own-method" : "");
          // по каким ЦФО сложилась сумма (нормативный, средний, фактический, расчётный) — при наведении (24.2)
          const parts = r.breakdown?.[c.key];
          if (parts?.length) {
            td.classList.add("has-breakdown");
            td.title = parts.map((p) => `${p.cfo}: ${money(p.amount)}`).join("\n");
          }
          // «Снизу ЦФО» / «Снизу ЦФУ» — ввод плановых операций прямо отсюда (23.3)
          if (["cfo", "executor"].includes(c.key) && r.kind === "article" && r.article_id && r.plan_kind) {
            td.classList.add("input-cell");
            td.title = c.key === "cfo" ? "Ввести план ЦФО" : "Ввести план ЦФУ";
            if (!td.textContent || td.textContent === "·") td.textContent = "+";
            td.addEventListener("click", () => openPlanCells({
              org: ctx.org(), ref: ctx.ref(), versionId: data.version_id, kind: r.plan_kind, articleId: r.article_id,
              months: [data.month], scope: ctx.filters.scope(), title: `${r.code ?? ""} ${r.title}`.trim(),
              executorFirst: c.key === "executor", onChanged: () => load(),
            }));
          }
          return td;
        }),
        (() => {
          const td = cell(values.itog, "itog");
          if (r.itog_manual) td.append(el("span", { class: "muted small", title: "ИТОГ поставлен вручную" }, " ✎"));
          if (r.kind === "article" && r.itog_article_id && itogEditable()) {
            td.classList.add("input-cell");
            td.title = "Изменить ИТОГ";
            if (td.textContent === "") td.textContent = "+";
            td.addEventListener("click", () => editItog(r));
          }
          return td;
        })(),
        cell(values.budget),
        cell(diff, diff === null || diff === undefined ? "" : Number(diff) > 0 ? "good" : Number(diff) < 0 ? "bad" : ""));
    });
    const table = el("table", { class: "recon method-plan" }, el("thead", {}, head), el("tbody", {}, ...body));
    tableBox.replaceChildren(el("div", { class: "table-scroll method-plan-scroll" }, table));
    resizableColumns(table, "method-plan");
  }

  async function load() {
    if (!versionId) {
      fixButton.hidden = historyButton.hidden = true;
      fixInfo.textContent = "";
      hint.hidden = true;
      warning.textContent = "На этот месяц нет бюджета — создайте бюджет на странице «Бюджеты».";
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

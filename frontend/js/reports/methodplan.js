// «Управленческий» → «План по методам» (docs/TZ.md, раздел 31, «Детали 23.2»; раздел 32, «Детали 24.4»): месяц, версия
// бюджета; колонки методов 1–6 (у каждой статьи считаются все, основная — голубая), «Сверху ИТОГ» — выбор финансового
// директора (колонка или своя сумма; пока не выбран — равен бюджету), бюджет версии по ЦФО и «Бюджет − ИТОГ».
import { api } from "../api.js";
import { PLAN_METHODS } from "../articles.js";
import { money, parseNumber } from "../format.js";
import { openPlanCells } from "../plancells.js";
import { el, instruction, openForm, resizableColumns, toast } from "../ui.js";

const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
const VERSION_STATUS = { draft: "черновик", approved: "утверждена", archived: "архив" };
const SOURCE_SHORT = { norm: "из 1. Нормативный", avg: "из 2. Средний", fact: "из 3. Фактический",
  calc: "из 4. Расчётный", cfo: "из 5. Снизу ЦФО", executor: "из 6. Снизу ЦФУ", manual: "✎ своя сумма",
  budget: "= бюджет", fixed: "зафиксировано" };

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
  const ownButton = el("button", { type: "button", class: "secondary", onclick: () => ownMethods() },
    "ИТОГ = основной метод");
  const historyButton = el("button", { type: "button", class: "secondary", onclick: () => showHistory() }, "История ИТОГ");
  const tableBox = el("div", { class: "method-plan-box" });
  box.replaceChildren(
    el("div", { class: "filters" },
      el("label", { class: "inline" }, "Месяц ", monthInput),
      el("label", { class: "inline" }, "Версия бюджета ", versionSelect),
      fixInfo, ownButton, fixButton, historyButton),
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

  const saveItog = async (r, body) => {
    try {
      await api.put(`/api/budget-versions/${data.version_id}/itog`, { month: data.month, cfo_id: cfoId(),
        article_id: r.itog_article_id, ...body });
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  };

  function editItog(r) {
    const choices = data.columns.slice(0, 6).filter((c) => r.values[c.key] !== null && r.values[c.key] !== undefined)
      .map((c) => [c.key, `${c.title}: ${money(r.values[c.key])}`]);
    // у расчётных и нормативных статей «не выбран» — ИТОГ по своей колонке (24.5)
    const auto = ["calc", "norm"].includes(r.method);
    const current = r.itog_auto || ["budget", "fixed"].includes(r.itog_source) ? "budget" : r.itog_source;
    openForm({
      title: `Сверху ИТОГ: ${`${r.code ?? ""} ${r.title}`.trim()}`,
      fields: [
        { name: "source", label: "Откуда ИТОГ", type: "select", options: [
          ["budget", auto ? `по методу статьи: ${money(r.values[r.method] ?? 0)} (не выбран)`
            : `= бюджет: ${money(r.values.budget ?? 0)} (не выбран)`], ...choices, ["manual", "Своя сумма"]] },
        { name: "amount", label: "Своя сумма, ₽", placeholder: "для «Своя сумма»" },
      ],
      values: { source: current, amount: r.itog_source === "manual" ? money(r.values.itog) : "" },
      extra: el("p", { class: "muted small" }, "Колонки 1–6 — независимые оценки, ИТОГ — выбранная из них или своя сумма. "
        + "Выбранная колонка до фиксации меняется вместе с данными. При фиксации: ИТОГ из «Снизу ЦФО» или «Снизу ЦФУ» — "
        + "в бюджете остаётся только эта колонка; иначе при обеих заполненных — большая из них; при пустых «снизу» "
        + "колонка 1–4 пишется в бюджет по своим ЦФО, своя сумма — только цель."),
      submitText: "Сохранить",
      onSubmit: async (v) => {
        if (v.source === "manual" && !v.amount) throw new Error("Введите сумму");
        const body = v.source === "budget" ? { source: null, amount: null }
          : v.source === "manual" ? { source: "manual", amount: parseNumber(v.amount) } : { source: v.source };
        await api.put(`/api/budget-versions/${data.version_id}/itog`, { month: data.month, cfo_id: cfoId(),
          article_id: r.itog_article_id, ...body });
        await load();
      },
    });
  }

  async function ownMethods() {
    if (!data) return;
    try {
      const r = await api.post(`/api/budget-versions/${data.version_id}/itog/own-methods`, { month: data.month, cfo_id: cfoId() });
      toast(r.count ? `ИТОГ взят из основного метода у статей: ${r.count}` : "Нечего менять: у статей ИТОГ уже выбран");
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function fixPlan() {
    if (!data) return;
    if (!confirm("Зафиксировать план месяца? В бюджете: ИТОГ из «Снизу ЦФО/ЦФУ» — остаётся только эта колонка; "
      + "при обеих «снизу» — большая; при пустых «снизу» — колонка 1–4, выбранная в ИТОГ, по своим ЦФО. "
      + "ИТОГ статей сохранится как утверждённые цифры.")) return;
    try {
      const r = await api.post(`/api/budget-versions/${data.version_id}/itog/fix`, { month: data.month, cfo_id: cfoId() });
      toast(`План зафиксирован: записано ячеек ${r.written}, удалено ${r.cleared}`);
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
    const ACTIONS = { edit: "изменил(а) ИТОГ", reset: "сбросил(а) ИТОГ", fix: "зафиксировал(а) план" };
    const box = el("dialog", { class: "dialog wide" }, el("h2", {}, "История «Сверху ИТОГ»"),
      events.length ? el("ul", { class: "history" }, ...events.map((e) => el("li", {},
        `${new Date(e.at).toLocaleString("ru-RU")} — ${e.user_name} ${ACTIONS[e.action] ?? e.action}`
        + (e.article_name ? `: ${e.article_name}` : "")
        + (e.action === "edit" ? `, ${e.old_amount === null ? "не выбран" : money(e.old_amount)} → ${money(e.new_amount)}` : "")
        + (e.details ? ` (${e.details})` : "")))) : el("p", { class: "muted" }, "Изменений пока нет."),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => box.close() }, "Закрыть")));
    box.addEventListener("close", () => box.remove());
    document.body.append(box);
    box.showModal();
  }

  // колонки методов: пусто — у метода нет данных, ноль виден как «0,00» (посчитано и вышло 0; 24.3, 24.4)
  const cell = (value, cls = "") => el("td", { class: `num ${cls}`.trim() },
    value === null || value === undefined ? "" : money(value));

  function render() {
    fixButton.hidden = ownButton.hidden = !itogEditable();
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
    hintText.textContent = `План на ${MONTHS[Number(m) - 1].toLowerCase()} ${y}. Колонки 1–6 считаются у каждой статьи сразу — `
      + "это независимые оценки, между собой они не складываются (пусто — у метода нет данных); голубая — основной метод "
      + "статьи. «Сверху ИТОГ» выбирает финансовый директор: значок ⇢ в ячейке метода берёт её значение в ИТОГ "
      + "(выбранная ячейка обведена), щелчок по ИТОГ — выбрать колонку или ввести свою сумму; пока не выбран, ИТОГ равен "
      + "бюджету, а у расчётных и нормативных статей — сразу своей колонке («авто»). «ИТОГ = основной метод» — взять "
      + "основную колонку у всех статей, где ИТОГ не выбран. Расчётный метод считается от колонки 4 базы, если база "
      + "расчётная, иначе — от её ИТОГ. «Зафиксировать план»: ИТОГ из «Снизу ЦФО/ЦФУ» — в бюджете остаётся только "
      + "эта колонка; при обеих заполненных — большая; при пустых «снизу» в бюджет по ЦФО пишется колонка 1–4, выбранная "
      + "в ИТОГ; ИТОГ сохраняется как утверждённая цифра. «Бюджет» — плановые операции версии по ЦФО (по нему идёт "
      + "план/факт). Щёлкните в «Снизу ЦФО» или «Снизу ЦФУ», чтобы ввести план своего ЦФО / ЦФУ. Методы статей — "
      + "«Статьи ДДС и ОПиУ», проценты и суммы — «Бюджеты» → «Нормативы».";
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
          const value = values[c.key];
          const has = value !== null && value !== undefined;
          const td = cell(value, c.key === r.method ? "own-method" : "");
          if (has && r.itog_source === c.key) td.classList.add("itog-from");
          // пояснение колонки и по каким ЦФО сложилась сумма — при наведении (24.2, 24.4)
          const tips = [];
          if (r.notes?.[c.key]) tips.push(r.notes[c.key]);
          const parts = r.breakdown?.[c.key];
          if (parts?.length) {
            td.classList.add("has-breakdown");
            tips.push(...parts.map((p) => `${p.cfo}: ${money(p.amount)}`));
          }
          if (tips.length) td.title = tips.join("\n");
          // «Снизу ЦФО» / «Снизу ЦФУ» — ввод плановых операций прямо отсюда (23.3)
          if (["cfo", "executor"].includes(c.key) && r.kind === "article" && r.article_id && r.plan_kind) {
            td.classList.add("input-cell");
            td.title = [c.key === "cfo" ? "Ввести план ЦФО" : "Ввести план ЦФУ", ...tips].join("\n");
            if (!td.textContent) td.textContent = "+";
            td.addEventListener("click", () => openPlanCells({
              org: ctx.org(), ref: ctx.ref(), versionId: data.version_id, kind: r.plan_kind, articleId: r.article_id,
              months: [data.month], scope: ctx.filters.scope(), title: `${r.code ?? ""} ${r.title}`.trim(),
              executorFirst: c.key === "executor", onChanged: () => load(),
            }));
          }
          // взять значение колонки в «Сверху ИТОГ» (24.4)
          if (has && r.kind === "article" && r.itog_article_id && itogEditable() && r.itog_source !== c.key) {
            td.append(el("button", { type: "button", class: "itog-pick", title: `Взять в ИТОГ: ${c.title}`,
              "aria-label": `Взять в ИТОГ: ${c.title}`,
              onclick: (event) => { event.stopPropagation(); saveItog(r, { source: c.key }); } }, "⇢"));
          }
          return td;
        }),
        (() => {
          const td = cell(values.itog, "itog");
          if (r.itog_source && r.kind === "article") {
            td.append(el("span", { class: "itog-src" }, SOURCE_SHORT[r.itog_source] + (r.itog_auto ? " (авто)" : "")
              + (r.itog_fixed ? " · зафикс." : "")));
          } else if (r.itog_manual) {
            td.append(el("span", { class: "itog-src" }, "есть своя сумма"));
          }
          if (r.kind === "article" && r.itog_article_id && itogEditable()) {
            td.classList.add("input-cell");
            td.title = "Выбрать ИТОГ";
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
      fixButton.hidden = ownButton.hidden = historyButton.hidden = true;
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

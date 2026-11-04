// Сетка ввода плана версии бюджета: строки — статьи, колонки — месяцы; ячейка = плановая операция.
import { api } from "./api.js";
import { money, parseNumber, sumMoney } from "./format.js";
import { el, makeTable, openForm, toast } from "./ui.js";

const KINDS = { pl: "План ОПиУ (по ЦФО)", cf: "План ДДС", inv: "План инвестиций" };

// Ввод «1 500,5» → «1500.5» до того, как значение попадёт в ячейку и итоги; неверный ввод — прежнее значение
function normalizeInput(value, _data, _type, _params, cell) {
  const raw = parseNumber(value);
  if (raw === null) return null;
  if (/^\d+(\.\d{1,2})?$/.test(raw)) return raw;
  toast("Введите сумму: число, не больше 2 знаков после запятой", "error");
  return cell ? cell.getValue() : null;
}

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);
}

function moneyOrEmpty(cell) {
  const v = cell.getValue();
  return v === null || v === undefined || v === "" || Number(v) === 0 ? "" : money(v);
}

export async function openPlanGrid(view, { org, budget, version, onBack }) {
  const [cfos, executors] = await Promise.all([
    api.get(`/api/organizations/${org.id}/cfos`), api.get(`/api/organizations/${org.id}/executors`)]);
  const executorName = new Map(executors.map((e) => [e.id, e.parent_id
    ? `${executors.find((x) => x.id === e.parent_id)?.name ?? ""} / ${e.name}` : e.name]));
  const executorChoices = executors.filter((e) => e.is_active)
    .sort((a, b) => executorName.get(a.id).localeCompare(executorName.get(b.id)));
  let executorId = executorChoices[0]?.id ?? null;
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
  const executorSelect = el("select", { class: "inline-select", "aria-label": "ЦФУ плана" },
    ...executorChoices.map((e) => el("option", { value: e.id }, executorName.get(e.id))));
  const executorLabel = el("label", { class: "inline" }, "ЦФУ ", executorSelect);
  const fileInput = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": "Файл плана" });
  const downloadButton = el("button", { type: "button", class: "secondary", onclick: () => download() }, "Скачать в Excel");
  const uploadButton = el("button", { type: "button", class: "secondary", onclick: () => { fileInput.value = ""; fileInput.click(); } }, "Загрузить из Excel");
  const status = el("p", { class: "muted" });
  const tableBox = el("div");
  // снизу вверх (docs/TZ.md, раздел 22, «Детали 12.2»): отметка «план сдан» у ЦФО и список, кто сдал
  const submitBox = el("div", { class: "filters submit-box" });
  const statusesButton = el("button", { type: "button", class: "secondary", onclick: () => showStatuses() }, "Кто сдал план");
  const topDownButton = el("button", { type: "button", class: "secondary", hidden: true, onclick: () => openTopDown() }, "Сверху вниз");
  const retroButton = el("button", { type: "button", class: "secondary", hidden: true, onclick: () => openRetro() }, "По среднему");
  const historyButton = el("button", { type: "button", class: "secondary", onclick: () => showHistory() }, "История изменений");
  const canDraft = org.permissions.includes("draft") && version.status === "draft";
  // администратор и фин. директор (право проводить) утверждают ЦФО и правят план после утверждения — с историей
  const supervisor = org.permissions.includes("post");
  const canPlan = canDraft || (supervisor && version.status !== "archived");

  view.replaceChildren(
    el("div", { class: "page-head" },
      el("h1", {}, `${budget.title} — ${version.name}`),
      el("button", { type: "button", class: "secondary", onclick: onBack }, "← К бюджетам")),
    kindTabs,
    el("div", { class: "filters" }, cfoLabel, executorLabel, modeTabs,
      downloadButton,
      uploadButton, fileInput, statusesButton, topDownButton, retroButton, historyButton),
    submitBox,
    status,
    tableBox,
  );

  const when = (iso) => new Date(iso).toLocaleDateString("ru-RU");

  // Сверху вниз (docs/TZ.md, раздел 22, «Детали 12.3»): цель ЦФО по статье → доли нижних ЦФО по факту 6 месяцев
  async function openTopDown() {
    const articles = data.rows.filter((r) => !r.executor_id).map((r) => [String(r.article_id), r.name]);
    const article = el("select", { "aria-label": "Статья цели" }, ...articles.map(([v, t]) => el("option", { value: v }, t)));
    const inputs = new Map(data.months.map((m) => [m.month,
      el("input", { inputmode: "decimal", placeholder: "—", "aria-label": `Цель: ${m.title}` })]));
    const listBox = el("div");
    const errorBox = el("p", { class: "error", role: "alert" });
    const monthTitle = new Map(data.months.map((m) => [m.month, m.title]));
    const articleName = new Map(articles.map(([v, t]) => [Number(v), t]));
    let targets = [];

    const fill = () => {
      for (const [month, input] of inputs) {
        const t = targets.find((x) => x.article_id === Number(article.value) && x.month === month);
        input.value = t ? money(t.target) : "";
      }
    };
    const renderList = () => listBox.replaceChildren(targets.length
      ? el("table", { class: "plain plan-cells" },
        el("thead", {}, el("tr", {}, ...["Статья", "Месяц", "Цель", "По низу", "Откл."].map((t, i) =>
          el("th", { class: i > 1 ? "num" : "" }, t)))),
        el("tbody", {}, ...targets.map((t) => el("tr", {},
          el("td", {}, articleName.get(t.article_id) ?? ""), el("td", {}, monthTitle.get(t.month) ?? ""),
          el("td", { class: "num" }, money(t.target)), el("td", { class: "num" }, money(t.bottom)),
          el("td", { class: `num${Number(t.deviation) ? " over-budget" : ""}` }, Number(t.deviation) ? money(t.deviation) : "")))))
      : el("p", { class: "muted" }, "Целей у этого ЦФО пока нет."));
    const reload = async () => {
      targets = await api.get(`/api/budget-versions/${version.id}/targets?cfo_id=${cfoId}`);
      renderList();
      fill();
    };
    article.addEventListener("change", fill);

    const apply = el("button", { type: "button" }, "Распределить");
    apply.addEventListener("click", async () => {
      errorBox.textContent = "";
      const body = { cfo_id: cfoId, article_id: Number(article.value),
        targets: [...inputs].map(([month, input]) => ({ month, amount: parseNumber(input.value) })) };
      try {
        const r = await api.post(`/api/budget-versions/${version.id}/targets/distribute`, body);
        const equal = r.months.filter((m) => m.target && !m.by_fact).map((m) => monthTitle.get(m.month).toLowerCase());
        toast("Цель распределена по нижним ЦФО" + (equal.length ? `; поровну (нет факта): ${equal.join(", ")}` : ""));
        targets = r.targets;
        renderList();
        changed = true;
      } catch (err) {
        errorBox.textContent = err.message;
      }
    });
    let changed = false;
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, `Сверху вниз: ${cfoSelect.selectedOptions[0]?.textContent ?? ""}`),
      el("p", { class: "muted" }, "Цель делится между самыми нижними ЦФО ветки пропорционально их факту по статье "
        + "за 6 месяцев перед месяцем плана (если месяц в будущем — за 6 месяцев до текущего); факта нет — поровну. "
        + "План нижних ЦФО по статье заменяется долями, потом его можно поправить вручную. Пусто — цель снимается."),
      el("label", { class: "block" }, "Статья", article),
      el("div", { class: "target-months" }, ...data.months.map((m) => el("label", {}, m.title, inputs.get(m.month)))),
      errorBox,
      el("div", { class: "form-actions" },
        el("button", { type: "button", class: "secondary", onclick: () => dialog.close() }, "Закрыть"), apply),
      el("h3", {}, "Цели и план по низу"),
      listBox);
    document.body.append(dialog);
    dialog.addEventListener("close", async () => { dialog.remove(); if (changed) await load(); });
    await reload();
    dialog.showModal();
  }

  // Ретроспективный (docs/TZ.md, раздел 22, «Детали 12.4»): пустые ячейки — среднее за 1 / 3 / 6 / 12 месяцев
  function openRetro() {
    const withBranch = kind === "pl" && hasChildren(cfoId);
    openForm({
      title: kind === "pl" ? `По среднему: ${cfoSelect.selectedOptions[0]?.textContent ?? ""}` : "По среднему: план ДДС",
      fields: [
        { name: "base", label: "База — сколько предыдущих месяцев", type: "select", required: true,
          options: [["1", "1 месяц"], ["3", "3 месяца"], ["6", "6 месяцев"], ["12", "12 месяцев"]],
          hint: "У каждого месяца плана — свои предыдущие месяцы: прошедшие — по факту, месяцы этого бюджета — по плану. "
            + "Заполняются только пустые ячейки." },
        ...(withBranch ? [{ name: "branch", label: "Вместе с подчинёнными ЦФО (каждый — по своему факту)", type: "checkbox" }] : []),
      ],
      values: { base: "3", branch: false },
      submitText: "Заполнить",
      onSubmit: async (v) => {
        const r = await api.post(`/api/budget-versions/${version.id}/retrospective`, {
          kind, cfo_id: kind === "pl" ? cfoId : null, branch: Boolean(v.branch), base: Number(v.base) });
        toast(`Заполнено ячеек: ${r.filled}; с планом не тронуто: ${r.kept}`);
        await load();
      },
    });
  }

  async function renderSubmit() {
    const show = kind === "pl" && cfoId && mode !== "executor";
    statusesButton.hidden = kind !== "pl";
    topDownButton.hidden = !(kind === "pl" && cfoId && hasChildren(cfoId) && canPlan && mode !== "executor");
    retroButton.hidden = !(canPlan && ((kind === "pl" && cfoId && mode !== "executor") || (kind === "cf" && !org.restricted)));
    historyButton.hidden = kind === "inv";
    if (!show) { submitBox.replaceChildren(); return; }
    const rows = await api.get(`/api/budget-versions/${version.id}/cfo-statuses`);
    const row = rows.find((r) => r.cfo_id === cfoId);
    if (!row) { submitBox.replaceChildren(); return; }
    const act = async (action) => {
      try {
        await api.post(`/api/budget-versions/${version.id}/cfo-statuses/${cfoId}/${action}`);
        toast({ submit: "План ЦФО отмечен как сданный", unsubmit: "Отметка снята", approve: "План ЦФО утверждён",
          unapprove: "Утверждение снято" }[action]);
        await load();
      } catch (err) {
        toast(err.message, "error");
      }
    };
    const approvedText = row.approved_at
      ? `Утверждён ${when(row.approved_at)}${row.approved_by ? ` (${row.approved_by})` : ""}`
      : row.locked ? "Утверждён вышестоящий ЦФО" : "";
    submitBox.replaceChildren(
      el("span", { class: row.submitted_at ? "badge ok" : "badge" }, row.submitted_at
        ? `План ЦФО сдан ${when(row.submitted_at)}${row.submitted_by ? ` (${row.submitted_by})` : ""}`
        : "План ЦФО не сдан"),
      row.changed_after ? el("span", { class: "over-budget" }, "изменён после сдачи") : "",
      approvedText ? el("span", { class: "badge approved" }, approvedText) : "",
      canDraft && !row.locked ? el("button", { type: "button", class: row.submitted_at ? "secondary" : "",
        onclick: () => act(row.submitted_at ? "unsubmit" : "submit") }, row.submitted_at ? "Вернуть в работу" : "Сдать план") : "",
      supervisor && version.status !== "archived" && (row.approved_at || !row.locked)
        ? el("button", { type: "button", class: "secondary", onclick: () => act(row.approved_at ? "unapprove" : "approve") },
          row.approved_at ? "Снять утверждение" : hasChildren(cfoId) ? "Утвердить ЦФО с подчинёнными" : "Утвердить ЦФО")
        : "",
    );
  }

  // История правок после утверждения (docs/TZ.md, раздел 22, «Детали 12.5»)
  async function showHistory() {
    const rows = await api.get(`/api/budget-versions/${version.id}/changes`);
    const cfoName = new Map(cfos.map((c) => [c.id, label(c)]));
    const monthTitle = (m) => ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
      "октябрь", "ноябрь", "декабрь"][m - 1];
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, "История изменений плана"),
      el("p", { class: "muted" }, "Правки после утверждения ЦФО или версии: кто, когда, что было и что стало."),
      rows.length ? el("table", { class: "plain plan-cells" },
        el("thead", {}, el("tr", {}, ...["Когда", "Кто", "ЦФО", "Статья", "Месяц", "Было", "Стало"].map((t, i) =>
          el("th", { class: i > 4 ? "num" : "" }, t)))),
        el("tbody", {}, ...rows.map((r) => el("tr", {},
          el("td", {}, new Date(r.changed_at).toLocaleString("ru-RU")), el("td", {}, r.user),
          el("td", {}, r.cfo_id ? cfoName.get(r.cfo_id) ?? "" : "—"),
          el("td", {}, r.article + (r.executor_id ? ` (${executorName.get(r.executor_id) ?? "ЦФУ"})` : "")),
          el("td", {}, `${monthTitle(r.month)} ${budget.year}`),
          el("td", { class: "num" }, r.old_amount ? money(r.old_amount) : "—"),
          el("td", { class: "num" }, r.new_amount ? money(r.new_amount) : "—")))))
        : el("p", { class: "muted" }, "Изменений после утверждения не было."),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async function showStatuses() {
    const rows = await api.get(`/api/budget-versions/${version.id}/cfo-statuses`);
    const leaves = rows.filter((r) => r.is_leaf);
    const done = leaves.filter((r) => r.submitted_at).length;
    const minLevel = Math.min(...rows.map((r) => r.level));
    const dialog = el("dialog", { class: "dialog wide" },
      el("h2", {}, "Кто сдал план"),
      el("p", { class: "muted" }, `Сдали ${done} из ${leaves.length} нижних ЦФО. Сумма — свой план ОПиУ ЦФО (без подчинённых).`),
      el("table", { class: "plain plan-cells" },
        el("thead", {}, el("tr", {}, el("th", {}, "ЦФО"), el("th", { class: "num" }, "Свой план"), el("th", {}, "Сдача"),
          el("th", {}, "Утверждение"))),
        el("tbody", {}, ...rows.map((r) => el("tr", {},
          el("td", { style: `padding-left:${(r.level - minLevel) * 18 + 6}px` }, r.name),
          el("td", { class: "num" }, Number(r.plan_total) ? money(r.plan_total) : ""),
          el("td", {}, r.submitted_at ? `сдан ${when(r.submitted_at)}${r.submitted_by ? `, ${r.submitted_by}` : ""}`
            + (r.changed_after ? " — изменён после сдачи" : "") : "не сдан"),
          el("td", {}, r.approved_at ? `утверждён ${when(r.approved_at)}${r.approved_by ? `, ${r.approved_by}` : ""}`
            : r.locked ? "утверждён выше" : ""))))),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => dialog.close() }, "Закрыть")));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

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
      ...(mode !== "own" ? { dataTree: true, dataTreeStartExpanded: false, dataTreeChildIndent: 18 } : {}),
      columns: [{ title: "Статья", field: "name" }],
    });
    table.on("cellEdited", onEdited);
    return table.ready ?? Promise.resolve();
  }

  function renderTabs() {
    const tabs = Object.entries(KINDS).filter(([k]) => k === "pl" || !org.restricted);
    kindTabs.replaceChildren(...tabs.map(([k, title]) =>
      el("button", { type: "button", class: `tab${k === kind ? " active" : ""}`, onclick: () => { kind = k; renderTabs(); load(); } }, title)));
    cfoLabel.hidden = kind !== "pl" || mode === "executor";
    executorLabel.hidden = kind !== "pl" || mode !== "executor";
    const modes = [
      ...(hasChildren(cfoId) ? [["branch", "Свод по ветке"]] : []),
      ["own", hasChildren(cfoId) ? "Свой план" : "План ЦФО"],
      ...(executorChoices.length ? [["executor", "По ЦФУ"]] : []),
    ];
    const showModes = kind === "pl" && modes.length > 1;
    modeTabs.hidden = !showModes;
    modeTabs.replaceChildren(...(showModes ? modes.map(([m, title]) =>
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
    const byExecutor = kind === "pl" && mode === "executor" && executorId;
    const params = new URLSearchParams(kind === "inv" ? { kind } : byExecutor ? { kind, mode: "executor", executor_id: executorId }
      : { kind, ...(kind === "pl" ? { cfo_id: cfoId } : {}), mode: branch ? "branch" : "own" });
    data = await api.get(`/api/budget-versions/${version.id}/grid?${params}`);
    await newTable();
    uploadButton.hidden = !data.editable || kind === "inv";
    downloadButton.hidden = kind === "inv";
    status.textContent = kind === "inv"
      ? "Строки — инвестпроекты (справочник «Инвестпроекты») и статьи вложений. Сумма в ячейке — вложения месяца "
        + "(Дт 08); плановая оплата по статье «Покупка основных средств» сама появляется в плане ДДС "
        + "со сдвигом оплаты проекта." + (data.rows.length ? "" : " Проектов пока нет — добавьте их в «Справочниках».")
        + (data.editable ? "" : " Только просмотр.")
      : data.mode === "executor"
      ? "План ЦФУ по всем ЦФО (у отдела — вместе с его исполнителями), только просмотр. Нажмите ⊞ у статьи — "
        + "увидите разбивку по ЦФО. Вводить план с ЦФУ — в сетке ЦФО: «+ ЦФУ» у статьи."
      : data.mode === "branch"
      ? "Свод по ветке: план этого ЦФО вместе со всеми подчинёнными — только просмотр. Нажмите ⊞ у статьи, "
        + "чтобы увидеть, из каких ЦФО сложилась сумма. Вводить план — в «Свой план» или в сетке нижестоящего ЦФО."
      : data.editable
      ? "Введите сумму в ячейку и нажмите Enter — она сразу сохраняется. Пустая ячейка или 0 — плана нет. "
        + "Суммы — положительные: доход или расход определяется статьёй."
        + (kind === "pl" && executorChoices.length ? " «+ ЦФУ» — строка статьи с исполнителем (кто делает расход)." : "")
        + (data.locked ? " План утверждён — каждое изменение записывается в «Историю изменений»." : "")
      : data.locked
        ? "Только просмотр: план утверждён — менять его могут администратор и финансовый директор."
        : version.status === "draft"
        ? "Только просмотр: недостаточно прав."
        : "Только просмотр: версия в архиве. Для пересмотра создайте новую версию.";
    const addExecutor = data.editable && kind === "pl" && executorChoices.length;
    table.setColumns([
      { title: "Статья", field: "name", minWidth: 240, widthGrow: 3, frozen: true, headerSort: false,
        tooltip: (_e, c) => { const r = c.getRow().getData(); return r.executor_id ? `${r.name}: ${r.executor_name}` : r.name; },
        formatter: (c) => {
          const r = c.getRow().getData();
          return r.executor_id ? `<span class="muted">↳ ${escapeHtml(r.executor_name)}</span>` : escapeHtml(c.getValue());
        } },
      ...(addExecutor ? [{ title: "", field: "_executor", width: 80, headerSort: false, frozen: true,
        formatter: (c) => (c.getRow().getData().executor_id ? "" : '<span class="link-like">+ ЦФУ</span>'),
        cellClick: (_e, c) => { if (!c.getRow().getData().executor_id) addExecutorRow(c.getRow()); } }] : []),
      ...data.months.map((m) => ({
        title: m.title, field: m.key, hozAlign: "right", minWidth: 105, headerSort: false,
        editor: data.editable ? "input" : false, editorParams: { selectContents: true }, mutatorEdit: normalizeInput, formatter: moneyOrEmpty,
        bottomCalc: sumCalc, bottomCalcFormatter: moneyOrEmpty,
      })),
      { title: "Итого", field: "total", hozAlign: "right", minWidth: 120, headerSort: false, cssClass: "strong",
        formatter: moneyOrEmpty, bottomCalc: sumCalc, bottomCalcFormatter: moneyOrEmpty },
    ]);
    const rows = data.rows.map((r) => ({
      key: `${r.project_id ?? 0}:${r.article_id}:${r.executor_id ?? 0}`, article_id: r.article_id,
      executor_id: r.executor_id ?? null, project_id: r.project_id ?? null,
      executor_name: r.executor_name ?? "", name: r.name, group: r.group, ...r.values,
      total: sumMoney(Object.values(r.values)),
      ...(r.parts?.length ? { _children: r.parts.map((p) => ({ key: `${r.article_id}:${p.cfo_id}`, name: p.name,
        group: r.group, ...p.values, total: sumMoney(Object.values(p.values)) })) } : {}),
    }));
    await table.setData(rows);
    await renderSubmit();
  }

  function addExecutorRow(row) {
    const base = row.getData();
    const present = new Set(table.getData().filter((r) => r.article_id === base.article_id).map((r) => r.executor_id));
    const options = executorChoices.filter((e) => !present.has(e.id)).map((e) => [String(e.id), executorName.get(e.id)]);
    if (!options.length) {
      toast("По этой статье строки есть для всех ЦФУ", "error");
      return;
    }
    openForm({
      title: `ЦФУ для статьи «${base.name}»`,
      fields: [{ name: "executor_id", label: "Исполнитель", type: "select", options,
        hint: "Появится строка статьи с этим ЦФУ — введите в неё суммы" }],
      submitText: "Добавить строку",
      onSubmit: async (v) => {
        const id = Number(v.executor_id);
        await table.addRow({ key: `${base.article_id}:${id}`, article_id: base.article_id, executor_id: id,
          executor_name: executorName.get(id), name: base.name, group: base.group, total: null }, false, row);
      },
    });
  }

  async function onEdited(cell) {
    const month = data.months.find((m) => m.key === cell.getField());
    if (!month) return;
    const row = cell.getRow();
    const raw = cell.getValue() ?? null; // уже приведено normalizeInput
    try {
      await api.put(`/api/budget-versions/${version.id}/cells`, {
        kind, cfo_id: kind === "pl" ? cfoId : null, executor_id: row.getData().executor_id ?? null,
        project_id: row.getData().project_id ?? null,
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

  executorSelect.addEventListener("change", () => { executorId = Number(executorSelect.value); load(); });
  cfoSelect.addEventListener("change", () => {
    cfoId = Number(cfoSelect.value);
    if (mode !== "executor") mode = hasChildren(cfoId) ? "branch" : "own";
    renderTabs();
    load();
  });
  renderTabs();
  await load();
}

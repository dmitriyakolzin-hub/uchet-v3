// Окно «Плановые операции» из отчётов (docs/TZ.md, раздел 22, «Детали 12.1»): плановые операции версии бюджета
// по статье (с подстатьями) и месяцам — просмотр, правка суммы, удаление, добавление. Итог равен цифре отчёта.
import { api } from "./api.js";
import { money, parseNumber } from "./format.js";
import { el, toast } from "./ui.js";

const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const STATUS = { draft: "черновик", approved: "утверждена", archived: "архив" };

function select(options, value, label) {
  const node = el("select", { "aria-label": label });
  for (const [v, title] of options) node.append(el("option", { value: String(v), selected: String(value ?? "") === String(v) }, title));
  return node;
}

/**
 * versionId — версия бюджета; kind — pl / cf; articleId — статья строки отчёта (группа — вместе с подстатьями);
 * months — ключи месяцев «YYYY-MM»; scope — фильтры отчёта {legal_entity_id, cfo_id, executor_id};
 * onChanged — после закрытия, если что-то поменяли
 */
export async function openPlanCells({ org, ref, versionId, kind, articleId, months, scope = {}, title, onChanged }) {
  const params = new URLSearchParams({ kind, article_id: articleId, ...scope });
  months.forEach((m) => params.append("month", m));
  const url = `/api/budget-versions/${versionId}/cells?${params}`;
  let changed = false;
  const body = el("div");
  const summary = el("p", { class: "muted" });
  const errorBox = el("p", { class: "error", role: "alert" });
  const inBranch = (c) => !org.cfo_scope || c.path.split(".").includes(String(org.cfo_scope));

  async function write(cell, amount) {
    errorBox.textContent = "";
    try {
      await api.put(`/api/budget-versions/${versionId}/cells`, {
        kind, cfo_id: cell.cfo_id, executor_id: cell.executor_id,
        cells: [{ article_id: cell.article_id, month: cell.month, amount }],
      });
      changed = true;
      await render();
    } catch (err) {
      errorBox.textContent = err.message;
    }
  }

  async function render() {
    const data = await api.get(url);
    const articleName = new Map(data.articles.map((a) => [a.id, a.label]));
    const many = data.articles.length > 1;
    summary.textContent = `${data.budget_title}: ${data.version.name} (${STATUS[data.version.status]}). `
      + `Итого ${money(data.total)}` + (data.editable
        ? (data.audited || data.locked_cfo_ids.length ? ". Правки утверждённого плана записываются в историю." : "")
        : " — только просмотр" + (data.version.status === "approved"
          ? ": версия утверждена — менять могут администратор и финансовый директор"
          : data.version.status !== "draft" ? ": версия в архиве" : ""));
    const rows = data.cells.map((c) => {
      // сервер говорит, можно ли менять ячейку (утверждённый ЦФО — только администратор и фин. директор)
      const canEdit = c.editable && (!org.cfo_scope || ref.cfos.some((x) => x.id === c.cfo_id && inBranch(x)));
      const amount = el("input", { inputmode: "decimal", value: money(c.amount), "aria-label": "Сумма плана", disabled: !canEdit });
      amount.addEventListener("change", () => write(c, parseNumber(amount.value)));
      return el("tr", {},
        el("td", {}, c.cfo_id ? ref.cfoLabel.get(c.cfo_id) ?? "—" : "—"),
        el("td", {}, c.executor_id ? ref.executorLabel.get(c.executor_id) ?? "" : c.project_id ? "оплата инвестпроекта" : ""),
        many ? el("td", {}, articleName.get(c.article_id) ?? "") : null,
        el("td", {}, `${MONTHS[c.month - 1]} ${data.year}`),
        el("td", { class: "num" }, amount),
        el("td", {}, canEdit ? el("button", { type: "button", class: "link", onclick: () => write(c, null) }, "удалить") : null));
    });
    const table = el("table", { class: "plain plan-cells" },
      el("thead", {}, el("tr", {}, el("th", {}, "ЦФО"), el("th", {}, "ЦФУ"), many ? el("th", {}, "Статья") : null,
        el("th", {}, "Месяц"), el("th", { class: "num" }, "Сумма"), el("th", {}))),
      el("tbody", {}, ...(rows.length ? rows : [el("tr", {}, el("td", { colspan: 6, class: "muted" }, "Плановых операций нет"))])));

    let add = null;
    if (data.editable && data.months.length && data.articles.length) {
      const cfo = select([...(kind === "cf" ? [["", "— без ЦФО —"]] : []),
        ...ref.cfos.filter((c) => inBranch(c) && !data.locked_cfo_ids.includes(c.id)).map((c) => [c.id, ref.cfoLabel.get(c.id)])],
      scope.cfo_id ?? "", "ЦФО новой операции");
      const executor = kind === "pl"
        ? select([["", "— без ЦФУ —"], ...ref.executors.map((e) => [e.id, ref.executorLabel.get(e.id)])], "", "ЦФУ новой операции")
        : null;
      const article = select(data.articles.map((a) => [a.id, a.label]), data.articles[0].id, "Статья новой операции");
      const month = select(data.months.map((m) => [m, `${MONTHS[m - 1]} ${data.year}`]), data.months[0], "Месяц новой операции");
      const amount = el("input", { inputmode: "decimal", placeholder: "0,00", "aria-label": "Сумма новой операции" });
      add = el("div", { class: "filters" },
        cfo, executor, many ? article : null, month, amount,
        el("button", { type: "button", onclick: () => {
          if (!parseNumber(amount.value)) { errorBox.textContent = "Укажите сумму"; return; }
          write({ cfo_id: Number(cfo.value) || null, executor_id: executor ? Number(executor.value) || null : null,
            article_id: Number(article.value), month: Number(month.value) }, parseNumber(amount.value));
        } }, "Добавить"));
    }
    body.replaceChildren(table, add ? el("h3", {}, "Добавить плановую операцию") : null, add ?? "");
  }

  const box = el("dialog", { class: "dialog wide" }, el("h2", {}, `Плановые операции: ${title}`), summary, body, errorBox,
    el("p", { class: "muted small" }, "Сумма 0 или пусто — плановая операция удаляется. "
      + "Добавленная операция с тем же ЦФО, ЦФУ и месяцем заменяет прежнюю сумму."),
    el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => box.close() }, "Закрыть")));
  box.addEventListener("close", async () => {
    box.remove();
    if (changed) {
      toast("План изменён");
      await onChanged?.();
    }
  });
  document.body.append(box);
  await render();
  box.showModal();
}

// «Бюджеты»: бюджеты на год / квартал / месяц и их версии (черновик → утверждена → архив).
import { api } from "../api.js";
import { el, openForm, toast } from "../ui.js";

const ORG_KEY = "uchet3_org";
const STATUS = { draft: "Черновик", approved: "Утверждена", archived: "Архив" };
const QUARTERS = ["I", "II", "III", "IV"];
const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
// Период бюджета в одном списке: «year:1», «quarter:2», «month:3»
const PERIODS = [
  ["year:1", "Год целиком"],
  ...QUARTERS.map((q, i) => [`quarter:${i + 1}`, `${q} квартал`]),
  ...MONTHS.map((m, i) => [`month:${i + 1}`, m]),
];

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

function dateTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export async function renderBudgets(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Бюджеты"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  const thisYear = new Date().getFullYear();
  let year = thisYear + (new Date().getMonth() >= 9 ? 1 : 0); // с октября — бюджет следующего года

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const yearSelect = el("select", { class: "inline-select", "aria-label": "Год" });
  const addButton = el("button", { type: "button", onclick: () => addBudget() }, "Новый бюджет");
  const list = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Бюджеты"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("p", { class: "muted" }, "Бюджет — на год, квартал или месяц. Ежемесячный пересмотр — новая версия: копия утверждённой, "
      + "правки, утверждение; прежняя утверждённая уходит в архив и остаётся для сравнения."),
    el("div", { class: "filters" }, el("label", { class: "inline" }, "Год ", yearSelect), addButton),
    list,
  );

  const canManage = () => org.permissions.includes("post");

  function fillYears() {
    const years = [];
    for (let y = thisYear - 2; y <= thisYear + 2; y += 1) years.push(y);
    yearSelect.replaceChildren(...years.map((y) => el("option", { value: y, selected: y === year }, y)));
  }

  async function act(promise, message) {
    try {
      await promise;
      toast(message);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function addBudget() {
    openForm({
      title: "Новый бюджет",
      fields: [
        { name: "year", label: "Год", type: "number", required: true },
        { name: "period", label: "Период", type: "select", options: PERIODS },
      ],
      values: { year, period: "year:1" },
      submitText: "Создать",
      onSubmit: async (values) => {
        const [kind, period] = values.period.split(":");
        await api.post(`/api/organizations/${org.id}/budgets`, { kind, period: Number(period), year: Number(values.year) });
        year = Number(values.year);
        fillYears();
        toast("Бюджет создан — первая версия-черновик готова к вводу");
        await load();
      },
    });
  }

  function addVersion(budget) {
    const source = budget.versions.find((v) => v.status === "approved") ?? budget.versions.at(-1);
    openForm({
      title: `Новая версия: ${budget.title}`,
      fields: [
        { name: "name", label: "Название", placeholder: "например, Пересмотр за март" },
        { name: "copy_from_id", label: "Основа", type: "select", options: [
          ["", "Пустая версия"],
          ...budget.versions.map((v) => [String(v.id), `Копия: ${v.name} (${STATUS[v.status].toLowerCase()})`]),
        ] },
      ],
      values: { copy_from_id: source ? String(source.id) : "" },
      submitText: "Создать",
      onSubmit: async (values) => {
        await api.post(`/api/budgets/${budget.id}/versions`, {
          name: values.name, copy_from_id: values.copy_from_id ? Number(values.copy_from_id) : null,
        });
        toast("Версия создана");
        await load();
      },
    });
  }

  function renameVersion(v) {
    openForm({
      title: "Название версии",
      fields: [{ name: "name", label: "Название", required: true }],
      values: { name: v.name },
      onSubmit: async (values) => {
        await api.patch(`/api/budget-versions/${v.id}`, { name: values.name });
        await load();
      },
    });
  }

  function versionRow(budget, v) {
    const actions = [];
    if (canManage()) {
      const link = (text, onclick, cls = "") => el("button", { type: "button", class: `link ${cls}`, onclick }, text);
      actions.push(link("Переименовать", () => renameVersion(v)));
      if (v.status === "draft") {
        actions.push(link("Утвердить", () => {
          if (confirm(`Утвердить «${v.name}»? Прежняя утверждённая версия уйдёт в архив.`)) {
            act(api.post(`/api/budget-versions/${v.id}/approve`), "Версия утверждена");
          }
        }));
        actions.push(link("Удалить", () => {
          if (confirm(`Удалить черновик «${v.name}»?`)) act(api.delete(`/api/budget-versions/${v.id}`), "Черновик удалён");
        }, "remove"));
      }
      if (v.status !== "archived") {
        actions.push(link("В архив", () => act(api.post(`/api/budget-versions/${v.id}/archive`), "Версия в архиве")));
      }
    }
    return el("tr", { class: `version-${v.status}` },
      el("td", {}, String(v.number)),
      el("td", {}, v.name),
      el("td", {}, el("span", { class: `status budget-${v.status}` }, STATUS[v.status])),
      el("td", {}, dateTime(v.created_at)),
      el("td", {}, dateTime(v.approved_at)),
      el("td", { class: "actions" }, ...actions));
  }

  function budgetCard(budget) {
    const allDrafts = budget.versions.every((v) => v.status === "draft");
    return el("section", { class: "check-box budget-card" },
      el("div", { class: "page-head" },
        el("h2", {}, budget.title),
        canManage() ? el("div", { class: "quick-actions" },
          el("button", { type: "button", class: "secondary", onclick: () => addVersion(budget) }, "Новая версия"),
          allDrafts ? el("button", { type: "button", class: "secondary danger", onclick: () => {
            if (confirm(`Удалить «${budget.title}» со всеми черновиками?`)) act(api.delete(`/api/budgets/${budget.id}`), "Бюджет удалён");
          } }, "Удалить бюджет") : null) : null),
      el("table", { class: "recon versions" },
        el("thead", {}, el("tr", {}, ...["№", "Версия", "Статус", "Создана", "Утверждена", ""].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...budget.versions.map((v) => versionRow(budget, v)))));
  }

  async function load() {
    addButton.hidden = !canManage();
    const budgets = await api.get(`/api/organizations/${org.id}/budgets?year=${year}`);
    list.replaceChildren(...(budgets.length
      ? budgets.map(budgetCard)
      : [el("p", { class: "muted" }, `На ${year} год бюджетов пока нет.`)]));
  }

  yearSelect.addEventListener("change", () => { year = Number(yearSelect.value); load(); });
  orgSelect.addEventListener("change", () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    load();
  });
  fillYears();
  await load();
}

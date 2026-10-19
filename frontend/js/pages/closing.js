// «Закрытие периода»: регламентные операции месяца, проверки, закрытие и открытие месяца, правила.
import { api } from "../api.js";
import { date as ruDate, money } from "../format.js";
import { openOperation } from "../opform.js";
import { el, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
const OP_STATUS = { draft: "черновик", posted: "проведена", cancelled: "аннулирована" };

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}
const mmYyyy = (iso) => `${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
const monthOf = (iso) => (iso ? `${MONTHS[Number(iso.slice(5, 7)) - 1].toLowerCase()} ${iso.slice(0, 4)}` : "");

// Месяц по умолчанию: следующий за закрытым, иначе прошлый
function defaultPeriod(closedThrough) {
  if (closedThrough) {
    const [y, m] = closedThrough.split("-").map(Number);
    return m === 12 ? { year: y + 1, month: 1 } : { year: y, month: m + 1 };
  }
  const now = new Date();
  return now.getMonth() === 0 ? { year: now.getFullYear() - 1, month: 12 } : { year: now.getFullYear(), month: now.getMonth() };
}

export async function renderClosing(view) {
  const orgs = (await api.get("/api/organizations")).filter((o) => o.permissions.includes("post") && !o.restricted);
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Закрытие периода"),
      el("p", { class: "muted" }, "Раздел доступен финансовому директору и администратору."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  let period = defaultPeriod(org.closed_through);

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const status = el("p", { class: "closing-status" });
  const monthBox = el("div", { class: "filters" });
  const routinesBox = el("div");
  const checksBox = el("div");
  const rulesBox = el("div");
  const logBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Закрытие периода"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("p", { class: "muted" }, "Месяц закрывается по порядку: выполните регламентные операции, начислите амортизацию и зарплату, "
      + "проведите черновики — затем «Закрыть месяц». Операции закрытого периода нельзя создать, изменить, провести или удалить "
      + "ни из одного раздела. Открыть можно последний закрытый месяц."),
    status, monthBox, routinesBox, checksBox, rulesBox, logBox,
  );

  async function act(promise, message) {
    try {
      const result = await promise;
      if (message) toast(message);
      return result;
    } catch (err) {
      toast(err.message, "error");
      return null;
    } finally {
      await load();
    }
  }

  async function showOperation(id) {
    const [op, ref] = await Promise.all([api.get(`/api/operations/${id}`), loadReferences(org.id)]);
    openOperation({ ref, org, op, perms: org.permissions, onChanged: () => load() });
  }

  function renderMonth() {
    const monthSelect = el("select", { class: "inline-select", "aria-label": "Месяц" },
      ...MONTHS.map((m, i) => el("option", { value: i + 1, selected: i + 1 === period.month }, m)));
    const thisYear = new Date().getFullYear();
    const years = [...new Set([thisYear - 2, thisYear - 1, thisYear, thisYear + 1, period.year])].sort();
    const yearSelect = el("select", { class: "inline-select", "aria-label": "Год" },
      ...years.map((y) => el("option", { value: y, selected: y === period.year }, y)));
    const change = () => { period = { year: Number(yearSelect.value), month: Number(monthSelect.value) }; load(); };
    monthSelect.addEventListener("change", change);
    yearSelect.addEventListener("change", change);
    monthBox.replaceChildren(el("label", { class: "inline" }, "Месяц ", monthSelect), yearSelect);
  }

  function renderRuns(runs) {
    const pending = runs.filter((r) => !r.run_id).length;
    routinesBox.replaceChildren(
      el("div", { class: "page-head section" }, el("h2", {}, "Регламентные операции месяца"),
        pending ? el("button", { type: "button", onclick: async () => {
          const rows = await act(api.post(`/api/organizations/${org.id}/routine-runs`, period));
          const failed = (rows ?? []).filter((r) => r.error);
          if (rows) toast(failed.length ? `Есть ошибки: ${failed.length} — см. таблицу` : "Регламентные операции выполнены", failed.length ? "error" : "ok");
          if (failed.length) renderRuns(rows);
        } }, `Выполнить (${pending})`) : null),
      runs.length
        ? el("table", { class: "recon versions assets" },
          el("thead", {}, el("tr", {}, ...["Дата", "Операция", "Юрлицо", "Сумма", "Состояние"].map((t) => el("th", {}, t)))),
          el("tbody", {}, ...runs.map((r) => el("tr", {},
            el("td", {}, ruDate(r.op_date)), el("td", {}, r.rule_name), el("td", {}, r.legal_entity_name),
            el("td", { class: "num" }, r.total === null ? "" : money(r.total)),
            el("td", {}, r.operation_id
              ? el("button", { type: "button", class: "link", onclick: () => showOperation(r.operation_id) },
                `№ ${r.operation_number} — ${OP_STATUS[r.operation_status]}`)
              : el("span", { class: r.error ? "error" : "status status-draft" }, r.error ?? "не выполнена"))))))
        : el("p", { class: "muted" }, "На этот месяц регламентных операций нет."),
    );
  }

  function renderChecks(data) {
    const mark = (c) => (c.ok ? "✓" : c.blocking ? "✗" : "!");
    const cls = (c) => (c.ok ? "check-ok" : c.blocking ? "check-fail" : "check-warn");
    const title = `${MONTHS[period.month - 1]} ${period.year}`;
    checksBox.replaceChildren(
      el("div", { class: "page-head section" }, el("h2", {}, `Проверки: ${title.toLowerCase()}`)),
      el("ul", { class: "checklist" }, ...data.checks.map((c) => el("li", { class: cls(c) },
        el("span", { class: "check-mark" }, mark(c)), " ", c.title, c.ok || c.blocking ? "" : " (предупреждение)",
        ...(c.ok ? [] : [el("ul", {}, ...c.details.map((d) => el("li", {}, d)))])))),
      el("div", { class: "quick-actions" },
        el("button", { type: "button", disabled: !data.can_close, onclick: () => {
          if (confirm(`Закрыть ${title.toLowerCase()}? Операции по ${title.toLowerCase()} включительно станут недоступны для изменений.`)) {
            act(api.post(`/api/organizations/${org.id}/closing/close`, period), "Месяц закрыт").then((r) => {
              if (r) { org.closed_through = r.closed_through; period = defaultPeriod(r.closed_through); load(); }
            });
          }
        } }, "Закрыть месяц"),
        data.closed_through ? el("button", { type: "button", class: "secondary danger", onclick: () => {
          if (confirm(`Открыть ${monthOf(data.closed_through)}? Операции этого месяца снова можно будет менять.`)) {
            act(api.post(`/api/organizations/${org.id}/closing/reopen`, period), "Месяц открыт").then((r) => {
              if (r) org.closed_through = r.closed_through;
            });
          }
        } }, `Открыть ${monthOf(data.closed_through)}`) : null),
    );
  }

  function editRule(rule) {
    openForm({
      title: `Регламентная операция: ${rule.name}`,
      fields: [
        { name: "name", label: "Название", required: true },
        { name: "day", label: "Число месяца", type: "number", required: true },
        { name: "valid_from", label: "Первый месяц", type: "month", required: true },
        { name: "valid_to", label: "Последний месяц (пусто — бессрочно)", type: "month" },
        { name: "post", label: "Проводить сразу", type: "checkbox" },
        { name: "is_active", label: "Действует", type: "checkbox" },
      ],
      values: { ...rule, valid_from: rule.valid_from.slice(0, 7), valid_to: rule.valid_to ? rule.valid_to.slice(0, 7) : "" },
      onSubmit: async (v) => {
        await api.patch(`/api/routines/${rule.id}`, {
          name: v.name, day: Number(v.day), valid_from: `${v.valid_from}-01`,
          valid_to: v.valid_to ? `${v.valid_to}-01` : null, post: v.post, is_active: v.is_active });
        toast("Сохранено");
        await load();
      },
    });
  }

  function renderRules(rules) {
    const link = (text, onclick, cls = "") => el("button", { type: "button", class: `link ${cls}`, onclick }, text);
    rulesBox.replaceChildren(
      el("div", { class: "page-head section" }, el("h2", {}, "Правила регламентных операций")),
      el("p", { class: "muted" }, "Новое правило: откройте в журнале операцию-образец (например, аренду) и нажмите "
        + "«Сделать регламентной». Чтобы изменить суммы или проводки — сделайте правило из исправленной операции, а старое снимите с «действует»."),
      rules.length
        ? el("table", { class: "recon versions assets" },
          el("thead", {}, el("tr", {}, ...["Название", "Юрлицо", "Число", "Действует", "Сумма", "Проводить", ""].map((t) => el("th", {}, t)))),
          el("tbody", {}, ...rules.map((r) => el("tr", { class: r.is_active ? "" : "muted" },
            el("td", {}, r.name), el("td", {}, r.legal_entity_name), el("td", { class: "num" }, String(r.day)),
            el("td", {}, r.is_active ? `с ${mmYyyy(r.valid_from)}${r.valid_to ? ` по ${mmYyyy(r.valid_to)}` : ""}` : "нет (архив)"),
            el("td", { class: "num" }, money(r.total)), el("td", {}, r.post ? "сразу" : "черновиком"),
            el("td", { class: "actions" }, link("Изменить", () => editRule(r)),
              link("Удалить", () => {
                if (confirm(`Удалить правило «${r.name}»?`)) act(api.delete(`/api/routines/${r.id}`), "Правило удалено");
              }, "remove"))))))
        : el("p", { class: "muted" }, "Правил пока нет."),
    );
  }

  function renderLog(log) {
    logBox.replaceChildren(
      el("div", { class: "page-head section" }, el("h2", {}, "История закрытий")),
      log.length
        ? el("table", { class: "recon versions assets" },
          el("thead", {}, el("tr", {}, ...["Когда", "Кто", "Действие"].map((t) => el("th", {}, t)))),
          el("tbody", {}, ...log.map((r) => el("tr", {},
            el("td", {}, new Date(r.at).toLocaleString("ru-RU")), el("td", {}, r.user_name),
            el("td", {}, r.action === "close" ? `Закрыт ${monthOf(r.closed_through)}`
              : `Открыт ${monthOf(r.previous)}${r.closed_through ? ` (закрыто по ${ruDate(r.closed_through)})` : ""}`)))))
        : el("p", { class: "muted" }, "Период ещё не закрывали."),
    );
  }

  async function load() {
    renderMonth();
    const q = `year=${period.year}&month=${period.month}`;
    const [data, runs, rules] = await Promise.all([
      api.get(`/api/organizations/${org.id}/closing?${q}`),
      api.get(`/api/organizations/${org.id}/routine-runs?${q}`),
      api.get(`/api/organizations/${org.id}/routines`),
    ]);
    org.closed_through = data.closed_through;
    status.replaceChildren(data.closed_through
      ? el("strong", {}, `Период закрыт по ${ruDate(data.closed_through)}`)
      : el("strong", {}, "Период не закрыт"));
    renderRuns(runs);
    renderChecks(data);
    renderRules(rules);
    renderLog(data.log);
  }

  orgSelect.addEventListener("change", () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    period = defaultPeriod(org.closed_through);
    load();
  });
  await load();
}

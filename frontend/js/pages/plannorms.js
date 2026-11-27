// Вкладка «Нормативы» страницы «Бюджеты» (docs/TZ.md, раздел 31, «Детали 23.1»): для «Расчётного» — базовый
// показатель и процент, для «Фактического» — сумма в месяц вручную (к регламентным операциям). Задаёт финансовый
// директор; норматив действует с выбранного месяца до следующего.
import { api } from "../api.js";
import { articleIndex, PLAN_METHODS } from "../articles.js";
import { money, parseNumber } from "../format.js";
import { el, instruction, openForm, toast } from "../ui.js";

const KINDS = { calc: "Расчётный", fact: "Фактический", norm: "Нормативный" };
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
  "ноябрь", "декабрь"];
const monthTitle = (iso) => `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;

export async function renderNorms(box, org) {
  const canEdit = org.permissions.includes("supervise");
  let norms;
  let articles;
  let cfos;
  let form;
  try {
    [norms, articles, cfos, form] = await Promise.all([
      api.get(`/api/organizations/${org.id}/plan-norms`),
      api.get(`/api/organizations/${org.id}/articles`),
      api.get(`/api/organizations/${org.id}/cfos`),
      api.get(`/api/organizations/${org.id}/report-form-rows`),
    ]);
  } catch (err) {
    box.replaceChildren(el("p", { class: "error" }, err.message));
    return;
  }
  const index = articleIndex(articles);
  const byId = new Map(articles.map((a) => [a.id, a]));
  const leaves = articles.filter((a) => !a.has_children && a.is_active)
    .sort((a, b) => index.label.get(a.id).localeCompare(index.label.get(b.id), "ru", { numeric: true }));
  const methodOf = (id) => PLAN_METHODS[byId.get(id)?.effective_plan_method] ?? "";

  function edit(norm, kind) {
    const k = norm?.kind ?? kind;
    const bases = [
      ...form.filter((r) => r.code).map((r) => [`row:${r.code}`, `${r.code} ${r.name} (строка формы)`]),
      ...articles.filter((a) => a.is_active).map((a) => [`art:${a.id}`, index.label.get(a.id)])
        .sort((a, b) => a[1].localeCompare(b[1], "ru", { numeric: true })),
    ];
    const today = new Date();
    const fields = [
      { name: "article_id", label: "Статья", type: "select", required: true,
        options: [["", "— выберите —"], ...leaves.map((a) => [String(a.id), `${index.label.get(a.id)} · ${methodOf(a.id)}`])] },
      { name: "cfo_id", label: "ЦФО", type: "select",
        options: [["", "— все ЦФО —"], ...cfos.filter((c) => c.is_active).map((c) => [String(c.id), c.name])] },
      { name: "valid_from", label: "Действует с месяца", type: "month", required: true },
    ];
    if (k === "calc") {
      fields.push({ name: "base", label: "Базовый показатель", type: "select", required: true, options: [["", "— выберите —"], ...bases] },
        { name: "percent", label: "Процент от базового показателя", required: true, placeholder: "например, 30,2" });
    } else {
      fields.push({ name: "amount", label: "Сумма в месяц, ₽", required: true, placeholder: "0,00" });
    }
    fields.push({ name: "comment", label: "Комментарий", type: "textarea" });
    const base = norm?.base_article_id ? `art:${norm.base_article_id}` : norm?.base_form_code ? `row:${norm.base_form_code}` : "";
    openForm({
      title: `${norm ? "Норматив" : "Новый норматив"}: ${KINDS[k].toLowerCase()}`,
      fields,
      values: {
        article_id: norm ? String(norm.article_id) : "", cfo_id: norm?.cfo_id ? String(norm.cfo_id) : "",
        valid_from: (norm?.valid_from ?? `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-01`).slice(0, 7),
        base, percent: norm?.percent ? String(Number(norm.percent)).replace(".", ",") : "",
        amount: norm?.amount ? money(norm.amount) : "", comment: norm?.comment ?? "",
      },
      extra: el("p", { class: "muted small" }, k === "calc"
        ? "План статьи = процент × итог базового показателя в плане этого же месяца."
        : k === "norm"
          ? "Сумма прибавляется к окладам сотрудников, у которых эта статья — статья затрат. Метод статьи должен быть «Нормативный»."
          : "Сумма прибавляется к регламентным операциям по этой статье. ЦФО не выбран — сумма на всю организацию."),
      submitText: "Сохранить",
      onSubmit: async (v) => {
        const body = { article_id: Number(v.article_id), kind: k, cfo_id: v.cfo_id ? Number(v.cfo_id) : null,
          valid_from: `${v.valid_from}-01`, comment: v.comment ?? "" };
        if (k === "calc") {
          body.base_article_id = v.base.startsWith("art:") ? Number(v.base.slice(4)) : null;
          body.base_form_code = v.base.startsWith("row:") ? v.base.slice(4) : null;
          body.percent = parseNumber(v.percent);
        } else {
          body.amount = parseNumber(v.amount);
        }
        if (norm) await api.put(`/api/plan-norms/${norm.id}`, body);
        else await api.post(`/api/organizations/${org.id}/plan-norms`, body);
        toast("Норматив сохранён");
        await renderNorms(box, org);
      },
    });
  }

  async function remove(norm) {
    if (!confirm(`Удалить норматив «${norm.article_name}» с ${monthTitle(norm.valid_from)}?`)) return;
    try {
      await api.delete(`/api/plan-norms/${norm.id}`);
      await renderNorms(box, org);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  const head = ["Статья", "Метод статьи", "Базовый показатель", "Процент", "Сумма в месяц", "ЦФО", "Действует с",
    "Кто изменил", ""];
  const section = (kind, title) => {
    const rows = norms.filter((n) => n.kind === kind);
    return el("section", { class: "norms-section" },
      el("div", { class: "page-head" }, el("h2", {}, title),
        canEdit ? el("button", { type: "button", class: "secondary", onclick: () => edit(null, kind) }, "+ Норматив") : ""),
      rows.length ? el("table", { class: "recon norms" },
        el("thead", {}, el("tr", {}, ...head.map((t) => el("th", {}, t)))),
        el("tbody", {}, ...rows.map((n) => el("tr", {},
          el("td", {}, n.article_name),
          el("td", {}, methodOf(n.article_id),
            byId.get(n.article_id)?.effective_plan_method !== n.kind
              ? el("div", { class: "over-budget small" }, `⚠ норматив не используется: метод статьи не «${KINDS[n.kind]}»`) : ""),
          el("td", {}, n.base_title),
          el("td", { class: "num" }, n.percent !== null ? `${String(Number(n.percent)).replace(".", ",")} %` : ""),
          el("td", { class: "num" }, n.amount !== null ? money(n.amount) : ""),
          el("td", {}, n.cfo_name || "все"),
          el("td", {}, monthTitle(n.valid_from)),
          el("td", { class: "muted" }, `${n.updated_by_name}, ${new Date(n.updated_at).toLocaleDateString("ru-RU")}`),
          el("td", {}, canEdit ? el("span", { class: "quick-actions" },
            el("button", { type: "button", class: "link", onclick: () => edit(n) }, "Изменить"),
            el("button", { type: "button", class: "link danger", onclick: () => remove(n) }, "Удалить")) : "")))))
        : el("p", { class: "muted" }, "Нормативов пока нет."));
  };

  box.replaceChildren(
    instruction("Нормативы задаёт финансовый директор. «Расчётный»: план статьи = процент от базового показателя "
      + "(статьи или строки управленческой формы) в плане того же месяца. «Фактический»: регламентные операции по статье "
      + "плюс сумма в месяц отсюда. «Нормативный»: оклады сотрудников со статьёй затрат плюс сумма в месяц отсюда. Норматив действует с выбранного месяца до следующего норматива этой статьи и ЦФО. "
      + "Метод статьи выбирается в справочнике «Статьи ДДС и ОПиУ»."),
    section("calc", "Расчётный: процент от базового показателя"),
    section("fact", "Фактический: сумма вручную"),
    section("norm", "Нормативный: сумма в месяц (к окладам)"),
  );
}

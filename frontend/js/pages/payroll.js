// «Зарплата»: начисления за месяц по юрлицу — оклад по дням, премии, НДФЛ, взносы, проведение в журнал.
import { api } from "../api.js";
import { money, parseNumber } from "../format.js";
import { openOperation } from "../opform.js";
import { el, makeTable, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];
const TARIFFS = { sme: "МСП", general: "общий" };
const EDITABLE = ["work_days", "salary", "bonus", "vacation", "sick", "other_accrual", "other_deduction"];

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}
const SHEET_KINDS = { advance: "Аванс", salary: "Зарплата" };
const SHEET_STATUS = { draft: "Черновик", sent: "Отправлена в банк", paid: "Оплачена" };
const isoToRu = (iso) => (iso ? iso.split("-").reverse().join(".") : "");
const moneyOrEmpty = (cell) => (Number(cell.getValue()) ? money(cell.getValue()) : "");

export async function renderPayroll(view) {
  const orgs = (await api.get("/api/organizations")).filter((o) => o.permissions.includes("post"));
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Зарплата"),
      el("p", { class: "muted" }, "Раздел доступен финансовому директору и администратору."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  let year = new Date().getFullYear();
  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const yearSelect = el("select", { class: "inline-select", "aria-label": "Год" },
    ...[year - 1, year, year + 1].map((y) => el("option", { value: y, selected: y === year }, y)));
  const listBox = el("div");
  const sheetsBox = el("div");

  async function showList() {
    view.replaceChildren(
      el("div", { class: "page-head" }, el("h1", {}, "Зарплата"), el("label", { class: "inline" }, "Организация ", orgSelect)),
      el("p", { class: "muted" }, "Начисление — по юрлицу за месяц. «Заполнить» берёт сотрудников юрлица и их оклады; "
        + "НДФЛ, взносы и «к выплате» считаются автоматически. Проведённое начисление попадает в журнал одной операцией."),
      el("div", { class: "filters" }, el("label", { class: "inline" }, "Год ", yearSelect),
        el("button", { type: "button", onclick: () => addRun() }, "Новое начисление")),
      listBox,
      el("div", { class: "page-head section" }, el("h2", {}, "Ведомости на выплату"),
        el("button", { type: "button", onclick: () => addSheet() }, "Новая ведомость")),
      el("p", { class: "muted" }, "Аванс — доля оклада; зарплата — «к выплате» из проведённого начисления минус аванс. "
        + "Отправленная в банк ведомость подтверждается выпиской: платёж на её сумму с назначением о зарплате "
        + "сам отметит её оплаченной."),
      sheetsBox,
    );
    const sheets = await api.get(`/api/organizations/${org.id}/payout-sheets?year=${year}`);
    sheetsBox.replaceChildren(sheets.length
      ? el("table", { class: "recon versions" },
        el("thead", {}, el("tr", {}, ...["№", "Вид", "Месяц", "Юрлицо", "Дата выплаты", "Сумма", "Статус", ""].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...sheets.map((sh) => el("tr", {},
          el("td", {}, String(sh.number)), el("td", {}, SHEET_KINDS[sh.kind]),
          el("td", {}, `${MONTHS[sh.month - 1]} ${sh.year}`), el("td", {}, sh.legal_entity_name),
          el("td", {}, isoToRu(sh.pay_date)), el("td", {}, money(sh.total)),
          el("td", {}, el("span", { class: `status sheet-${sh.status}` }, SHEET_STATUS[sh.status])),
          el("td", {}, el("button", { type: "button", class: "link", onclick: () => showSheet(sh.id) }, "Открыть"))))))
      : el("p", { class: "muted" }, `За ${year} год ведомостей пока нет.`));
    const runs = await api.get(`/api/organizations/${org.id}/payroll-runs?year=${year}`);
    listBox.replaceChildren(runs.length
      ? el("table", { class: "recon versions" },
        el("thead", {}, el("tr", {}, ...["Месяц", "Юрлицо", "Статус", "Начислено", "НДФЛ", "Взносы", "К выплате", ""].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...runs.map((r) => el("tr", {},
          el("td", {}, `${MONTHS[r.month - 1]} ${r.year}`), el("td", {}, r.legal_entity_name),
          el("td", {}, el("span", { class: `status status-${r.status === "posted" ? "posted" : "draft"}` }, r.status === "posted" ? "Проведено" : "Черновик")),
          el("td", {}, money(r.totals.accrued)), el("td", {}, money(r.totals.ndfl)), el("td", {}, money(r.totals.insurance)),
          el("td", {}, money(r.totals.net)),
          el("td", {}, el("button", { type: "button", class: "link", onclick: () => showRun(r.id) }, "Открыть"))))))
      : el("p", { class: "muted" }, `За ${year} год начислений пока нет.`));
  }

  async function addRun() {
    const entities = await api.get(`/api/organizations/${org.id}/legal-entities`);
    const now = new Date();
    openForm({
      title: "Новое начисление зарплаты",
      fields: [
        { name: "legal_entity_id", label: "Юрлицо", type: "select", options: entities.filter((e) => e.is_active).map((e) => [String(e.id), e.short_name]) },
        { name: "month", label: "Месяц", type: "select", options: MONTHS.map((m, i) => [String(i + 1), m]) },
        { name: "year", label: "Год", type: "number", required: true },
      ],
      values: { month: String(now.getMonth() + 1), year },
      submitText: "Создать и заполнить",
      onSubmit: async (v) => {
        const run = await api.post(`/api/organizations/${org.id}/payroll-runs`,
          { legal_entity_id: Number(v.legal_entity_id), month: Number(v.month), year: Number(v.year) });
        await showRun(run.id);
      },
    });
  }

  async function showRun(runId) {
    let run = await api.get(`/api/payroll-runs/${runId}`);
    const head = el("div");
    const tableBox = el("div");
    const fileInput = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": "Файл начисления" });
    view.replaceChildren(head, tableBox, fileInput);

    const editor = () => (run.status === "draft" ? { editor: "input", editorParams: { selectContents: true }, mutatorEdit: (value, _d, _t, _p, cell) => {
      const raw = parseNumber(value);
      if (raw === null) return "0";
      if (/^\d+(\.\d{1,2})?$/.test(raw)) return raw;
      toast("Введите число, не больше 2 знаков после запятой", "error");
      return cell ? cell.getValue() : "0";
    } } : {});
    const col = (title, field, extra = {}) => ({ title, field, hozAlign: "right", minWidth: 105, headerSort: false,
      formatter: field === "work_days" ? (c) => String(c.getValue()).replace(".", ",").replace(/,00$/, "") : moneyOrEmpty,
      bottomCalc: field === "work_days" || field === "salary" ? undefined : () => run.totals[field],
      bottomCalcFormatter: moneyOrEmpty, ...(EDITABLE.includes(field) ? editor() : {}), ...extra });
    const table = makeTable(tableBox, {
      index: "id",
      columnDefaults: { headerWordWrap: true },
      columns: [
        { title: "Сотрудник", field: "employee_name", minWidth: 200, widthGrow: 2, frozen: true, headerSort: false },
        col("Оклад", "salary"), col("Дней", "work_days", { minWidth: 70 }), col("По окладу", "salary_amount"),
        col("Премия", "bonus"), col("Отпускные", "vacation"), col("Больничные", "sick"), col("Прочие начисл.", "other_accrual"),
        col("Начислено", "accrued", { cssClass: "strong" }), col("НДФЛ", "ndfl"), col("Прочие удерж.", "other_deduction"),
        col("К выплате", "net", { cssClass: "strong" }), col("Взносы", "insurance"),
        ...(run.status === "draft" ? [{ title: "", field: "_delete", width: 40, hozAlign: "center", headerSort: false,
          formatter: () => "✕", cellClick: async (_e, c) => {
            if (!confirm(`Убрать ${c.getRow().getData().employee_name} из начисления?`)) return;
            await update(api.delete(`/api/payroll-lines/${c.getRow().getData().id}`));
          } }] : []),
      ],
    });
    table.on("cellEdited", async (cell) => {
      await update(api.patch(`/api/payroll-lines/${cell.getRow().getData().id}`, { [cell.getField()]: cell.getValue() }),
        () => cell.restoreOldValue());
    });

    async function update(promise, onError) {
      try {
        const result = await promise;
        run = result.run ?? result;
        renderHead();
        await table.setData(run.lines);
        table.recalc();
      } catch (err) {
        toast(err.message, "error");
        onError?.();
      }
    }

    function renderHead() {
      const draft = run.status === "draft";
      const normInput = el("input", { type: "number", min: 1, max: 31, value: run.norm_days, disabled: !draft,
        "aria-label": "Норма дней", style: "width: 80px; display: inline-block; margin: 0 0 0 6px" });
      normInput.addEventListener("change", () => update(api.patch(`/api/payroll-runs/${run.id}`, { norm_days: Number(normInput.value) })));
      const button = (text, onclick, cls = "secondary") => el("button", { type: "button", class: cls, onclick }, text);
      head.replaceChildren(
        el("div", { class: "page-head" },
          el("h1", {}, `Зарплата: ${MONTHS[run.month - 1].toLowerCase()} ${run.year}, ${run.legal_entity_name}`),
          button("← К списку", () => showList())),
        el("p", { class: "muted" },
          `Статус: ${draft ? "черновик" : "проведено"}. Тариф взносов: ${TARIFFS[run.insurance_tariff]}. `,
          el("label", {}, "Рабочих дней в месяце:", normInput),
          draft ? " Щёлкните по дням или сумме, чтобы изменить; Enter — сохранить." : ""),
        el("div", { class: "quick-actions" },
          draft ? button("Заполнить", () => update(api.post(`/api/payroll-runs/${run.id}/fill`))) : null,
          button("Скачать в Excel", () => api.download(`/api/payroll-runs/${run.id}/lines.xlsx`, `payroll_${run.year}_${run.month}.xlsx`)),
          draft ? button("Загрузить из Excel", () => { fileInput.value = ""; fileInput.click(); }) : null,
          draft ? button("Провести", () => update(api.post(`/api/payroll-runs/${run.id}/post`)), "")
            : button("Отменить проведение", () => {
              if (confirm("Отменить проведение? Операция журнала будет удалена.")) update(api.post(`/api/payroll-runs/${run.id}/unpost`));
            }),
          run.operation_id ? button("Операция в журнале", async () => {
            const [op, ref] = await Promise.all([api.get(`/api/operations/${run.operation_id}`), loadReferences(org.id)]);
            openOperation({ ref, org, op, perms: org.permissions, onChanged: () => showRun(run.id) });
          }) : null,
          draft ? button("Удалить", async () => {
            if (!confirm("Удалить начисление?")) return;
            try { await api.delete(`/api/payroll-runs/${run.id}`); await showList(); } catch (err) { toast(err.message, "error"); }
          }, "secondary danger") : null),
      );
    }

    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      if (!file) return;
      fileInput.value = "";
      try {
        const r = await api.upload(`/api/payroll-runs/${run.id}/lines`, file);
        if (r.errors.length) toast(`Не загружено строк: ${r.errors.length}. ${r.errors.map((e) => `Строка ${e.row}: ${e.error}`).join("; ")}`, "error");
        else toast(`Загружено строк: ${r.changed}`);
        await update(Promise.resolve(r.run));
      } catch (err) {
        toast(err.message, "error");
      }
    });

    renderHead();
    await table.ready;
    await table.setData(run.lines);
    table.recalc();
  }

  async function addSheet() {
    const [entities, banks] = await Promise.all([
      api.get(`/api/organizations/${org.id}/legal-entities`), api.get(`/api/organizations/${org.id}/bank-accounts`)]);
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    openForm({
      title: "Новая ведомость на выплату",
      fields: [
        { name: "kind", label: "Вид", type: "select", options: Object.entries(SHEET_KINDS) },
        { name: "legal_entity_id", label: "Юрлицо", type: "select", options: entities.filter((e) => e.is_active).map((e) => [String(e.id), e.short_name]) },
        { name: "bank_account_id", label: "Расчётный счёт, с которого платим", type: "select",
          options: banks.filter((b) => b.is_active).map((b) => [String(b.id), `${b.name} ··${b.number.slice(-4)}`]),
          hint: "Счёт должен принадлежать выбранному юрлицу" },
        { name: "month", label: "За месяц", type: "select", options: MONTHS.map((m, i) => [String(i + 1), m]) },
        { name: "year", label: "Год", type: "number", required: true },
        { name: "pay_date", label: "Дата выплаты", type: "date", required: true },
        { name: "advance_percent", label: "Аванс, % оклада", type: "number", hint: "Только для аванса" },
      ],
      values: { kind: "advance", month: String(now.getMonth() + 1), year,
        pay_date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-15`, advance_percent: "50" },
      submitText: "Создать и заполнить",
      onSubmit: async (v) => {
        const sh = await api.post(`/api/organizations/${org.id}/payout-sheets`, {
          kind: v.kind, legal_entity_id: Number(v.legal_entity_id), bank_account_id: Number(v.bank_account_id),
          month: Number(v.month), year: Number(v.year), pay_date: v.pay_date, advance_percent: v.advance_percent || "50" });
        await showSheet(sh.id);
      },
    });
  }

  async function showSheet(sheetId) {
    let sh = await api.get(`/api/payout-sheets/${sheetId}`);
    const head = el("div");
    const tableBox = el("div");
    view.replaceChildren(head, tableBox);
    const table = makeTable(tableBox, {
      index: "id",
      columns: [
        { title: "Таб. №", field: "personnel_number", width: 100, headerSort: false },
        { title: "Сотрудник", field: "employee_name", widthGrow: 3, headerSort: false },
        { title: "Сумма", field: "amount", width: 160, hozAlign: "right", headerSort: false, formatter: moneyOrEmpty,
          bottomCalc: () => sh.total, bottomCalcFormatter: moneyOrEmpty,
          ...(sh.status === "draft" ? { editor: "input", editorParams: { selectContents: true }, mutatorEdit: (value, _d, _t, _p, cell) => {
            const raw = parseNumber(value);
            if (raw && /^\d+(\.\d{1,2})?$/.test(raw) && Number(raw) > 0) return raw;
            toast("Введите сумму больше нуля", "error");
            return cell ? cell.getValue() : "0";
          } } : {}) },
        ...(sh.status === "draft" ? [{ title: "", field: "_delete", width: 40, hozAlign: "center", headerSort: false,
          formatter: () => "✕", cellClick: (_e, c) => act(api.delete(`/api/payout-lines/${c.getRow().getData().id}`)) }] : []),
      ],
    });
    table.on("cellEdited", (cell) => act(api.patch(`/api/payout-lines/${cell.getRow().getData().id}`, { amount: cell.getValue() }),
      () => cell.restoreOldValue()));

    async function act(promise, onError) {
      try {
        sh = await promise;
        if (!sh) return showList();
        return showSheet(sh.id);
      } catch (err) {
        toast(err.message, "error");
        onError?.();
      }
    }

    const button = (text, onclick, cls = "secondary") => el("button", { type: "button", class: cls, onclick }, text);
    head.replaceChildren(
      el("div", { class: "page-head" },
        el("h1", {}, `Ведомость № ${sh.number}: ${SHEET_KINDS[sh.kind].toLowerCase()} за ${MONTHS[sh.month - 1].toLowerCase()} ${sh.year}`),
        button("← К списку", () => showList())),
      el("p", { class: "muted" },
        `${sh.legal_entity_name}, счёт ${sh.bank_account_name}, дата выплаты ${isoToRu(sh.pay_date)}. Статус: ${SHEET_STATUS[sh.status].toLowerCase()}`,
        sh.status === "paid" ? ` ${isoToRu(sh.paid_on)}${sh.confirmed_by_statement ? " (подтверждено выпиской)" : " (вручную)"}.` : "."),
      el("div", { class: "quick-actions" },
        button("Скачать реестр (Excel)", () => api.download(`/api/payout-sheets/${sh.id}/registry.xlsx`, `registry_${sh.number}.xlsx`)),
        sh.status === "draft" ? button("Отправить в банк", () => act(api.post(`/api/payout-sheets/${sh.id}/send`)), "") : null,
        sh.status === "sent" ? button("Вернуть в черновик", () => act(api.post(`/api/payout-sheets/${sh.id}/unsend`))) : null,
        sh.status === "sent" ? button("Подтвердить оплату вручную", () => openForm({
          title: "Оплата ведомости", fields: [{ name: "paid_on", label: "Дата оплаты", type: "date", required: true }],
          values: { paid_on: sh.pay_date }, submitText: "Подтвердить",
          onSubmit: async (v) => { await act(api.post(`/api/payout-sheets/${sh.id}/pay`, { paid_on: v.paid_on })); },
        })) : null,
        sh.status === "paid" ? button("Отменить оплату", () => {
          if (confirm("Отменить оплату? Операция оплаты будет удалена из журнала.")) act(api.post(`/api/payout-sheets/${sh.id}/unpay`));
        }) : null,
        sh.operation_id ? button("Операция в журнале", async () => {
          const [op, ref] = await Promise.all([api.get(`/api/operations/${sh.operation_id}`), loadReferences(org.id)]);
          openOperation({ ref, org, op, perms: org.permissions, onChanged: () => showSheet(sh.id) });
        }) : null,
        sh.status === "draft" ? button("Удалить", () => {
          if (confirm("Удалить ведомость?")) act(api.delete(`/api/payout-sheets/${sh.id}`));
        }, "secondary danger") : null),
    );
    await table.ready;
    await table.setData(sh.lines);
    table.recalc();
  }

  orgSelect.addEventListener("change", () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    showList();
  });
  yearSelect.addEventListener("change", () => { year = Number(yearSelect.value); showList(); });
  await showList();
}

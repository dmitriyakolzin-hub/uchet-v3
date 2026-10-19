// «Начальные остатки»: дата остатков, контроль счёта 00, операции ввода остатков.
import { api } from "../api.js";
import { date, money } from "../format.js";
import { openOperation } from "../opform.js";
import { el, makeTable, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const STATUS_TITLES = { draft: "Черновик", posted: "Проведена", cancelled: "Аннулирована" };

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

export async function renderOpening(view) {
  let orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Начальные остатки"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  if (org.restricted) {
    view.replaceChildren(el("h1", {}, "Начальные остатки"),
      el("p", { class: "muted" }, "Раздел недоступен руководителю ЦФО: начальные остатки вводит финансовый директор."));
    return;
  }
  let ref = await loadReferences(org.id);

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  // до загрузки данных поле недоступно — иначе загрузка затрёт введённую дату
  const dateInput = el("input", { type: "date", "aria-label": "Дата начальных остатков", disabled: true });
  const saveDate = el("button", { type: "button", class: "secondary", hidden: true }, "Сохранить дату");
  const dateRow = el("div", { class: "filters" }, el("label", { class: "inline" }, "Дата начальных остатков", dateInput), saveDate);
  const status = el("div", { class: "check-box" });
  const addButton = el("button", { type: "button" }, "Добавить остатки");
  const tableBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Начальные остатки"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("p", { class: "muted" }, "Остатки вводятся проводками против счёта 00: активы — Дт счёта, Кт 00; обязательства и капитал — Дт 00, Кт счёта. Когда всё введено верно, сальдо счёта 00 равно нулю."),
    dateRow,
    status,
    el("div", { class: "page-head" }, el("h2", {}, "Операции ввода остатков"), addButton),
    tableBox,
  );

  const codes = () => new Map(ref.accounts.map((a) => [a.id, a.code]));
  let codeOf = codes();
  const table = makeTable(tableBox, {
    columns: [
      { title: "№", field: "number", width: 75 },
      { title: "Статус", field: "status", width: 130, formatter: (c) => `<span class="status status-${c.getValue()}">${STATUS_TITLES[c.getValue()]}</span>` },
      { title: "Проводки", field: "postings", widthGrow: 2, formatter: (c) => c.getValue().map((p) => `${codeOf.get(p.debit_account_id) ?? "?"} → ${codeOf.get(p.credit_account_id) ?? "?"}`).join(", ") },
      { title: "Описание", field: "description", widthGrow: 3, tooltip: true },
      { title: "Сумма, ₽", field: "total_rub", width: 140, hozAlign: "right", formatter: (c) => money(c.getValue()) },
    ],
  });
  table.on("rowClick", (_e, row) => openOperation({ ref, org, op: row.getData(), perms: org.permissions, onChanged: load }));

  async function load() {
    const [summary, list] = await Promise.all([
      api.get(`/api/organizations/${org.id}/opening-balances`),
      api.get(`/api/organizations/${org.id}/operations?opening=true&limit=1000`),
    ]);
    const canEdit = org.permissions.includes("edit_reference");
    dateInput.value = summary.opening_balance_date ?? "";
    const locked = summary.posted + summary.drafts > 0;
    dateInput.disabled = !canEdit || locked;
    saveDate.hidden = !canEdit || locked;
    addButton.hidden = !org.permissions.includes("draft") || !summary.opening_balance_date;

    const balance = summary.account_00_balance;
    if (!summary.opening_balance_date) {
      status.className = "check-box";
      status.textContent = "Задайте дату начальных остатков — например, последний день месяца перед началом учёта в системе.";
    } else if (summary.posted === 0) {
      status.className = "check-box";
      status.textContent = `Остатки на ${date(summary.opening_balance_date)} ещё не введены.`;
    } else if (Number(balance) === 0) {
      status.className = "check-box ok";
      status.textContent = `Остатки на ${date(summary.opening_balance_date)} сходятся: сальдо счёта 00 равно нулю. Проведено операций: ${summary.posted}.`;
    } else {
      status.className = "check-box bad";
      // активы вводятся в кредит 00, пассивы — в дебет: отрицательное сальдо — активов введено больше
      const more = balance.startsWith("-") ? "активов введено больше, чем обязательств и капитала" : "обязательств и капитала введено больше, чем активов";
      status.textContent = `Расхождение ${money(balance.replace("-", ""))} ₽: ${more}. Проверьте введённые остатки.`;
    }
    if (summary.drafts) status.textContent += ` Черновиков: ${summary.drafts} — они не учитываются, пока не проведены.`;
    if (locked && canEdit) status.textContent += " Дату нельзя изменить, пока есть операции ввода остатков.";
    await table.setData(list.items);
  }

  saveDate.addEventListener("click", async () => {
    try {
      org = await api.patch(`/api/organizations/${org.id}`, { opening_balance_date: dateInput.value || null });
      orgs = orgs.map((o) => (o.id === org.id ? org : o));
      toast("Дата начальных остатков сохранена");
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  });
  addButton.addEventListener("click", () => openOperation({ ref, org, op: null, perms: org.permissions, onChanged: load, opening: true }));
  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    ref = await loadReferences(org.id);
    codeOf = codes();
    await load();
  });

  await table.ready;
  await load();
}

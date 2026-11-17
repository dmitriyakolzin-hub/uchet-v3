// «Имущество»: реестр единиц имущества, карточка с историей и файлами, групповое изменение, ввод имеющегося
// имущества вручную и из Excel (docs/TZ.md, раздел 28, «Детали 19.1»).
import { api } from "../api.js";
import { date as ruDate, money, parseNumber } from "../format.js";
import { el, instruction, makeTable, openForm, toast } from "../ui.js";
import { assetRequestsTab } from "./assetrequests.js";
import { assetTransfersTab, transferForm } from "./assettransfers.js";
import { assetDisposalsTab, disposalForm } from "./assetdisposals.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const STATUSES = [["stock", "На складе"], ["in_use", "В эксплуатации"], ["repair", "В ремонте"], ["disposed", "Выбыло"]];
const OWNERSHIP = [["own", "Собственное"], ["leasing", "Лизинг"], ["rent", "Аренда"]];
const ACTIONS = { created: "завёл(а)", edited: "изменил(а)", bulk_edited: "изменил(а) группой", imported: "загрузил(а)",
  file: "приложил(а) файл", file_removed: "убрал(а) файл", received: "принял(а)", accounted: "поставил(а) на учёт",
  unaccounted: "снял(а) с учёта", transfer_created: "отправил(а) в перемещение", transferred: "переместил(а)",
  transfer_rejected: "— получатель отказался", transfer_cancelled: "отменил(а) перемещение", work: "внёс(ла) работу",
  work_finished: "завершил(а) работу", work_removed: "удалил(а) работу", disposal_created: "предложил(а) выбытие",
  disposed: "— выбыла", disposal_rejected: "— выбытие отклонено", disposal_cancelled: "отменил(а) выбытие",
  disposal_undone: "— выбытие отменено" };

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}
const escape = (text) => String(text ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);

export async function renderAssets(view) {
  const orgs = (await api.get("/api/organizations")).filter((o) => o.permissions.includes("assets"));
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Имущество"), el("p", { class: "muted" }, "У вас нет доступа к имуществу ни в одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const filtersBox = el("div", { class: "filters" });
  const actionsBox = el("div", { class: "filters" });
  const bulkBar = el("div", { class: "bulk-bar", hidden: true });
  const tableBox = el("div");
  const summary = el("p", { class: "muted" });
  // вкладки: реестр единиц и заявки на имущество (шаг 19.2)
  const TAB_KEY = "uchet3_assets_tab";
  let tab = ["requests", "transfers", "disposals"].includes(recall(TAB_KEY)) ? recall(TAB_KEY) : "registry";
  const tabBar = el("div", { class: "tabs", role: "tablist" });
  const registryBox = el("div");
  const requestsBox = el("div");
  const transfersBox = el("div");
  const disposalsBox = el("div");
  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Имущество"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    tabBar, registryBox, requestsBox, transfersBox, disposalsBox,
  );
  registryBox.append(
    instruction("Каждая единица имущества — своя карточка с инвентарным номером, местом (ЦФО, склад), МОЛ и историей. "
      + "Отслеживается: основные средства, имущество в лизинге и аренде, а также имущество от 500 ₽ со сроком использования "
      + "больше 6 месяцев (инвентарь, инструмент, мелкое оборудование). Имеющееся имущество заведите кнопкой «+ Единица» "
      + "(можно сразу несколько одинаковых) или загрузите из Excel по шаблону. Карточки основных средств "
      + "(«Основные средства») появляются здесь сами. МОЛ видит только своё имущество, руководитель ЦФО — своей ветки. "
      + "Заводить и править: финансовый директор, бухгалтер, оператор АХО."),
    filtersBox, actionsBox, bulkBar, summary, tableBox,
  );

  let ref = null;
  const filters = { search: "", legal_entity_id: "", cfo_id: "", employee_id: "", category_id: "", status: "", ownership: "", mine: false };
  const canEdit = () => org.permissions.includes("assets_edit");

  const table = makeTable(tableBox, {
    index: "id",
    selectableRows: "highlight",
    columns: [
      { formatter: "rowSelection", titleFormatter: "rowSelection", hozAlign: "center", headerSort: false, width: 44 },
      { title: "Инв. №", field: "number", width: 120 },
      { title: "Наименование", field: "name", widthGrow: 3, formatter: (c) => escape(c.getValue())
        + (c.getRow().getData().serial_number ? `<div class="muted small">с/н ${escape(c.getRow().getData().serial_number)}</div>` : "") },
      { title: "Категория", field: "category_name", widthGrow: 1 },
      { title: "Место", field: "cfo_name", widthGrow: 2, formatter: (c) => escape(c.getValue())
        + (c.getRow().getData().warehouse_name ? `<div class="muted small">${escape(c.getRow().getData().warehouse_name)}</div>` : "") },
      { title: "МОЛ", field: "employee_name", widthGrow: 2 },
      { title: "Статус", field: "status_title", width: 140,
        formatter: (c) => `<span class="badge asset-${c.getRow().getData().status}">${escape(c.getValue())}</span>`
          + (c.getRow().getData().in_transfer ? '<div class="muted small">перемещается</div>' : "")
          + (c.getRow().getData().in_disposal ? '<div class="muted small">на выбытии</div>' : "") },
      { title: "Владение", field: "ownership_title", width: 120 },
      { title: "Учёт", field: "accounting_title", width: 150, formatter: (c) => escape(c.getValue() || "—") },
      { title: "Цена, ₽", field: "purchase_price", width: 120, hozAlign: "right", formatter: (c) => (c.getValue() ? money(c.getValue()) : "") },
    ],
  });
  table.on("cellClick", (_e, cell) => {
    if (cell.getColumn().getDefinition().formatter === "rowSelection") return;
    openCard(cell.getRow().getData());
  });
  table.on("rowSelectionChanged", () => renderBulk());

  const options = (items, label, empty = "— не указано —") => [["", empty], ...items.map((i) => [String(i.id), label(i)])];

  function renderFilters() {
    const select = (key, label, items) => {
      const node = el("select", { "aria-label": label }, ...items.map(([v, t]) => el("option", { value: v, selected: String(filters[key]) === v }, t)));
      node.addEventListener("change", () => { filters[key] = node.value; load(); });
      return node;
    };
    const search = el("input", { type: "search", placeholder: "Название, инв. или серийный №", value: filters.search, "aria-label": "Поиск имущества" });
    search.addEventListener("change", () => { filters.search = search.value.trim(); load(); });
    const mine = el("input", { type: "checkbox", checked: filters.mine, "aria-label": "Моё имущество" });
    mine.addEventListener("change", () => { filters.mine = mine.checked; load(); });
    filtersBox.replaceChildren(
      search,
      select("legal_entity_id", "Юрлицо", options(ref.entities, (e) => e.short_name, "Все юрлица")),
      select("cfo_id", "Место", options(ref.cfos, (c) => ref.cfoLabel.get(c.id), "Все места")),
      select("employee_id", "МОЛ", options(ref.employees, (e) => e.full_name, "Все МОЛ")),
      select("category_id", "Категория", options(ref.categories, (c) => c.name, "Все категории")),
      select("status", "Статус", [["", "Все статусы"], ...STATUSES]),
      select("ownership", "Владение", [["", "Любое владение"], ...OWNERSHIP]),
      el("label", { class: "check" }, mine, " Моё имущество"),
    );
    const fileInput = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": "Файл Excel с имуществом" });
    fileInput.addEventListener("change", () => importFile(fileInput));
    actionsBox.replaceChildren(
      ...(canEdit() ? [
        el("button", { type: "button", onclick: () => editItem(null) }, "+ Единица"),
        el("button", { type: "button", class: "secondary", onclick: () => api.download(
          `/api/organizations/${org.id}/import/templates/asset-items.xlsx`, "шаблон_имущество.xlsx").catch((err) => toast(err.message, "error")) },
        "Шаблон Excel"),
        el("button", { type: "button", class: "secondary", onclick: () => { fileInput.value = ""; fileInput.click(); } }, "Загрузить из Excel"),
        fileInput,
      ] : []),
      el("button", { type: "button", class: "secondary push-right", onclick: () => api.download(
        `/api/organizations/${org.id}/asset-items.xlsx?${params()}`, "имущество.xlsx").catch((err) => toast(err.message, "error")) },
      "Скачать в Excel"),
    );
  }

  function params() {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) if (v) p.set(k, v === true ? "true" : v);
    return p.toString();
  }

  async function loadRef() {
    const o = `/api/organizations/${org.id}`;
    const [entities, cfos, warehouses, employees, categories] = await Promise.all([
      api.get(`${o}/legal-entities`), api.get(`${o}/cfos`), api.get(`${o}/warehouses`), api.get(`${o}/employees`),
      api.get(`${o}/asset-categories`),
    ]);
    const names = new Map(cfos.map((c) => [c.id, c.name]));
    const cfoLabel = new Map(cfos.map((c) => [c.id, String(c.path).split(".").map((id) => names.get(Number(id))).join(" / ")]));
    ref = { entities, cfos: cfos.filter((c) => c.is_active), cfoLabel, warehouses: warehouses.filter((w) => w.is_active),
      employees: employees.filter((e) => e.is_active), categories: categories.filter((c) => c.is_active) };
  }

  async function load() {
    let rows;
    try {
      rows = await api.get(`/api/organizations/${org.id}/asset-items?${params()}`);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const total = rows.reduce((s, r) => s + (r.purchase_price ? 1 : 0), 0);
    summary.textContent = `Единиц: ${rows.length}${total < rows.length ? ` (без цены: ${rows.length - total})` : ""}`;
    await table.setData(rows);
    renderBulk();
  }

  function itemFields(item, creating) {
    return [
      { name: "name", label: "Наименование", required: true, placeholder: "Холодильник Бирюса" },
      ...(creating ? [{ name: "count", label: "Количество одинаковых единиц", type: "number", hint: "Каждая получит свою карточку и номер" }] : []),
      { name: "number", label: "Инвентарный номер", hint: creating ? "Пусто — присвоится сам («ИН-000001»)" : "" },
      { name: "category_id", label: "Категория", type: "select", options: options(ref.categories, (c) => c.name) },
      { name: "ownership", label: "Владение", type: "select", options: OWNERSHIP },
      { name: "legal_entity_id", label: "Юрлицо", type: "select", options: options(ref.entities, (e) => e.short_name) },
      { name: "serial_number", label: "Серийный (заводской) номер" },
      { name: "cfo_id", label: "Место (ЦФО)", type: "select", options: options(ref.cfos, (c) => ref.cfoLabel.get(c.id)) },
      { name: "warehouse_id", label: "Склад", type: "select", options: options(ref.warehouses, (w) => w.name) },
      { name: "employee_id", label: "МОЛ", type: "select", options: options(ref.employees, (e) => e.full_name) },
      { name: "status", label: "Статус", type: "select", options: STATUSES.filter(([k]) => k !== "disposed") },
      { name: "purchase_price", label: "Цена приобретения, ₽", placeholder: "0,00" },
      { name: "acquired_on", label: "Дата приобретения", type: "date" },
      { name: "note", label: "Примечание", type: "textarea" },
    ];
  }

  const idOrNull = (v) => (v ? Number(v) : null);

  function editItem(item) {
    const creating = !item;
    openForm({
      title: creating ? "Новая единица имущества" : `${item.number} — ${item.name}`,
      fields: itemFields(item, creating),
      values: item ? { ...Object.fromEntries(Object.entries(item).map(([k, v]) => [k, v === null ? "" : String(v)])),
        purchase_price: item.purchase_price ? money(item.purchase_price) : "" }
        : { count: "1", ownership: "own", status: "stock" },
      submitText: creating ? "Создать" : "Сохранить",
      onSubmit: async (v) => {
        const body = {
          name: v.name, number: v.number ?? "", category_id: idOrNull(v.category_id), ownership: v.ownership,
          legal_entity_id: idOrNull(v.legal_entity_id), serial_number: v.serial_number ?? "", cfo_id: idOrNull(v.cfo_id),
          warehouse_id: idOrNull(v.warehouse_id), employee_id: idOrNull(v.employee_id), status: v.status,
          purchase_price: v.purchase_price ? parseNumber(v.purchase_price) : null, acquired_on: v.acquired_on || null,
          note: v.note ?? "",
        };
        if (creating) {
          const created = await api.post(`/api/organizations/${org.id}/asset-items`, { ...body, count: Number(v.count || 1) });
          toast(created.length > 1 ? `Заведено единиц: ${created.length} (${created[0].number} — ${created.at(-1).number})` : `Заведено: ${created[0].number}`);
        } else {
          await api.put(`/api/asset-items/${item.id}`, body);
          toast("Сохранено");
        }
        await load();
      },
    });
  }

  // работа по единице: модернизация (увеличивает стоимость), ремонт (пока идёт — «в ремонте»), ТО
  async function workForm(item, after) {
    let counterparties = [];
    try {
      counterparties = (await api.get(`/api/organizations/${org.id}/counterparties`)).filter((c) => c.is_active);
    } catch { /* без списка подрядчиков */ }
    openForm({
      title: `Работа: ${item.number} ${item.name}`,
      fields: [
        { name: "kind", label: "Вид", type: "select", options: [["repair", "Ремонт"], ["maintenance", "ТО"], ["modernization", "Модернизация"]] },
        { name: "started_on", label: "Начало", type: "date", required: true },
        { name: "finished_on", label: "Окончание", type: "date", hint: "Ремонт без даты окончания — единица «в ремонте», пока его не завершат" },
        { name: "counterparty_id", label: "Подрядчик", type: "select", options: options(counterparties, (c) => c.name, "— свои силы —") },
        { name: "employee_id", label: "Кто делал (сотрудник)", type: "select", options: options(ref.employees, (e) => e.full_name) },
        { name: "amount", label: "Стоимость, ₽", placeholder: "0,00", hint: "У модернизации обязательна — увеличивает стоимость единицы" },
        { name: "description", label: "Что сделано", type: "textarea" },
      ],
      values: { kind: "repair", started_on: new Date().toISOString().slice(0, 10) },
      submitText: "Сохранить",
      onSubmit: async (v) => {
        await api.post(`/api/asset-items/${item.id}/works`, {
          kind: v.kind, started_on: v.started_on, finished_on: v.finished_on || null, counterparty_id: idOrNull(v.counterparty_id),
          employee_id: idOrNull(v.employee_id), amount: v.amount ? parseNumber(v.amount) : null, description: v.description ?? "",
        });
        await after();
      },
    });
  }

  async function openCard(row) {
    let item;
    let events;
    let works;
    try {
      [item, events, works] = await Promise.all([api.get(`/api/asset-items/${row.id}`), api.get(`/api/asset-items/${row.id}/events`),
        api.get(`/api/asset-items/${row.id}/works`)]);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const field = (title, value) => (value ? el("tr", {}, el("th", {}, title), el("td", {}, value)) : "");
    const canService = org.permissions.includes("asset_service") && item.status !== "disposed";
    const reopen = async () => { close(); await load(); await openCard(item); };
    const worksBox = el("div", { class: "asset-works" },
      el("h3", {}, "Работы: модернизация, ремонт, ТО"),
      Number(works.modernization_total) ? el("p", {}, `Модернизация: ${money(works.modernization_total)} ₽`
        + (works.cost_with_modernization ? `; стоимость с модернизацией — ${money(works.cost_with_modernization)} ₽` : "")) : "",
      works.works.length ? el("ul", {}, ...works.works.map((w) => el("li", {},
        `${w.kind_title}: ${ruDate(w.started_on)}${w.finished_on ? ` — ${ruDate(w.finished_on)}` : " — идёт"}`
          + (w.amount ? `, ${money(w.amount)} ₽` : "") + (w.counterparty_name ? `, ${w.counterparty_name}` : "")
          + (w.employee_name ? `, ${w.employee_name}` : ""),
        w.description ? el("div", { class: "muted small" }, w.description) : "",
        canService && !w.finished_on ? el("button", { type: "button", class: "link", onclick: () => openForm({
          title: `Завершить: ${w.kind_title.toLowerCase()}`,
          fields: [{ name: "finished_on", label: "Дата окончания", type: "date", required: true },
            { name: "status", label: "Статус после ремонта", type: "select", options: [["in_use", "В эксплуатации"], ["stock", "На складе"]] }],
          values: { finished_on: new Date().toISOString().slice(0, 10) }, submitText: "Завершить",
          onSubmit: async (v) => { await api.post(`/api/asset-works/${w.id}/finish`, v); await reopen(); } }) }, "Завершить") : "",
        canService ? el("button", { type: "button", class: "link danger", "aria-label": `Удалить работу ${w.kind_title}`, onclick: async () => {
          if (!confirm("Удалить ошибочно внесённую работу?")) return;
          try { await api.delete(`/api/asset-works/${w.id}`); await reopen(); } catch (err) { toast(err.message, "error"); }
        } }, "×") : ""))) : el("p", { class: "muted" }, "Работ не было"),
      canService ? el("button", { type: "button", class: "link", onclick: () => workForm(item, reopen) }, "+ работа") : "");
    const fileInput = el("input", { type: "file", hidden: true, "aria-label": "Файл к единице имущества" });
    const dialog = el("dialog", { class: "dialog wide" });
    const close = () => { dialog.close(); dialog.remove(); };
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      if (!file) return;
      try {
        await api.upload(`/api/asset-items/${item.id}/files?filename=${encodeURIComponent(file.name)}`, file);
        close();
        await openCard(item);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    dialog.append(
      el("h2", {}, `${item.number} — ${item.name}`),
      el("table", { class: "recon request-card" }, el("tbody", {},
        field("Статус", item.status_title), field("Категория", item.category_name), field("Владение", item.ownership_title),
        field("Юрлицо", item.legal_entity_name), field("Серийный №", item.serial_number), field("Место", item.cfo_name),
        field("Склад", item.warehouse_name), field("МОЛ", item.employee_name),
        field("Цена приобретения", item.purchase_price ? `${money(item.purchase_price)} ₽` : ""),
        field("Дата приобретения", item.acquired_on ? ruDate(item.acquired_on) : ""),
        field("Карточка ОС", item.fixed_asset_id ? "есть (раздел «Основные средства»)" : ""),
        field("Учёт", item.accounting ? `${item.accounting_title}${item.accounted_on ? ` с ${ruDate(item.accounted_on)}` : ""}`
          + (item.accounting_operation_number ? `, операция № ${item.accounting_operation_number}` : "") : "не поставлена на учёт"),
        field("Цена с удорожанием", item.upgraded_price ? `${money(item.upgraded_price)} ₽` : ""),
        field("Остаток лизинга", item.lease_remaining !== null && item.ownership === "leasing" ? `${money(item.lease_remaining)} ₽` : ""),
        field("Примечание", item.note))),
      el("h3", {}, "Файлы"),
      el("div", { class: "asset-files" },
        ...(item.files.length ? item.files.map((f) => el("div", {},
          el("button", { type: "button", class: "link", onclick: () => api.download(`/api/asset-item-files/${f.id}`, f.filename)
            .catch((err) => toast(err.message, "error")) }, f.filename),
          canEdit() && item.status !== "disposed" ? el("button", { type: "button", class: "link danger", "aria-label": `Убрать ${f.filename}`, onclick: async () => {
            try {
              await api.delete(`/api/asset-item-files/${f.id}`);
              close();
              await openCard(item);
            } catch (err) {
              toast(err.message, "error");
            }
          } }, "×") : "")) : [el("span", { class: "muted" }, "Нет файлов")]),
        canEdit() && item.status !== "disposed" ? el("button", { type: "button", class: "link", onclick: () => fileInput.click() }, "+ файл") : "",
        fileInput),
      worksBox,
      el("h3", {}, "История"),
      el("ul", { class: "asset-history" }, ...events.map((e) => el("li", {},
        `${new Date(e.at).toLocaleString("ru-RU")} — ${e.user_name} ${ACTIONS[e.action] ?? e.action}`,
        e.details ? el("div", { class: "muted small" }, e.details) : ""))),
      el("div", { class: "form-actions" },
        canEdit() && item.status !== "disposed" ? el("button", { type: "button", onclick: () => { close(); editItem(item); } }, "Изменить") : "",
        el("button", { type: "button", class: "secondary", onclick: () => printLabels([item]) }, "Этикетка QR"),
        (canEdit() || item.mine) && item.status !== "disposed" && !item.in_transfer
          ? el("button", { type: "button", class: "secondary", onclick: () => { close(); transferForm({ org, ref, items: [item], onDone: load }); } }, "Переместить") : "",
        canAccount() && !item.accounting && item.status !== "disposed"
          ? el("button", { type: "button", class: "secondary", onclick: () => { close(); accountItems([item]); } }, "Поставить на учёт") : "",
        canAccount() && item.accounting && item.accounted_on && item.status !== "disposed" ? el("button", { type: "button", class: "secondary danger", onclick: async () => {
          if (!confirm(`Снять ${item.number} с учёта? Операция поступления и карточка ОС удалятся.`)) return;
          try {
            await api.post(`/api/asset-items/${item.id}/unaccount`);
            close();
            await load();
          } catch (err) {
            toast(err.message, "error");
          }
        } }, "Снять с учёта") : "",
        canEdit() && !item.fixed_asset_id && !item.accounting ? el("button", { type: "button", class: "secondary danger", onclick: async () => {
          if (!confirm(`Удалить ${item.number}? Удаляйте только заведённое по ошибке.`)) return;
          try {
            await api.delete(`/api/asset-items/${item.id}`);
            close();
            await load();
          } catch (err) {
            toast(err.message, "error");
          }
        } }, "Удалить") : "",
        el("button", { type: "button", class: "secondary", onclick: close }, "Закрыть")),
    );
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  // ставит на учёт бухгалтер или финансовый директор (право на имущество и на проводки) — шаг 19.4
  const canAccount = () => org.permissions.includes("assets_edit") && org.permissions.includes("post");
  // предложить выбытие — инженер, АХО, бухгалтер, ФД (шаг 19.6)
  const canDispose = () => org.permissions.includes("asset_service") || org.permissions.includes("assets_edit");

  function renderBulk() {
    const selected = table.getSelectedData();
    bulkBar.hidden = selected.length === 0;
    bulkBar.replaceChildren(
      el("span", {}, `Отмечено: ${selected.length}`),
      canEdit() ? el("button", { type: "button", onclick: () => bulkEdit(selected) }, "Изменить выбранные") : "",
      canAccount() && selected.some((r) => !r.accounting)
        ? el("button", { type: "button", class: "secondary", onclick: () => accountItems(selected.filter((r) => !r.accounting)) }, "Поставить на учёт") : "",
      canEdit() || selected.every((r) => r.mine)
        ? el("button", { type: "button", class: "secondary", onclick: () => transferForm({ org, ref, items: selected, onDone: async () => { table.deselectRow(); await load(); } }) }, "Переместить / передать") : "",
      canDispose() && selected.every((r) => r.status !== "disposed")
        ? el("button", { type: "button", class: "secondary", onclick: () => disposalForm({ org, ref, items: selected, onDone: async () => { table.deselectRow(); await load(); } }) }, "Выбытие…") : "",
      el("button", { type: "button", class: "secondary", onclick: () => printLabels(selected) }, "Этикетки QR"),
    );
  }

  // постановка на учёт: ОС (карточка, поступление 08/60.01, ввод), малоценка в расход, за балансом, учтено ранее
  async function accountItems(selected) {
    let full;
    try {
      full = await loadReferences(org.id);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const first = selected[0];
    const contracts = (cp) => options(full.contracts.filter((c) => String(c.counterparty_id) === cp), (c) => c.name || `№ ${c.number}`, "— без договора —");
    const plItems = full.leafArticles.filter((a) => a.is_active && a.is_pl);
    const dialog = openForm({
      title: `Поставить на учёт: ${selected.length === 1 ? `${first.number} ${first.name}` : `${selected.length} ед.`}`,
      fields: [
        { name: "kind", label: "Как учитываем", type: "select", options: [["fixed_asset", "Основное средство (01, амортизация)"],
          ["expense", "Малоценка — сразу в расход"], ["off_balance", "За балансом (аренда, лизинг у лизингодателя)"],
          ["earlier", "Учтено ранее (без проводок)"]] },
        { name: "on_date", label: "Дата", type: "date", required: true },
        { name: "counterparty_id", label: "Поставщик (Кт 60.01)", type: "select", options: options(full.counterparties, (c) => c.name, "— без проводки поступления —"),
          hint: "Для ОС без поставщика — только карточка ОС; поступление проведите в журнале" },
        { name: "contract_id", label: "Договор", type: "select", options: contracts(String(first.supplier_id ?? "")) },
        { name: "useful_life_months", label: "Срок полезного использования, месяцев", type: "number" },
        { name: "depreciation_item_id", label: "Статья амортизации", type: "select",
          options: options(plItems.filter((a) => a.debit_account_id), (a) => full.articleLabel.get(a.id), "— по умолчанию —") },
        { name: "commission", label: "Сразу ввести в эксплуатацию (Дт 01 — Кт 08)", type: "checkbox" },
        { name: "expense_account", label: "Счёт затрат", type: "select", options: [["26", "26 Общехозяйственные расходы"], ["44", "44 Расходы на продажу"]] },
        { name: "expense_item_id", label: "Статья ОПиУ расхода", type: "select", options: options(plItems, (a) => full.articleLabel.get(a.id)) },
      ],
      values: { kind: "fixed_asset", on_date: new Date().toISOString().slice(0, 10), counterparty_id: String(first.supplier_id ?? ""),
        contract_id: String(first.supplier_contract_id ?? ""), useful_life_months: "60", commission: true, expense_account: "26" },
      submitText: "Поставить на учёт",
      onSubmit: async (v) => {
        const fixed = v.kind === "fixed_asset";
        await api.post(`/api/organizations/${org.id}/asset-items/account`, {
          ids: selected.map((r) => r.id), kind: v.kind, on_date: v.on_date,
          counterparty_id: idOrNull(v.counterparty_id), contract_id: idOrNull(v.contract_id),
          useful_life_months: fixed ? Number(v.useful_life_months || 0) || null : null,
          depreciation_item_id: fixed ? idOrNull(v.depreciation_item_id) : null,
          commission_on: fixed && v.commission ? v.on_date : null,
          expense_account: v.expense_account, expense_item_id: v.kind === "expense" ? idOrNull(v.expense_item_id) : null,
        });
        toast("Поставлено на учёт");
        table.deselectRow();
        await load();
      },
    });
    const form = dialog.querySelector("form");
    const nodes = (name) => {
      const input = form.elements[name];
      const label = input.closest("label.check") ?? form.querySelector(`label[for="${input.id}"]`);
      return [label, input.closest("label.check") ? null : input,
        input.nextElementSibling?.tagName === "SMALL" ? input.nextElementSibling : null].filter(Boolean);
    };
    const toggle = () => {
      const kind = form.elements.kind.value;
      const show = { counterparty_id: ["fixed_asset", "expense"], contract_id: ["fixed_asset", "expense"],
        useful_life_months: ["fixed_asset"], depreciation_item_id: ["fixed_asset"], commission: ["fixed_asset"],
        expense_account: ["expense"], expense_item_id: ["expense"] };
      for (const [name, kinds] of Object.entries(show)) nodes(name).forEach((n) => { n.hidden = !kinds.includes(kind); });
    };
    form.elements.kind.addEventListener("change", toggle);
    form.elements.counterparty_id.addEventListener("change", () => {
      form.elements.contract_id.replaceChildren(...contracts(form.elements.counterparty_id.value).map(([v, t]) => el("option", { value: v }, t)));
    });
    toggle();
  }

  // QR-этикетки: лист A4, 3 × 8 наклеек; в коде — ссылка на карточку (сканируется телефоном)
  async function printLabels(selected) {
    let labels;
    try {
      const p = new URLSearchParams({ base: location.origin });
      selected.forEach((r) => p.append("ids", r.id));
      labels = await api.get(`/api/organizations/${org.id}/asset-labels?${p}`);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const win = window.open("", "_blank");
    if (!win) {
      toast("Браузер не дал открыть окно печати — разрешите всплывающие окна", "error");
      return;
    }
    win.document.write(`<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Этикетки ${escape(org.name)}</title>
      <style>@page { size: A4; margin: 10mm 5mm; } body { margin: 0; font-family: Arial, sans-serif; }
      .sheet { display: grid; grid-template-columns: repeat(3, 66mm); grid-auto-rows: 34mm; gap: 2mm 3mm; }
      .label { border: 1px dashed #bbb; padding: 2mm; display: flex; gap: 2mm; align-items: center; overflow: hidden; break-inside: avoid; }
      .label svg { width: 28mm; height: 28mm; flex: none; } .num { font-size: 13pt; font-weight: bold; }
      .name { font-size: 8pt; margin-top: 1mm; } .place { font-size: 7pt; color: #555; margin-top: 1mm; }
      .org { font-size: 7pt; color: #555; } @media print { .label { border-color: #eee; } .hint { display: none; } }
      .hint { padding: 8px; font-size: 12px; }</style></head><body>
      <div class="hint">Печать: Ctrl+P (на телефоне — «Поделиться» → «Печать»). Формат — A4, 24 наклейки 70×37 мм.</div>
      <div class="sheet">${labels.map((x) => `<div class="label">${x.svg}<div><div class="num">${escape(x.number)}</div>
        <div class="name">${escape(x.name)}</div><div class="place">${escape(x.place)}</div><div class="org">${escape(org.name)}</div></div></div>`).join("")}</div>
      </body></html>`);
    win.document.close();
    win.focus();
  }

  function bulkEdit(selected) {
    const KEEP = "keep";
    const keep = (items, label) => [[KEEP, "— не менять —"], ...options(items, label, "— очистить —")];
    openForm({
      title: `Изменить единиц: ${selected.length}`,
      fields: [
        { name: "cfo_id", label: "Место (ЦФО)", type: "select", options: keep(ref.cfos, (c) => ref.cfoLabel.get(c.id)) },
        { name: "warehouse_id", label: "Склад", type: "select", options: keep(ref.warehouses, (w) => w.name) },
        { name: "employee_id", label: "МОЛ", type: "select", options: keep(ref.employees, (e) => e.full_name) },
        { name: "category_id", label: "Категория", type: "select", options: keep(ref.categories, (c) => c.name) },
        { name: "status", label: "Статус", type: "select", options: [[KEEP, "— не менять —"], ...STATUSES.filter(([k]) => k !== "disposed")] },
      ],
      values: { cfo_id: KEEP, warehouse_id: KEEP, employee_id: KEEP, category_id: KEEP, status: KEEP },
      submitText: "Применить",
      onSubmit: async (v) => {
        const body = { ids: selected.map((r) => r.id) };
        for (const [k, value] of Object.entries(v)) {
          if (value !== KEEP) body[k] = k === "status" ? value : idOrNull(value);
        }
        await api.post(`/api/organizations/${org.id}/asset-items/bulk`, body);
        toast("Изменено");
        table.deselectRow();
        await load();
      },
    });
  }

  async function importFile(input) {
    const file = input.files[0];
    if (!file) return;
    try {
      const r = await api.upload(`/api/organizations/${org.id}/import/asset-items`, file);
      const errors = r.errors.map(([row, text]) => `строка ${row}: ${text}`);
      toast(`Загружено единиц: ${r.created}${errors.length ? `; с ошибками строк: ${errors.length}` : ""}`, errors.length ? "error" : "ok");
      if (errors.length) {
        openForm({ title: "Строки с ошибками не загружены", fields: [], extra: el("ul", {}, ...errors.map((e) => el("li", {}, e))),
          submitText: "Понятно", onSubmit: async () => {} });
      }
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  const requests = assetRequestsTab(requestsBox, { org: () => org, ref: () => ref });
  const transfers = assetTransfersTab(transfersBox, { org: () => org, reload: () => load() });
  const disposals = assetDisposalsTab(disposalsBox, { org: () => org, reload: () => load() });
  function renderTabs() {
    tabBar.replaceChildren(...[["registry", "Реестр"], ["requests", "Заявки на имущество"], ["transfers", "Перемещения"], ["disposals", "Выбытие"]].map(([k, t]) => el("button", {
      type: "button", role: "tab", class: `tab${k === tab ? " active" : ""}`,
      onclick: () => { tab = k; remember(TAB_KEY, k); showTab(); },
    }, t)));
  }
  async function showTab() {
    renderTabs();
    registryBox.hidden = tab !== "registry";
    requestsBox.hidden = tab !== "requests";
    transfersBox.hidden = tab !== "transfers";
    disposalsBox.hidden = tab !== "disposals";
    if (tab === "requests") await requests.show();
    else if (tab === "transfers") await transfers.show();
    else if (tab === "disposals") await disposals.show();
    else table.redraw(true);
  }

  async function open() {
    await loadRef();
    renderFilters();
    await load();
    await showTab();
  }
  // переход по QR-этикетке: #/assets?item=ID — открыть карточку единицы
  const linked = new URLSearchParams(location.hash.split("?")[1] ?? "").get("item");
  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    Object.keys(filters).forEach((k) => { filters[k] = k === "mine" ? false : ""; });
    await open();
  });
  await table.ready;
  await open();
  if (linked) {
    try {
      const item = await api.get(`/api/asset-items/${Number(linked)}`);
      if (item.organization_id !== org.id) {
        org = orgs.find((o) => o.id === item.organization_id) ?? org;
        orgSelect.value = String(org.id);
        await open();
      }
      await openCard(item);
    } catch (err) {
      toast(err.message, "error");
    }
  }
}

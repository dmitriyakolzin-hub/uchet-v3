// «Имущество»: реестр единиц имущества, карточка с историей и файлами, групповое изменение, ввод имеющегося
// имущества вручную и из Excel (docs/TZ.md, раздел 28, «Детали 19.1»).
import { api } from "../api.js";
import { date as ruDate, money, parseNumber } from "../format.js";
import { el, instruction, makeTable, openForm, toast } from "../ui.js";

const ORG_KEY = "uchet3_org";
const STATUSES = [["stock", "На складе"], ["in_use", "В эксплуатации"], ["repair", "В ремонте"], ["disposed", "Выбыло"]];
const OWNERSHIP = [["own", "Собственное"], ["leasing", "Лизинг"], ["rent", "Аренда"]];
const ACTIONS = { created: "завёл(а)", edited: "изменил(а)", bulk_edited: "изменил(а) группой", imported: "загрузил(а)",
  file: "приложил(а) файл", file_removed: "убрал(а) файл" };

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
  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Имущество"), el("label", { class: "inline" }, "Организация ", orgSelect)),
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
        formatter: (c) => `<span class="badge asset-${c.getRow().getData().status}">${escape(c.getValue())}</span>` },
      { title: "Владение", field: "ownership_title", width: 120 },
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

  async function openCard(row) {
    let item;
    let events;
    try {
      [item, events] = await Promise.all([api.get(`/api/asset-items/${row.id}`), api.get(`/api/asset-items/${row.id}/events`)]);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const field = (title, value) => (value ? el("tr", {}, el("th", {}, title), el("td", {}, value)) : "");
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
        field("Карточка ОС", item.fixed_asset_id ? "есть (раздел «Основные средства»)" : ""), field("Примечание", item.note))),
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
      el("h3", {}, "История"),
      el("ul", { class: "asset-history" }, ...events.map((e) => el("li", {},
        `${new Date(e.at).toLocaleString("ru-RU")} — ${e.user_name} ${ACTIONS[e.action] ?? e.action}`,
        e.details ? el("div", { class: "muted small" }, e.details) : ""))),
      el("div", { class: "form-actions" },
        canEdit() && item.status !== "disposed" ? el("button", { type: "button", onclick: () => { close(); editItem(item); } }, "Изменить") : "",
        canEdit() && !item.fixed_asset_id ? el("button", { type: "button", class: "secondary danger", onclick: async () => {
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

  function renderBulk() {
    const selected = table.getSelectedData();
    bulkBar.hidden = !canEdit() || selected.length === 0;
    bulkBar.replaceChildren(
      el("span", {}, `Отмечено: ${selected.length}`),
      el("button", { type: "button", onclick: () => bulkEdit(selected) }, "Изменить выбранные"),
    );
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

  async function open() {
    await loadRef();
    renderFilters();
    await load();
  }
  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    Object.keys(filters).forEach((k) => { filters[k] = k === "mine" ? false : ""; });
    await open();
  });
  await table.ready;
  await open();
}

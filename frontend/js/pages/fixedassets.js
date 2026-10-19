// «Основные средства»: карточки, ввод в эксплуатацию (Дт 01 — Кт 08), ежемесячная амортизация (Дт 26/44 — Кт 02).
// Стоимость, амортизация и остаточная стоимость — из журнала (аналитика «Основное средство» на 01, 02, 08).
import { api } from "../api.js";
import { date as ruDate, isoDate, money, parseNumber, sumMoney } from "../format.js";
import { openOperation } from "../opform.js";
import { el, openForm, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
  "Ноябрь", "Декабрь"];

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

export async function renderFixedAssets(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Основные средства"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  const today = new Date();
  let onDate = isoDate(today);
  // по умолчанию — прошлый месяц: амортизацию начисляют после его окончания
  let period = today.getMonth() === 0 ? { year: today.getFullYear() - 1, month: 12 } : { year: today.getFullYear(), month: today.getMonth() };

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const dateInput = el("input", { type: "date", value: onDate, "aria-label": "Остатки на дату" });
  // кнопка появляется после загрузки справочников (форме нужны юрлица, ЦФО, статьи)
  const addButton = el("button", { type: "button", hidden: true, onclick: () => editAsset(null) }, "Новое основное средство");
  const cardsBox = el("div");
  const deprBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Основные средства"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("p", { class: "muted" }, "Поступление проводите в журнале: Дт 08 — Кт 60 с аналитикой «Основное средство». "
      + "«Ввести в эксплуатацию» переносит стоимость с 08 на 01. Амортизация — линейная, с месяца, следующего за вводом. "
      + "ОС, введённые до даты начальных остатков, вводятся без операции — их стоимость и амортизацию внесите "
      + "на вкладке «Начальные остатки» (Дт 01 — Кт 00, Дт 00 — Кт 02)."),
    el("div", { class: "filters" }, el("label", { class: "inline" }, "Остатки на ", dateInput), addButton),
    cardsBox,
    deprBox,
  );

  const can = (perm) => org.permissions.includes(perm);
  let ref = null;

  async function act(promise, message) {
    try {
      await promise;
      if (message) toast(message);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function showOperation(id) {
    const [op, fresh] = await Promise.all([api.get(`/api/operations/${id}`), loadReferences(org.id)]);
    openOperation({ ref: fresh, org, op, perms: org.permissions, onChanged: () => load() });
  }

  function editAsset(asset) {
    const plItems = ref.articles.filter((a) => a.is_pl && a.debit_account_id && (a.is_active || a.id === asset?.pl_item_id));
    const fields = [
      { name: "name", label: "Наименование", required: true, placeholder: "Ноутбук бухгалтера" },
      { name: "inventory_number", label: "Инвентарный номер" },
      { name: "legal_entity_id", label: "Юрлицо", type: "select",
        options: ref.entities.filter((e) => e.is_active || e.id === asset?.legal_entity_id).map((e) => [String(e.id), e.short_name]) },
      { name: "cost", label: "Первоначальная стоимость, ₽", required: true },
      { name: "useful_life_months", label: "Срок полезного использования, месяцев", type: "number", required: true },
      { name: "cfo_id", label: "ЦФО (куда относится амортизация)", type: "select",
        options: [["", "— не указан —"], ...ref.cfos.map((c) => [String(c.id), ref.cfoLabel.get(c.id)])] },
      { name: "pl_item_id", label: "Статья амортизации (ОПиУ)", type: "select",
        options: plItems.map((a) => [String(a.id), a.name]),
        hint: "Счёт затрат берётся из шаблона статьи: 26 — управленческие, 44 — коммерческие расходы" },
      { name: "project_id", label: "Инвестпроект (необязательно)", type: "select",
        options: [["", "— нет —"], ...ref.projects.filter((x) => x.is_active || x.id === asset?.project_id).map((x) => [String(x.id), x.name])],
        hint: "Вложения в это ОС (Дт 08) попадут в отчёт «Инвестиции» по проекту" },
      ...(asset ? [{ name: "is_active", label: "Действует (снимите — в архив, амортизация не начисляется)", type: "checkbox" }] : []),
    ];
    const defaultItem = plItems.find((a) => a.name === "Амортизация основных средств");
    openForm({
      title: asset ? `Основное средство: ${asset.name}` : "Новое основное средство",
      fields,
      values: asset
        ? { ...asset, legal_entity_id: String(asset.legal_entity_id), cost: money(asset.cost),
          cfo_id: asset.cfo_id ? String(asset.cfo_id) : "", pl_item_id: asset.pl_item_id ? String(asset.pl_item_id) : "",
          project_id: asset.project_id ? String(asset.project_id) : "" }
        : { useful_life_months: 36, pl_item_id: defaultItem ? String(defaultItem.id) : "" },
      onSubmit: async (v) => {
        const body = {
          name: v.name, inventory_number: v.inventory_number, legal_entity_id: Number(v.legal_entity_id),
          cost: parseNumber(v.cost), useful_life_months: Number(v.useful_life_months),
          cfo_id: v.cfo_id ? Number(v.cfo_id) : null, pl_item_id: v.pl_item_id ? Number(v.pl_item_id) : null,
          project_id: v.project_id ? Number(v.project_id) : null,
          ...(asset ? { is_active: v.is_active } : {}),
        };
        if (asset) await api.patch(`/api/fixed-assets/${asset.id}`, body);
        else await api.post(`/api/organizations/${org.id}/fixed-assets`, body);
        toast("Сохранено");
        await load();
      },
    });
  }

  function commission(asset) {
    openForm({
      title: `Ввод в эксплуатацию: ${asset.name}`,
      fields: [{ name: "commissioned_on", label: "Дата ввода", type: "date", required: true,
        hint: "Стоимость переносится с 08 на 01 этой датой; амортизация — с первого числа следующего месяца" }],
      values: { commissioned_on: isoDate(new Date()) },
      submitText: "Ввести",
      onSubmit: async (v) => {
        await api.post(`/api/fixed-assets/${asset.id}/commission`, { commissioned_on: v.commissioned_on });
        toast("Введено в эксплуатацию");
        await load();
      },
    });
  }

  function dispose(asset) {
    const contracts = ref.contracts.filter((c) => c.legal_entity_id === asset.legal_entity_id);
    openForm({
      title: `Продажа или списание: ${asset.name}`,
      fields: [
        { name: "kind", label: "Вид выбытия", type: "select", options: [["writeoff", "Списание"], ["sale", "Продажа"]] },
        { name: "disposed_on", label: "Дата выбытия", type: "date", required: true,
          hint: "Амортизация за предыдущие месяцы должна быть начислена; в месяце выбытия она не начисляется" },
        { name: "counterparty_id", label: "Покупатель (для продажи)", type: "select",
          options: [["", "—"], ...ref.counterparties.map((c) => [String(c.id), c.inn ? `${c.name} (${c.inn})` : c.name])] },
        { name: "contract_id", label: "Договор (для продажи)", type: "select",
          options: [["", "—"], ...contracts.map((c) => [String(c.id), `№ ${c.number} — ${ref.counterpartyName.get(c.counterparty_id) ?? ""}`])] },
        { name: "price", label: "Цена с НДС, ₽ (для продажи)" },
        { name: "vat_rate", label: "НДС в цене", type: "select",
          options: [["20", "20%"], ["22", "22%"], ["10", "10%"], ["7", "7%"], ["5", "5%"], ["0", "без НДС"]] },
      ],
      values: { kind: "writeoff", disposed_on: isoDate(new Date()), vat_rate: "20" },
      submitText: "Провести",
      onSubmit: async (v) => {
        const sale = v.kind === "sale";
        await api.post(`/api/fixed-assets/${asset.id}/dispose`, {
          kind: v.kind, disposed_on: v.disposed_on,
          counterparty_id: sale && v.counterparty_id ? Number(v.counterparty_id) : null,
          contract_id: sale && v.contract_id ? Number(v.contract_id) : null,
          price: sale ? parseNumber(v.price) ?? "0" : "0", vat_rate: sale ? Number(v.vat_rate) : 0,
        });
        toast(sale ? "Продажа проведена" : "Списание проведено");
        await load();
      },
    });
  }

  function cardsTable(assets, balances) {
    if (!assets.length) return el("p", { class: "muted" }, "Основных средств пока нет.");
    const entityName = new Map(ref.entities.map((e) => [e.id, e.short_name]));
    const link = (text, onclick, cls = "") => el("button", { type: "button", class: `link ${cls}`, onclick }, text);
    const rows = assets.map((a) => {
      const b = balances.get(a.id) ?? {};
      const actions = [];
      if (can("edit_reference")) actions.push(link("Изменить", () => editAsset(a)));
      if (can("post") && !a.commissioned_on) actions.push(link("Ввести в эксплуатацию", () => commission(a)));
      if (can("post") && a.commissioned_on && !a.disposed_on) {
        actions.push(link("Продать / списать", () => dispose(a)));
      }
      if (can("post") && a.disposed_on) {
        actions.push(link("Отменить выбытие", () => {
          if (confirm(`Отменить выбытие «${a.name}»? Операция выбытия будет удалена.`)) {
            act(api.post(`/api/fixed-assets/${a.id}/undispose`), "Выбытие отменено");
          }
        }, "remove"));
      }
      if (a.disposal_operation_id) actions.push(link("Операция выбытия", () => showOperation(a.disposal_operation_id)));
      if (can("post") && a.commissioned_on && !a.disposed_on) {
        actions.push(link("Отменить ввод", () => {
          if (confirm(`Отменить ввод в эксплуатацию «${a.name}»?`)) {
            act(api.post(`/api/fixed-assets/${a.id}/uncommission`), "Ввод отменён");
          }
        }, "remove"));
      }
      if (a.commission_operation_id) actions.push(link("Операция ввода", () => showOperation(a.commission_operation_id)));
      return el("tr", { class: a.is_active ? "" : "muted" },
        el("td", {}, a.inventory_number),
        el("td", {}, a.name + (a.is_active ? "" : " (архив)")),
        el("td", {}, entityName.get(a.legal_entity_id) ?? ""),
        el("td", {}, a.cfo_id ? ref.cfoLabel.get(a.cfo_id) ?? "" : ""),
        el("td", { class: "num" }, String(a.useful_life_months)),
        el("td", {}, a.commissioned_on ? ruDate(a.commissioned_on) : el("span", { class: "status status-draft" }, "не введено"),
          a.disposed_on ? el("div", { class: "muted" }, `${a.disposal_kind === "sale" ? "продано" : "списано"} ${ruDate(a.disposed_on)}`) : null),
        el("td", { class: "num" }, money(b.cost ?? 0)),
        el("td", { class: "num" }, money(b.depreciation ?? 0)),
        el("td", { class: "num strong" }, money(b.residual ?? 0)),
        el("td", { class: "num" }, money(b.monthly ?? 0)),
        el("td", { class: "num" }, Number(b.investment) ? money(b.investment) : ""),
        el("td", { class: "actions" }, ...actions));
    });
    return el("table", { class: "recon versions assets" },
      el("thead", {}, el("tr", {}, ...["Инв. №", "Наименование", "Юрлицо", "ЦФО", "Срок, мес.", "Введено", "Стоимость (01)",
        "Амортизация (02)", "Остаточная", "В месяц", "На 08", ""].map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows));
  }

  async function renderDepreciation() {
    if (!can("post")) {
      deprBox.replaceChildren();
      return;
    }
    const monthSelect = el("select", { class: "inline-select", "aria-label": "Месяц" },
      ...MONTHS.map((m, i) => el("option", { value: i + 1, selected: i + 1 === period.month }, m)));
    const yearSelect = el("select", { class: "inline-select", "aria-label": "Год" },
      ...[0, 1, 2].map((d) => today.getFullYear() - d).map((y) => el("option", { value: y, selected: y === period.year }, y)));
    const change = () => { period = { year: Number(yearSelect.value), month: Number(monthSelect.value) }; renderDepreciation(); };
    monthSelect.addEventListener("change", change);
    yearSelect.addEventListener("change", change);
    const q = `year=${period.year}&month=${period.month}`;
    const [preview, runs] = await Promise.all([
      api.get(`/api/organizations/${org.id}/depreciation/preview?${q}`),
      api.get(`/api/organizations/${org.id}/depreciation-runs?year=${period.year}`),
    ]);
    const total = sumMoney(preview.map((p) => p.amount));
    const deprButton = el("button", { type: "button", disabled: !preview.length, onclick: () => {
      if (confirm(`Начислить амортизацию за ${MONTHS[period.month - 1].toLowerCase()} ${period.year}: ${money(total)} ₽?`)) {
        act(api.post(`/api/organizations/${org.id}/depreciation-runs`, period), "Амортизация начислена");
      }
    } }, "Начислить за месяц");
    deprBox.replaceChildren(
      el("div", { class: "page-head section" }, el("h2", {}, "Амортизация")),
      el("p", { class: "muted" }, "Начисляется за месяц по всем юрлицам одной операцией на юрлицо (последний день месяца): "
        + "стоимость / срок, в последний месяц срока — остаток. Месяцы — по порядку; отменять — с последнего."),
      el("div", { class: "filters" }, el("label", { class: "inline" }, "Месяц ", monthSelect), yearSelect, deprButton),
      preview.length
        ? el("table", { class: "recon versions assets" },
          el("thead", {}, el("tr", {}, ...["Юрлицо", "Основное средство", "Остаточная до начисления", "Начислить"].map((t) => el("th", {}, t)))),
          el("tbody", {}, ...preview.map((p) => el("tr", {},
            el("td", {}, p.legal_entity_name),
            el("td", {}, p.inventory_number ? `${p.asset_name} (${p.inventory_number})` : p.asset_name),
            el("td", { class: "num" }, money(p.residual_before)), el("td", { class: "num" }, money(p.amount))))))
        : el("p", { class: "muted" }, `За ${MONTHS[period.month - 1].toLowerCase()} ${period.year} начислять нечего.`),
      el("h3", {}, `Начисления за ${period.year} год`),
      runs.length
        ? el("table", { class: "recon versions assets" },
          el("thead", {}, el("tr", {}, ...["Месяц", "Юрлицо", "Сумма", "Операция", ""].map((t) => el("th", {}, t)))),
          el("tbody", {}, ...runs.map((r) => el("tr", {},
            el("td", {}, `${MONTHS[r.month - 1]} ${r.year}`), el("td", {}, r.legal_entity_name),
            el("td", { class: "num" }, money(r.total)),
            el("td", {}, r.operation_id ? el("button", { type: "button", class: "link", onclick: () => showOperation(r.operation_id) },
              `№ ${r.operation_number}`) : ""),
            el("td", {}, el("button", { type: "button", class: "link remove", onclick: () => {
              if (confirm(`Отменить начисление за ${MONTHS[r.month - 1].toLowerCase()} ${r.year} (${r.legal_entity_name})?`)) {
                act(api.delete(`/api/depreciation-runs/${r.id}`), "Начисление отменено");
              }
            } }, "Отменить"))))))
        : el("p", { class: "muted" }, "Начислений пока нет."),
    );
  }

  async function load() {
    if (org.restricted) {
      addButton.hidden = true;
      cardsBox.replaceChildren(el("p", { class: "muted" }, "Раздел недоступен руководителю ЦФО."));
      deprBox.replaceChildren();
      return;
    }
    addButton.hidden = true;
    ref = await loadReferences(org.id);
    addButton.hidden = !can("edit_reference");
    const [assets, register] = await Promise.all([
      api.get(`/api/organizations/${org.id}/fixed-assets`),
      api.get(`/api/organizations/${org.id}/fixed-assets/register?on=${onDate}`),
    ]);
    cardsBox.replaceChildren(cardsTable(assets, new Map(register.map((r) => [r.id, r]))));
    await renderDepreciation();
  }

  dateInput.addEventListener("change", () => { if (dateInput.value) { onDate = dateInput.value; load(); } });
  orgSelect.addEventListener("change", () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    load();
  });
  await load();
}

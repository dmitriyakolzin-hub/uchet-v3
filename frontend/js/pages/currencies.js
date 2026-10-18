import { api } from "../api.js";
import { el, makeTable, openForm, toast, yesNo } from "../ui.js";

// 81.543200 → «81,5432»: убираем лишние нули, но оставляем минимум 2 знака
function formatRate(value) {
  if (value === null || value === undefined) return "";
  let [whole, frac = ""] = String(value).split(".");
  frac = frac.replace(/0+$/, "").padEnd(2, "0");
  return `${whole},${frac}`;
}

// Сегодняшняя дата по часам компьютера (а не по UTC) в формате ГГГГ-ММ-ДД
function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

const CURRENCY_FIELDS = [
  { name: "code", label: "Буквенный код", required: true, hint: "3 латинские буквы, например USD" },
  { name: "numeric_code", label: "Цифровой код", required: true, hint: "3 цифры, например 840" },
  { name: "name", label: "Наименование", required: true, placeholder: "Доллар США" },
];

const RATE_FIELDS = [
  { name: "rate_date", label: "Дата", type: "date", required: true },
  { name: "rate", label: "Курс, ₽", required: true, hint: "Например 81,5432 — до 6 знаков после запятой" },
  { name: "nominal", label: "За сколько единиц валюты", required: true, hint: "Обычно 1; для иены, например, 100" },
];

export async function renderCurrencies(view, { me }) {
  const canEditRates =
    me.is_admin || me.memberships.some((m) => m.permissions.includes("edit_reference"));
  let selected = null;

  const addCurrency = me.is_admin
    ? el("button", { type: "button", onclick: () => editCurrency(null) }, "Добавить валюту")
    : null;
  const addRate = el("button", { type: "button", hidden: true, onclick: () => editRate(null) }, "Добавить курс");
  const ratesTitle = el("h2", {}, "Курсы");
  const ratesHint = el("p", { class: "muted" }, "Выберите валюту в таблице выше.");
  const currencyBox = el("div");
  const rateBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Валюты"), addCurrency),
    currencyBox,
    el("div", { class: "page-head section" }, ratesTitle, addRate),
    ratesHint,
    rateBox,
  );

  const currencyColumns = [
    { title: "Код", field: "code", width: 90 },
    { title: "Цифровой код", field: "numeric_code", width: 140 },
    { title: "Наименование", field: "name", widthGrow: 3 },
    { title: "Активна", field: "is_active", formatter: yesNo, width: 100 },
  ];
  if (me.is_admin) {
    currencyColumns.push({
      title: "", field: "_edit", width: 48, hozAlign: "center", headerSort: false,
      formatter: () => "✎", cellClick: (_e, cell) => editCurrency(cell.getRow().getData()),
    });
  }
  const currencyTable = makeTable(currencyBox, { selectableRows: 1, columns: currencyColumns });
  currencyTable.on("rowClick", (_e, row) => selectCurrency(row.getData()));

  const rateColumns = [
    { title: "Дата", field: "rate_date", formatter: (c) => formatDate(c.getValue()), sorter: "string" },
    { title: "Курс, ₽", field: "rate", formatter: (c) => formatRate(c.getValue()), hozAlign: "right" },
    { title: "За единиц", field: "nominal", hozAlign: "right", width: 120 },
  ];
  if (canEditRates) {
    rateColumns.push(
      {
        title: "", field: "_edit", width: 48, hozAlign: "center", headerSort: false,
        formatter: () => "✎", cellClick: (_e, cell) => editRate(cell.getRow().getData()),
      },
      {
        title: "", field: "_delete", width: 48, hozAlign: "center", headerSort: false,
        formatter: () => "✕", cellClick: (_e, cell) => deleteRate(cell.getRow().getData()),
      },
    );
  }
  const rateTable = makeTable(rateBox, { columns: rateColumns });

  async function loadCurrencies() {
    const currencies = await api.get("/api/currencies");
    await currencyTable.setData(currencies);
    if (selected) {
      selected = currencies.find((c) => c.id === selected.id) ?? null;
      if (selected) currencyTable.selectRow(selected.id);
    }
  }

  async function selectCurrency(currency) {
    selected = currency;
    currencyTable.deselectRow();
    currencyTable.selectRow(currency.id);
    ratesHint.hidden = true;
    if (currency.is_base) {
      ratesTitle.textContent = `Курсы: ${currency.code}`;
      addRate.hidden = true;
      ratesHint.hidden = false;
      ratesHint.textContent = "Рубль — базовая валюта, его курс всегда 1.";
      await rateTable.setData([]);
      return;
    }
    ratesTitle.textContent = `Курсы: ${currency.code} — ${currency.name}`;
    addRate.hidden = !canEditRates;
    await rateTable.setData(await api.get(`/api/currencies/${currency.id}/rates`));
  }

  function editCurrency(currency) {
    openForm({
      title: currency ? `Валюта ${currency.code}` : "Новая валюта",
      fields: currency
        ? [
            { name: "name", label: "Наименование", required: true },
            { name: "is_active", label: "Активна", type: "checkbox" },
          ]
        : CURRENCY_FIELDS,
      values: currency ?? {},
      onSubmit: async (values) => {
        const saved = currency
          ? await api.patch(`/api/currencies/${currency.id}`, values)
          : await api.post("/api/currencies", values);
        toast("Сохранено");
        selected = saved;
        await loadCurrencies();
        await selectCurrency(saved);
      },
    });
  }

  function editRate(rate) {
    openForm({
      title: rate ? `Курс ${selected.code} на ${formatDate(rate.rate_date)}` : `Новый курс ${selected.code}`,
      fields: RATE_FIELDS,
      values: rate
        ? { ...rate, rate: formatRate(rate.rate) }
        : { rate_date: today(), nominal: "1" },
      onSubmit: async (values) => {
        const body = {
          rate_date: values.rate_date,
          rate: values.rate.replace(/\s/g, "").replace(",", "."),
          nominal: Number(values.nominal),
        };
        if (rate) await api.put(`/api/exchange-rates/${rate.id}`, body);
        else await api.post(`/api/currencies/${selected.id}/rates`, body);
        toast("Сохранено");
        await selectCurrency(selected);
      },
    });
  }

  async function deleteRate(rate) {
    if (!confirm(`Удалить курс ${selected.code} на ${formatDate(rate.rate_date)}?`)) return;
    try {
      await api.delete(`/api/exchange-rates/${rate.id}`);
      toast("Курс удалён");
      await selectCurrency(selected);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  await Promise.all([currencyTable.ready, rateTable.ready]);
  await loadCurrencies();
}

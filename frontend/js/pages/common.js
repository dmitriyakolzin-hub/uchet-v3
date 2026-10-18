// «Общие справочники»: едины для всех организаций, изменяет администратор.
import { referenceTab } from "../reftab.js";
import { el } from "../ui.js";

const TAB_KEY = "uchet3_common_tab";

const TABS = [
  {
    key: "banks",
    title: "Банки",
    addText: "Добавить банк",
    columns: [
      { title: "БИК", field: "bic", width: 120 },
      { title: "Наименование", field: "name", widthGrow: 3 },
      { title: "Корр. счёт", field: "corr_account", width: 210 },
      { title: "Город", field: "city" },
    ],
    fields: () => [
      { name: "bic", label: "БИК", required: true, hint: "9 цифр", createOnly: true },
      { name: "name", label: "Наименование", required: true, placeholder: "ПАО Сбербанк" },
      { name: "corr_account", label: "Корреспондентский счёт", hint: "20 цифр", nullable: true },
      { name: "city", label: "Город" },
    ],
  },
  {
    key: "units",
    title: "Единицы измерения",
    addText: "Добавить единицу",
    columns: [
      { title: "Код ОКЕИ", field: "okei", width: 120 },
      { title: "Краткое", field: "short_name", width: 120 },
      { title: "Наименование", field: "name", widthGrow: 3 },
    ],
    fields: () => [
      { name: "okei", label: "Код ОКЕИ", required: true, hint: "3–4 цифры, например 796 — штука", createOnly: true },
      { name: "short_name", label: "Краткое наименование", required: true, placeholder: "шт" },
      { name: "name", label: "Полное наименование", required: true, placeholder: "Штука" },
    ],
  },
];

function recall() {
  try {
    return localStorage.getItem(TAB_KEY);
  } catch {
    return null;
  }
}

export async function renderCommon(view, { me }) {
  let tabKey = TABS.some((t) => t.key === recall()) ? recall() : TABS[0].key;
  const tabBar = el("div", { class: "tabs", role: "tablist" });
  const box = el("div");
  view.replaceChildren(
    el("h1", {}, "Общие справочники"),
    el("p", { class: "muted" }, "Едины для всех организаций. Изменяет администратор."),
    tabBar,
    box,
  );

  async function show() {
    tabBar.replaceChildren(
      ...TABS.map((t) =>
        el("button", {
          type: "button", role: "tab", class: t.key === tabKey ? "tab active" : "tab",
          "aria-selected": t.key === tabKey ? "true" : "false",
          onclick: () => {
            tabKey = t.key;
            try { localStorage.setItem(TAB_KEY, tabKey); } catch { /* не страшно */ }
            show();
          },
        }, t.title),
      ),
    );
    const tab = TABS.find((t) => t.key === tabKey);
    await referenceTab(box, {
      title: tab.title,
      addText: tab.addText,
      listUrl: `/api/${tab.key}`,
      itemUrl: (id) => `/api/${tab.key}/${id}`,
      canEdit: me.is_admin,
      columns: tab.columns,
      fields: tab.fields,
    });
  }

  await show();
}

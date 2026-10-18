import { referenceTab } from "../reftab.js";
import { el } from "../ui.js";

export async function renderBanks(view, { me }) {
  const box = el("div");
  view.replaceChildren(
    el("h1", {}, "Банки"),
    el("p", { class: "muted" }, "Общий справочник для всех организаций. Изменяет администратор."),
    box,
  );
  await referenceTab(box, {
    title: "Список банков",
    addText: "Добавить банк",
    listUrl: "/api/banks",
    itemUrl: (id) => `/api/banks/${id}`,
    canEdit: me.is_admin,
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
  });
}

import { api } from "../api.js";
import { editButton, el, makeTable, openForm, orNull, toast, yesNo } from "../ui.js";

const KINDS = [
  ["group", "Своя группа"],
  ["client", "Клиент"],
];
const KIND_TITLES = Object.fromEntries(KINDS);

const ORG_FIELDS = [
  { name: "name", label: "Наименование", required: true },
  { name: "kind", label: "Тип", type: "select", options: KINDS },
  { name: "note", label: "Примечание", type: "textarea" },
];

const ENTITY_FIELDS = [
  { name: "full_name", label: "Полное наименование", required: true, placeholder: "ООО «Ромашка»" },
  { name: "short_name", label: "Краткое наименование", required: true, placeholder: "Ромашка" },
  { name: "inn", label: "ИНН", required: true, hint: "10 цифр для организации, 12 — для ИП" },
  { name: "kpp", label: "КПП", hint: "9 цифр, для ИП не заполняется" },
  { name: "ogrn", label: "ОГРН / ОГРНИП", hint: "13 или 15 цифр" },
];

const can = (org, perm) => org?.permissions.includes(perm);

export async function renderOrganizations(view, { me }) {
  let selected = null;

  const addOrgButton = me.is_admin
    ? el("button", { type: "button", onclick: () => editOrganization(null) }, "Добавить организацию")
    : null;
  const addEntityButton = el(
    "button",
    { type: "button", hidden: true, onclick: () => editEntity(null) },
    "Добавить юрлицо",
  );
  const entitiesTitle = el("h2", {}, "Юрлица");
  const entitiesHint = el("p", { class: "muted" }, "Выберите организацию в таблице выше.");
  const orgTableBox = el("div");
  const entityTableBox = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Организации"), addOrgButton),
    orgTableBox,
    el("div", { class: "page-head section" }, entitiesTitle, addEntityButton),
    entitiesHint,
    entityTableBox,
  );

  const orgTable = makeTable(orgTableBox, {
    selectableRows: 1,
    columns: [
      { title: "Наименование", field: "name", widthGrow: 3 },
      { title: "Тип", field: "kind", formatter: (c) => KIND_TITLES[c.getValue()] ?? "" },
      { title: "Активна", field: "is_active", formatter: yesNo, width: 100 },
      { title: "Примечание", field: "note", widthGrow: 2 },
      editButton((row) => editOrganization(row), (row) => can(row, "edit_reference")),
    ],
  });
  orgTable.on("rowClick", (_e, row) => selectOrganization(row.getData()));

  const entityTable = makeTable(entityTableBox, {
    columns: [
      { title: "Краткое наименование", field: "short_name", widthGrow: 2 },
      { title: "Полное наименование", field: "full_name", widthGrow: 3 },
      { title: "ИНН", field: "inn" },
      { title: "КПП", field: "kpp" },
      { title: "ОГРН", field: "ogrn" },
      { title: "Активно", field: "is_active", formatter: yesNo, width: 100 },
      editButton((row) => editEntity(row), () => can(selected, "edit_reference")),
    ],
  });

  async function loadOrganizations() {
    const orgs = await api.get("/api/organizations");
    await orgTable.setData(orgs);
    if (selected) {
      selected = orgs.find((o) => o.id === selected.id) ?? null;
      if (selected) orgTable.selectRow(selected.id);
    }
    if (!selected && orgs.length === 1) selectOrganization(orgs[0]);
  }

  async function selectOrganization(org) {
    selected = org;
    orgTable.deselectRow();
    orgTable.selectRow(org.id);
    entitiesTitle.textContent = `Юрлица: ${org.name}`;
    entitiesHint.hidden = true;
    addEntityButton.hidden = !can(org, "edit_reference");
    await entityTable.setData(await api.get(`/api/organizations/${org.id}/legal-entities`));
  }

  function editOrganization(org) {
    openForm({
      title: org ? "Организация" : "Новая организация",
      fields: org ? [...ORG_FIELDS, { name: "is_active", label: "Активна", type: "checkbox" }] : ORG_FIELDS,
      values: org ?? { kind: "group" },
      onSubmit: async (values) => {
        const saved = org
          ? await api.patch(`/api/organizations/${org.id}`, values)
          : await api.post("/api/organizations", values);
        selected = saved;
        toast("Сохранено");
        await loadOrganizations();
        await selectOrganization(saved);
      },
    });
  }

  function editEntity(entity) {
    openForm({
      title: entity ? "Юрлицо" : `Новое юрлицо: ${selected.name}`,
      fields: entity ? [...ENTITY_FIELDS, { name: "is_active", label: "Активно", type: "checkbox" }] : ENTITY_FIELDS,
      values: entity ?? {},
      onSubmit: async (values) => {
        const body = { ...values, kpp: orNull(values.kpp), ogrn: orNull(values.ogrn) };
        if (entity) await api.patch(`/api/legal-entities/${entity.id}`, body);
        else await api.post(`/api/organizations/${selected.id}/legal-entities`, body);
        toast("Сохранено");
        await selectOrganization(selected);
      },
    });
  }

  await Promise.all([orgTable.ready, entityTable.ready]);
  await loadOrganizations();
}

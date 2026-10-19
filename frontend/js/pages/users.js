import { api } from "../api.js";
import { editButton, el, makeTable, openForm, toast, yesNo } from "../ui.js";

// Таблица «организация → роль» внутри формы пользователя. Руководителю ЦФО выбирается его ЦФО (ветка).
function rolesEditor(organizations, roles, memberships) {
  const current = Object.fromEntries(memberships.map((m) => [m.organization_id, m]));
  const options = [["", "— нет доступа —"], ...roles.map((r) => [r.code, r.title])];
  const rows = organizations.map((org) => {
    const select = el("select", { "data-org": org.id, "aria-label": `Роль в ${org.name}` });
    for (const [code, title] of options) {
      select.append(el("option", { value: code, selected: code === (current[org.id]?.role ?? "") }, title));
    }
    const cfoSelect = el("select", { "data-cfo": org.id, "aria-label": `ЦФО в ${org.name}`, hidden: true });
    let loaded = false;
    async function syncCfo() {
      cfoSelect.hidden = select.value !== "cfo_head";
      if (cfoSelect.hidden || loaded) return;
      loaded = true;
      const cfos = await api.get(`/api/organizations/${org.id}/cfos`);
      const names = new Map(cfos.map((c) => [String(c.id), c.name]));
      const label = (c) => c.path.split(".").map((id) => names.get(id)).join(" / ");
      cfoSelect.replaceChildren(
        el("option", { value: "" }, cfos.length ? "— выберите ЦФО —" : "— в организации нет ЦФО —"),
        ...cfos.filter((c) => c.is_active).sort((a, b) => label(a).localeCompare(label(b)))
          .map((c) => el("option", { value: c.id, selected: c.id === current[org.id]?.cfo_id }, label(c))),
      );
    }
    select.addEventListener("change", syncCfo);
    syncCfo();
    return el("tr", {}, el("td", {}, org.name, org.is_active ? "" : " (архив)"), el("td", {}, select, cfoSelect));
  });
  const box = el(
    "fieldset",
    { class: "roles" },
    el("legend", {}, "Роли по организациям"),
    organizations.length
      ? el("table", {}, el("tbody", {}, rows))
      : el("p", { class: "muted" }, "Организаций пока нет."),
    el("p", { class: "muted" }, "Руководитель ЦФО видит и вводит данные только по выбранному ЦФО и его подчинённым."),
  );
  box.collect = () =>
    [...box.querySelectorAll("select[data-org]")]
      .filter((s) => s.value)
      .map((s) => {
        const cfo = box.querySelector(`select[data-cfo="${s.dataset.org}"]`).value;
        return { organization_id: Number(s.dataset.org), role: s.value, cfo_id: s.value === "cfo_head" && cfo ? Number(cfo) : null };
      });
  return box;
}

export async function renderUsers(view, { me }) {
  if (!me.is_admin) {
    view.replaceChildren(el("p", { class: "error" }, "Раздел доступен только администратору."));
    return;
  }
  const [roles, organizations] = await Promise.all([
    api.get("/api/roles"),
    api.get("/api/organizations"),
  ]);
  const roleTitles = Object.fromEntries(roles.map((r) => [r.code, r.title]));

  const tableBox = el("div");
  view.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("h1", {}, "Пользователи"),
      el("button", { type: "button", onclick: () => editUser(null) }, "Добавить пользователя"),
    ),
    tableBox,
  );

  const table = makeTable(tableBox, {
    columns: [
      { title: "Логин", field: "login" },
      { title: "ФИО", field: "full_name", widthGrow: 2 },
      { title: "Администратор", field: "is_admin", formatter: yesNo, width: 130 },
      { title: "Активен", field: "is_active", formatter: yesNo, width: 100 },
      {
        title: "Роли",
        field: "memberships",
        widthGrow: 3,
        formatter: (cell) =>
          cell
            .getValue()
            .map((m) => `${m.organization_name}: ${roleTitles[m.role]}${m.cfo_name ? ` (${m.cfo_name})` : ""}`)
            .join("; ")
            .replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]),
      },
      editButton((row) => editUser(row)),
    ],
  });

  async function load() {
    await table.setData(await api.get("/api/users"));
  }

  function editUser(user) {
    const editor = rolesEditor(organizations, roles, user?.memberships ?? []);
    const fields = user
      ? [
          { name: "full_name", label: "ФИО" },
          { name: "password", label: "Новый пароль", type: "password", hint: "Оставьте пустым, чтобы не менять. Не короче 10 символов." },
          { name: "is_admin", label: "Администратор (все права во всех организациях)", type: "checkbox" },
          { name: "is_active", label: "Активен (может входить в систему)", type: "checkbox" },
        ]
      : [
          { name: "login", label: "Логин", required: true, hint: "Латинские буквы, цифры, точка, дефис" },
          { name: "full_name", label: "ФИО" },
          { name: "password", label: "Пароль", type: "password", required: true, hint: "Не короче 10 символов" },
          { name: "is_admin", label: "Администратор (все права во всех организациях)", type: "checkbox" },
        ];
    openForm({
      title: user ? `Пользователь ${user.login}` : "Новый пользователь",
      fields,
      values: user ?? {},
      extra: editor,
      onSubmit: async (values) => {
        const memberships = editor.collect();
        if (user) {
          const body = { full_name: values.full_name, is_admin: values.is_admin, is_active: values.is_active };
          if (values.password) body.password = values.password;
          await api.patch(`/api/users/${user.id}`, body);
          await api.put(`/api/users/${user.id}/memberships`, { memberships });
        } else {
          await api.post("/api/users", { ...values, memberships });
        }
        toast("Сохранено");
        await load();
      },
    });
  }

  await table.ready;
  await load();
}

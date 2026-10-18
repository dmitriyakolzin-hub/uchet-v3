import { api } from "../api.js";
import { editButton, el, makeTable, openForm, toast, yesNo } from "../ui.js";

// Таблица «организация → роль» внутри формы пользователя.
function rolesEditor(organizations, roles, memberships) {
  const current = Object.fromEntries(memberships.map((m) => [m.organization_id, m.role]));
  const options = [["", "— нет доступа —"], ...roles.map((r) => [r.code, r.title])];
  const rows = organizations.map((org) => {
    const select = el("select", { "data-org": org.id, "aria-label": `Роль в ${org.name}` });
    for (const [code, title] of options) {
      select.append(el("option", { value: code, selected: code === (current[org.id] ?? "") }, title));
    }
    return el("tr", {}, el("td", {}, org.name, org.is_active ? "" : " (архив)"), el("td", {}, select));
  });
  const box = el(
    "fieldset",
    { class: "roles" },
    el("legend", {}, "Роли по организациям"),
    organizations.length
      ? el("table", {}, el("tbody", {}, rows))
      : el("p", { class: "muted" }, "Организаций пока нет."),
  );
  box.collect = () =>
    [...box.querySelectorAll("select[data-org]")]
      .filter((s) => s.value)
      .map((s) => ({ organization_id: Number(s.dataset.org), role: s.value }));
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
            .map((m) => `${m.organization_name}: ${roleTitles[m.role]}`)
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

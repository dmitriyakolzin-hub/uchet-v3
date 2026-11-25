import { api } from "../api.js";
import { editButton, el, instruction, makeTable, openForm, toast, yesNo } from "../ui.js";

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
    // руководителю ЦФУ — его ЦФУ (отдел — вместе с исполнителями)
    const executorSelect = el("select", { "data-executor": org.id, "aria-label": `ЦФУ в ${org.name}`, hidden: true });
    let executorsLoaded = false;
    async function syncExecutor() {
      executorSelect.hidden = select.value !== "executor_head";
      if (executorSelect.hidden || executorsLoaded) return;
      executorsLoaded = true;
      const executors = await api.get(`/api/organizations/${org.id}/executors`);
      const names = new Map(executors.map((e) => [e.id, e.name]));
      const label = (e) => (e.parent_id ? `${names.get(e.parent_id)} / ${e.name}` : e.name);
      executorSelect.replaceChildren(
        el("option", { value: "" }, executors.length ? "— выберите ЦФУ —" : "— в организации нет ЦФУ —"),
        ...executors.filter((e) => e.is_active).sort((a, b) => label(a).localeCompare(label(b)))
          .map((e) => el("option", { value: e.id, selected: e.id === current[org.id]?.executor_id }, label(e))),
      );
    }
    select.addEventListener("change", syncCfo);
    select.addEventListener("change", syncExecutor);
    syncCfo();
    syncExecutor();
    // дополнительный просмотр поверх роли (22.6): все заявки, всё имущество — только смотреть
    const grant = (key, title) => {
      const box = el("input", { type: "checkbox", "data-grant": key, "data-org-grant": org.id, checked: Boolean(current[org.id]?.[key]),
        "aria-label": `${title} в ${org.name}` });
      return el("label", { class: "check small" }, box, ` ${title}`);
    };
    const grants = el("div", { class: "grants" }, grant("see_all_requests", "Видит все заявки"),
      grant("see_all_assets", "Видит всё имущество"));
    const syncGrants = () => { grants.hidden = !select.value; };
    select.addEventListener("change", syncGrants);
    syncGrants();
    return el("tr", {}, el("td", {}, org.name, org.is_active ? "" : " (архив)"), el("td", {}, select, cfoSelect, executorSelect, grants));
  });
  const box = el(
    "fieldset",
    { class: "roles" },
    el("legend", {}, "Роли по организациям"),
    organizations.length
      ? el("table", {}, el("tbody", {}, rows))
      : el("p", { class: "muted" }, "Организаций пока нет."),
    instruction("Руководитель ЦФО видит и вводит данные только по выбранному ЦФО и его подчинённым. "
      + "Руководитель ЦФУ видит только заявки своего ЦФУ. Финансовый менеджер — деньги, заявки и календарь, "
      + "без справочников, бюджета и пользователей. Отметки «Видит все заявки» и «Видит всё имущество» дают "
      + "просмотр всех заявок на оплату и на имущество / всего реестра имущества, перемещений и выбытия организации "
      + "сверх роли — без права согласовывать и править. Кому какую роль — памятка docs/ROLES.md."),
  );
  box.collect = () =>
    [...box.querySelectorAll("select[data-org]")]
      .filter((s) => s.value)
      .map((s) => {
        const cfo = box.querySelector(`select[data-cfo="${s.dataset.org}"]`).value;
        const executor = box.querySelector(`select[data-executor="${s.dataset.org}"]`).value;
        const grant = (key) => box.querySelector(`input[data-grant="${key}"][data-org-grant="${s.dataset.org}"]`).checked;
        return { organization_id: Number(s.dataset.org), role: s.value, cfo_id: s.value === "cfo_head" && cfo ? Number(cfo) : null,
          executor_id: s.value === "executor_head" && executor ? Number(executor) : null,
          see_all_requests: grant("see_all_requests"), see_all_assets: grant("see_all_assets") };
      });
  return box;
}

export async function renderUsers(view, { me }) {
  if (!me.can_manage) {
    view.replaceChildren(el("p", { class: "error" }, "Раздел доступен администратору и финансовому директору."));
    return;
  }
  const [roles, allOrganizations] = await Promise.all([
    api.get("/api/roles"),
    api.get("/api/organizations"),
  ]);
  // финансовый директор даёт роли только в своих организациях; роли в остальных у пользователя сохраняются
  const organizations = allOrganizations.filter((o) => me.is_admin || o.role === "finance_director");
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
            .map((m) => `${m.organization_name}: ${roleTitles[m.role]}${m.cfo_name ? ` (${m.cfo_name})` : ""}`
              + (m.see_all_requests ? " + все заявки" : "") + (m.see_all_assets ? " + всё имущество" : ""))
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
          ...(me.is_admin ? [{ name: "is_admin", label: "Администратор (все права во всех организациях)", type: "checkbox" }] : []),
          { name: "is_active", label: "Активен (может входить в систему)", type: "checkbox" },
        ]
      : [
          { name: "login", label: "Логин", required: true, hint: "Латинские буквы, цифры, точка, дефис" },
          { name: "full_name", label: "ФИО" },
          { name: "password", label: "Пароль", type: "password", required: true, hint: "Не короче 10 символов" },
          ...(me.is_admin ? [{ name: "is_admin", label: "Администратор (все права во всех организациях)", type: "checkbox" }] : []),
        ];
    openForm({
      title: user ? `Пользователь ${user.login}` : "Новый пользователь",
      fields,
      values: user ?? {},
      extra: editor,
      onSubmit: async (values) => {
        const memberships = editor.collect();
        if (user) {
          const body = { full_name: values.full_name, is_active: values.is_active };
          if (me.is_admin) body.is_admin = values.is_admin;
          if (values.password) body.password = values.password;
          await api.patch(`/api/users/${user.id}`, body);
          await api.put(`/api/users/${user.id}/memberships`, { memberships });
        } else {
          await api.post("/api/users", { ...values, is_admin: me.is_admin && Boolean(values.is_admin), memberships });
        }
        toast("Сохранено");
        await load();
      },
    });
  }

  await table.ready;
  await load();
}

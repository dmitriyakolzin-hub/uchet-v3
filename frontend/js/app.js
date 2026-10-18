import { api, clearSession, hasSession, login } from "./api.js";
import { renderAccounts } from "./pages/accounts.js";
import { renderBanks } from "./pages/banks.js";
import { renderCurrencies } from "./pages/currencies.js";
import { renderOrganizations } from "./pages/organizations.js";
import { renderPassword } from "./pages/password.js";
import { renderReferences } from "./pages/references.js";
import { renderUsers } from "./pages/users.js";
import { el } from "./ui.js";

const PAGES = {
  organizations: { title: "Организации", render: renderOrganizations },
  references: { title: "Справочники", render: renderReferences },
  accounts: { title: "План счетов", render: renderAccounts },
  currencies: { title: "Валюты", render: renderCurrencies },
  banks: { title: "Банки", render: renderBanks },
  users: { title: "Пользователи", render: renderUsers, adminOnly: true },
  password: { title: "Сменить пароль", render: renderPassword },
};
const DEFAULT_PAGE = "organizations";

const loginView = document.getElementById("login-view");
const appView = document.getElementById("app-view");
const nav = document.getElementById("nav");
const view = document.getElementById("view");
const form = document.getElementById("login-form");
const loginError = document.getElementById("login-error");

let me = null;

function showLogin() {
  me = null;
  appView.hidden = true;
  loginView.hidden = false;
  form.login.focus();
}

function buildNav() {
  nav.replaceChildren(
    ...Object.entries(PAGES)
      .filter(([, page]) => !page.adminOnly || me.is_admin)
      .map(([key, page]) => el("a", { href: `#/${key}`, "data-page": key }, page.title)),
  );
}

async function route() {
  if (!me) return;
  let key = location.hash.replace(/^#\//, "") || DEFAULT_PAGE;
  if (!PAGES[key] || (PAGES[key].adminOnly && !me.is_admin)) key = DEFAULT_PAGE;
  for (const link of nav.querySelectorAll("a")) {
    link.classList.toggle("active", link.dataset.page === key);
  }
  document.title = `${PAGES[key].title} — Учёт v3`;
  view.replaceChildren(el("p", { class: "muted" }, "Загрузка…"));
  try {
    await PAGES[key].render(view, { me });
  } catch (err) {
    if (err.status !== 401) view.replaceChildren(el("p", { class: "error" }, err.message));
  }
}

async function showApp() {
  try {
    me = await api.get("/api/auth/me");
  } catch {
    clearSession();
    showLogin();
    return;
  }
  document.getElementById("user-name").textContent = me.full_name || me.login;
  buildNav();
  loginView.hidden = true;
  appView.hidden = false;
  await route();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    await login(form.login.value, form.password.value);
    form.reset();
    await showApp();
  } catch (err) {
    loginError.textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

document.getElementById("logout").addEventListener("click", () => {
  clearSession();
  showLogin();
});

window.addEventListener("hashchange", route);
window.addEventListener("session-expired", showLogin);

if (hasSession()) showApp();
else showLogin();

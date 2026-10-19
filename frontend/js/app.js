import { api, clearSession, hasSession, login } from "./api.js";
import { renderAccounts } from "./pages/accounts.js";
import { renderArticles } from "./pages/articles.js";
import { renderCommon } from "./pages/common.js";
import { renderCurrencies } from "./pages/currencies.js";
import { renderJournal } from "./pages/journal.js";
import { renderMoney } from "./pages/money.js";
import { renderOpening } from "./pages/opening.js";
import { renderOrganizations } from "./pages/organizations.js";
import { renderPassword } from "./pages/password.js";
import { renderReferences } from "./pages/references.js";
import { renderUsers } from "./pages/users.js";
import { el } from "./ui.js";

const PAGES = {
  journal: { title: "Журнал операций", render: renderJournal },
  money: { title: "Деньги", render: renderMoney },
  opening: { title: "Начальные остатки", render: renderOpening },
  organizations: { title: "Организации", render: renderOrganizations },
  references: { title: "Справочники", render: renderReferences },
  accounts: { title: "План счетов", render: renderAccounts },
  articles: { title: "Статьи ДДС и ОПиУ", render: renderArticles },
  currencies: { title: "Валюты", render: renderCurrencies },
  common: { title: "Общие справочники", render: renderCommon },
  users: { title: "Пользователи", render: renderUsers, adminOnly: true },
  password: { title: "Сменить пароль", render: renderPassword },
};
const DEFAULT_PAGE = "journal";

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
  // Каждый переход рисует в свой контейнер: если пользователь ушёл со страницы, пока она грузилась,
  // запоздавшая отрисовка попадёт в отсоединённый контейнер и не затрёт новую страницу
  const container = el("div", {}, el("p", { class: "muted" }, "Загрузка…"));
  view.replaceChildren(container);
  try {
    await PAGES[key].render(container, { me });
  } catch (err) {
    if (err.status !== 401) container.replaceChildren(el("p", { class: "error" }, err.message));
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

(() => {
  const loginView = document.getElementById("login-view");
  const homeView = document.getElementById("home-view");
  const form = document.getElementById("login-form");
  const errorBox = document.getElementById("login-error");

  function showLogin() {
    homeView.hidden = true;
    loginView.hidden = false;
  }

  async function showHome() {
    try {
      const resp = await Api.request("/api/auth/me");
      const user = await resp.json();
      document.getElementById("user-name").textContent = user.full_name || user.login;
      loginView.hidden = true;
      homeView.hidden = false;
    } catch {
      Api.clear();
      showLogin();
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await Api.login(form.login.value, form.password.value);
      form.reset();
      await showHome();
    } catch (err) {
      errorBox.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });

  document.getElementById("logout").addEventListener("click", () => {
    Api.clear();
    showLogin();
  });

  if (Api.hasSession()) showHome();
  else showLogin();
})();

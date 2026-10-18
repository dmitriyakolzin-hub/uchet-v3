// Обращение к серверу: подставляет токен, при истечении один раз обновляет его.
const Api = (() => {
  const ACCESS = "uchet3_access";
  const REFRESH = "uchet3_refresh";

  function store(tokens) {
    localStorage.setItem(ACCESS, tokens.access_token);
    localStorage.setItem(REFRESH, tokens.refresh_token);
  }

  function clear() {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
  }

  function hasSession() {
    return Boolean(localStorage.getItem(REFRESH));
  }

  async function login(login, password) {
    const resp = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ login, password }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : "Не удалось войти");
    }
    store(await resp.json());
  }

  async function refresh() {
    const token = localStorage.getItem(REFRESH);
    if (!token) return false;
    const resp = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
    });
    if (!resp.ok) {
      clear();
      return false;
    }
    store(await resp.json());
    return true;
  }

  async function request(path, options = {}) {
    const send = () =>
      fetch(path, {
        ...options,
        headers: {
          ...(options.headers || {}),
          Authorization: `Bearer ${localStorage.getItem(ACCESS) || ""}`,
        },
      });
    let resp = await send();
    if (resp.status === 401 && (await refresh())) resp = await send();
    if (resp.status === 401) throw new Error("unauthorized");
    return resp;
  }

  return { login, request, clear, hasSession };
})();

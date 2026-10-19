// Обращение к серверу: подставляет токен, при истечении один раз обновляет его.
const ACCESS = "uchet3_access";
const REFRESH = "uchet3_refresh";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function store(tokens) {
  localStorage.setItem(ACCESS, tokens.access_token);
  localStorage.setItem(REFRESH, tokens.refresh_token);
}

export function clearSession() {
  localStorage.removeItem(ACCESS);
  localStorage.removeItem(REFRESH);
}

export function hasSession() {
  return Boolean(localStorage.getItem(REFRESH));
}

// Текст ошибки из ответа сервера: строка detail или список ошибок проверки полей.
async function errorMessage(resp) {
  const body = await resp.json().catch(() => ({}));
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((e) => `${(e.loc || []).filter((p) => p !== "body").join(".")}: ${e.msg}`)
      .join("; ");
  }
  return `Ошибка сервера (${resp.status})`;
}

export async function login(loginName, password) {
  const resp = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login: loginName, password }),
  });
  if (!resp.ok) throw new ApiError(resp.status, await errorMessage(resp));
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
    clearSession();
    return false;
  }
  store(await resp.json());
  return true;
}

async function request(method, path, body, raw = false, blob = false) {
  // raw — тело отправляется как есть (файл), без JSON
  const send = () =>
    fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${localStorage.getItem(ACCESS) || ""}`,
        ...(raw ? { "Content-Type": "application/octet-stream" } : body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: raw ? body : body !== undefined ? JSON.stringify(body) : undefined,
    });
  let resp = await send();
  if (resp.status === 401 && (await refresh())) resp = await send();
  if (resp.status === 401) {
    clearSession();
    window.dispatchEvent(new Event("session-expired"));
    throw new ApiError(401, "Требуется вход в систему");
  }
  if (!resp.ok) throw new ApiError(resp.status, await errorMessage(resp));
  if (resp.status === 204) return null;
  return blob ? resp.blob() : resp.json();
}

// Скачать файл с сервера (с токеном) и сохранить под именем fileName
async function download(path, fileName) {
  const data = await request("GET", path, undefined, false, true);
  const url = URL.createObjectURL(data);
  const link = Object.assign(document.createElement("a"), { href: url, download: fileName });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  patch: (path, body) => request("PATCH", path, body),
  put: (path, body) => request("PUT", path, body),
  delete: (path) => request("DELETE", path),
  upload: (path, file) => request("POST", path, file, true),
  download,
  storeTokens: store,
};

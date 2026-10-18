import { api } from "../api.js";
import { el, toast } from "../ui.js";

export function renderPassword(view) {
  const error = el("p", { class: "error", role: "alert" });
  const form = el(
    "form",
    { class: "form narrow" },
    el("label", { for: "cur" }, "Текущий пароль"),
    el("input", { id: "cur", name: "current", type: "password", required: true, autocomplete: "current-password" }),
    el("label", { for: "new1" }, "Новый пароль"),
    el("input", { id: "new1", name: "new1", type: "password", required: true, autocomplete: "new-password" }),
    el("small", { class: "muted" }, "Не короче 10 символов. Вход на других устройствах будет завершён."),
    el("label", { for: "new2" }, "Повторите новый пароль"),
    el("input", { id: "new2", name: "new2", type: "password", required: true, autocomplete: "new-password" }),
    error,
    el("div", { class: "form-actions" }, el("button", { type: "submit" }, "Сменить пароль")),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    if (form.new1.value !== form.new2.value) {
      error.textContent = "Новые пароли не совпадают";
      return;
    }
    try {
      const tokens = await api.post("/api/auth/change-password", {
        current_password: form.current.value,
        new_password: form.new1.value,
      });
      api.storeTokens(tokens);
      form.reset();
      toast("Пароль изменён");
    } catch (err) {
      error.textContent = err.message;
    }
  });
  view.replaceChildren(el("h1", {}, "Смена пароля"), form);
}

// Выбор нескольких значений в фильтре (docs/TZ.md, раздел 25): кнопка с подписью и выпадающий список с флажками
// и поиском. Ничего не выбрано — «все». onChange вызывается после выбора (с небольшой задержкой).
import { el } from "./ui.js";

export function multiSelect({ label, allLabel, onChange }) {
  let options = []; // [[значение, подпись]]
  const chosen = new Set();
  const button = el("button", { type: "button", class: "ms-button secondary", "aria-label": label, "aria-haspopup": "true" });
  const search = el("input", { type: "search", placeholder: "Поиск", "aria-label": `${label}: поиск` });
  const list = el("div", { class: "ms-list" });
  const clear = el("button", { type: "button", class: "link" }, "Очистить");
  const panel = el("div", { class: "ms-panel", hidden: true }, search, el("div", { class: "ms-actions" }, clear), list);
  const node = el("div", { class: "ms" }, button, panel);
  let timer = null;

  const changed = () => {
    renderButton();
    clearTimeout(timer);
    timer = setTimeout(() => onChange?.(), 300);
  };
  function renderButton() {
    const titles = options.filter(([v]) => chosen.has(v)).map(([, t]) => t);
    button.textContent = !titles.length ? allLabel : titles.length === 1 ? titles[0] : `${label}: ${titles.length}`;
    button.title = titles.join("\n");
    button.classList.toggle("ms-active", titles.length > 0);
  }
  function renderList() {
    const q = search.value.trim().toLowerCase();
    const shown = options.filter(([, t]) => !q || t.toLowerCase().includes(q));
    list.replaceChildren(...shown.slice(0, 300).map(([v, t]) => {
      const box = el("input", { type: "checkbox", checked: chosen.has(v) });
      box.addEventListener("change", () => {
        if (box.checked) chosen.add(v); else chosen.delete(v);
        changed();
      });
      return el("label", { class: "check ms-item" }, box, " ", t);
    }), ...(shown.length > 300 ? [el("p", { class: "muted small" }, `ещё ${shown.length - 300} — уточните поиск`)] : []),
    ...(shown.length ? [] : [el("p", { class: "muted small" }, "Ничего не найдено")]));
  }
  search.addEventListener("input", renderList);
  clear.addEventListener("click", () => {
    chosen.clear();
    renderList();
    changed();
  });
  button.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
      renderList();
      search.focus();
    }
  });
  document.addEventListener("click", (e) => {
    if (!node.isConnected) return;
    if (!node.contains(e.target)) panel.hidden = true;
  });
  node.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      panel.hidden = true;
      button.focus();
    }
  });
  renderButton();

  return {
    node,
    values: () => [...chosen],
    setOptions(items) {
      options = items.map(([v, t]) => [String(v), t]);
      const known = new Set(options.map(([v]) => v));
      for (const v of [...chosen]) if (!known.has(v)) chosen.delete(v);
      renderButton();
      if (!panel.hidden) renderList();
    },
  };
}

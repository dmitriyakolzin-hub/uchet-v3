// «Загрузка из Excel»: операции по шаблону (строки → черновики) и список контрагентов.
import { api } from "../api.js";
import { el, toast } from "../ui.js";

const ORG_KEY = "uchet3_org";

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

const KINDS = {
  operations: {
    title: "Операции",
    text: "Одна строка — одна проводка; строки с одинаковым значением в колонке «Операция» станут одной операцией. "
      + "Все операции загружаются черновиками — проверьте и проведите их в «Журнале операций».",
    perm: "draft",
    template: "operations.xlsx",
  },
  counterparties: {
    title: "Контрагенты",
    text: "Одна строка — один контрагент. Контрагенты с ИНН, который уже есть в справочнике, пропускаются.",
    perm: "edit_reference",
    template: "counterparties.xlsx",
  },
};

export async function renderImport(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Загрузка из Excel"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const cards = el("div", { class: "import-cards" });

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Загрузка из Excel"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    el("p", { class: "muted" }, "Скачайте шаблон, заполните его и загрузите. Повторная загрузка того же файла не создаёт дублей. "
      + "Банковские выписки загружаются на странице «Деньги»."),
    cards,
  );

  function card(kind) {
    const k = KINDS[kind];
    const fileInput = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": `Файл: ${k.title}` });
    fileInput.addEventListener("change", () => upload(kind, fileInput.files[0], fileInput));
    const allowed = org.permissions.includes(k.perm);
    return el("section", { class: "check-box import-card" },
      el("h2", {}, k.title),
      el("p", {}, k.text),
      allowed
        ? el("div", { class: "quick-actions" },
          el("button", { type: "button", class: "secondary", onclick: () => downloadTemplate(k) }, "Скачать шаблон"),
          el("button", { type: "button", onclick: () => { fileInput.value = ""; fileInput.click(); } }, "Загрузить файл"),
          fileInput)
        : el("p", { class: "muted" }, "Нет прав на загрузку в этой организации."),
    );
  }

  async function downloadTemplate(k) {
    try {
      await api.download(`/api/organizations/${org.id}/import/templates/${k.template}`, k.template);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function upload(kind, file, input) {
    if (!file) return;
    input.value = ""; // чтобы тот же файл можно было выбрать снова
    toast(`Загружается ${file.name}…`);
    try {
      const result = await api.upload(`/api/organizations/${org.id}/import/${kind}`, file);
      showResult(kind, file.name, result);
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function showResult(kind, fileName, r) {
    const lines = kind === "operations"
      ? [
        `Строк в файле: ${r.rows}`,
        `Создано черновиков: ${r.created.length}${r.created.length ? ` — № ${r.created.join(", ")}` : ""}`,
        r.incomplete.length ? `Из них неполных (нужно дополнить в журнале): ${r.incomplete.length} — № ${r.incomplete.join(", ")}` : null,
        r.skipped_duplicates ? `Пропущено — уже были загружены раньше: ${r.skipped_duplicates}` : null,
      ]
      : [
        `Строк в файле: ${r.rows}`,
        `Добавлено контрагентов: ${r.created.length}${r.created.length ? ` — ${r.created.join(", ")}` : ""}`,
        r.skipped_existing ? `Пропущено — уже есть в справочнике: ${r.skipped_existing}` : null,
      ];
    const errors = r.errors.length
      ? [el("h3", {}, `Не загружено строк: ${r.errors.length}`),
        el("ul", { class: "error-list" }, ...r.errors.map((e) => el("li", {}, `Строка ${e.row}: ${e.error}`))),
        el("p", { class: "muted" }, "Исправьте эти строки в файле и загрузите его снова — уже загруженное не задвоится.")]
      : [];
    const dialog = el(
      "dialog",
      { class: "dialog wide" },
      el("h2", {}, `Файл загружен: ${fileName}`),
      el("ul", {}, ...lines.filter(Boolean).map((l) => el("li", {}, l))),
      ...errors,
      el("div", { class: "form-actions" },
        kind === "operations" && r.created.length
          ? el("button", { type: "button", class: "secondary", onclick: () => { dialog.close(); location.hash = "#journal"; } }, "Открыть журнал")
          : null,
        el("button", { type: "button", onclick: () => dialog.close() }, "Понятно")),
    );
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  function render() {
    cards.replaceChildren(card("operations"), card("counterparties"));
  }

  orgSelect.addEventListener("change", () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    render();
  });
  render();
}

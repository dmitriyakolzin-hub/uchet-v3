// Внутренние передачи между направлениями по количеству (docs/TZ.md, раздел 29, «Детали 20.4»): список, документ
// с товарами (сумма = количество × внутренняя цена без НДС), отмена, загрузка из Excel, передачи по перемещениям журнала.
import { api } from "./api.js";
import { date as ruDate, isoDate, money, monthBounds, parseNumber } from "./format.js";
import { el, toast } from "./ui.js";

const STATUS = { posted: "Проведена", cancelled: "Отменена" };

function dialogBox(title, wide = true) {
  const dialog = el("dialog", { class: `dialog${wide ? " wide" : ""}` });
  document.body.append(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.append(el("h2", {}, title));
  dialog.showModal();
  return dialog;
}

function cfoOptions(ref, empty) {
  return [el("option", { value: "" }, empty), ...[...ref.cfos]
    .sort((a, b) => ref.cfoLabel.get(a.id).localeCompare(ref.cfoLabel.get(b.id)))
    .map((c) => el("option", { value: c.id }, ref.cfoLabel.get(c.id)))];
}

/** Окно «Внутренние передачи» — из журнала. canPost — право проводить. */
export function openTransfers({ org, ref, canPost, onChanged }) {
  const [start, end] = monthBounds();
  const from = el("input", { type: "date", value: start, "aria-label": "Передачи с" });
  const to = el("input", { type: "date", value: end, "aria-label": "Передачи по" });
  const listBox = el("div");
  const fileInput = el("input", { type: "file", accept: ".xlsx", hidden: true, "aria-label": "Файл передач" });
  const dialog = dialogBox("Внутренние передачи между направлениями");
  dialog.append(
    el("p", { class: "muted" }, "Передача — товары с количеством; сумма — количество × внутренняя цена без НДС. Вид цены — "
      + "по звену того, кто передаёт (производство → цена производства, логистика → логистики, торговля → дисконта), "
      + "цена — для ЦФО того, кто получает. Проведённая передача — операция в журнале: внутренняя выручка у передающего, "
      + "внутренняя закупка у получающего; у компании в целом — ноль."),
    el("div", { class: "filters" }, from, to,
      canPost ? el("button", { type: "button", onclick: () => transferForm() }, "+ Передача") : "",
      canPost ? el("button", { type: "button", class: "secondary", onclick: () => fromJournal() }, "Найти в журнале") : "",
      canPost ? el("button", { type: "button", class: "secondary", onclick: () => api.download(
        `/api/organizations/${org.id}/import/templates/internal-transfers.xlsx`, "internal_transfers.xlsx")
        .catch((err) => toast(err.message, "error")) }, "Шаблон Excel") : "",
      canPost ? el("button", { type: "button", class: "secondary", onclick: () => fileInput.click() }, "Загрузить из Excel") : "",
      fileInput),
    listBox,
    el("div", { class: "form-actions" }, el("button", { type: "button", class: "secondary", onclick: () => dialog.close() }, "Закрыть")),
  );
  from.addEventListener("change", load);
  to.addEventListener("change", load);
  const changed = async () => { await load(); await onChanged?.(); };

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    fileInput.value = "";
    try {
      const r = await api.upload(`/api/organizations/${org.id}/import/internal-transfers`, file);
      const box = dialogBox(`Передачи загружены: ${file.name}`);
      box.append(el("ul", {}, el("li", {}, `Строк в файле: ${r.rows}`), el("li", {}, `Проведено передач: ${r.created.length}`)),
        ...(r.errors.length ? [el("h3", {}, `Не загружено строк: ${r.errors.length}`),
          el("ul", { class: "error-list" }, ...r.errors.map((e) => el("li", {}, `Строка ${e.row}: ${e.error}`)))] : []),
        el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => box.close() }, "Понятно")));
      await changed();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  async function load() {
    let rows;
    try {
      rows = await api.get(`/api/organizations/${org.id}/internal-transfers?date_from=${from.value}&date_to=${to.value}&with_cancelled=true`);
    } catch (err) {
      listBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    listBox.replaceChildren(rows.length ? el("table", { class: "recon internal-transfers" },
      el("thead", {}, el("tr", {}, ...["№", "Дата", "Откуда → куда", "Сумма", "Источник", "Операция", "Статус", ""].map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows.map((d) => el("tr", { "data-number": d.transfer_number },
        el("td", {}, String(d.transfer_number)),
        el("td", {}, ruDate(d.on_date)),
        el("td", {}, `${d.from_name} → ${d.to_name}`, d.description ? el("div", { class: "muted small" }, d.description) : ""),
        el("td", { class: "num" }, money(d.total)),
        el("td", {}, d.source_title, d.source_operation_number ? el("div", { class: "muted small" }, `перемещение № ${d.source_operation_number}`) : ""),
        el("td", {}, d.number ? `№ ${d.number}` : ""),
        el("td", {}, STATUS[d.status]),
        el("td", { class: "req-actions" },
          el("button", { type: "button", class: "link", onclick: () => view(d.id) }, "Строки"),
          canPost && d.status === "posted" ? el("button", { type: "button", class: "link danger", onclick: async () => {
            if (!confirm(`Отменить передачу № ${d.transfer_number}? Её операция в журнале удалится.`)) return;
            try { await api.post(`/api/internal-transfers/${d.id}/cancel`); await changed(); } catch (err) { toast(err.message, "error"); }
          } }, "Отменить") : "")))))
      : el("p", { class: "muted" }, "Передач за период нет."));
  }

  async function view(id) {
    const d = await api.get(`/api/internal-transfers/${id}`);
    const box = dialogBox(`Передача № ${d.transfer_number} от ${ruDate(d.on_date)}: ${d.from_name} → ${d.to_name}`);
    box.append(linesTable(d.lines), el("p", { class: "strong" }, `Итого: ${money(d.total)} ₽`),
      ...(d.materials?.length ? [el("h3", {}, "Списано по техкарте (себестоимость производства)"),
        el("ul", {}, ...d.materials.map((m) => el("li", {}, `${m.title} × ${Number(m.quantity).toLocaleString("ru-RU")} — ${money(m.amount)} ₽`)))] : []),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => box.close() }, "Закрыть")));
  }

  // документ с товарами: строки — товар + количество или сумма вручную; «Посчитать» — суммы по ценам
  function transferForm() {
    const box = dialogBox("Новая внутренняя передача");
    const onDate = el("input", { type: "date", value: isoDate(new Date()), "aria-label": "Дата передачи" });
    const fromCfo = el("select", { "aria-label": "Кто передаёт" }, ...cfoOptions(ref, "— кто передаёт —"));
    const toCfo = el("select", { "aria-label": "Кто получает" }, ...cfoOptions(ref, "— кто получает —"));
    const description = el("input", { type: "text", placeholder: "Описание (необязательно)", "aria-label": "Описание передачи" });
    const lines = el("tbody");
    const result = el("div");
    const goods = [...ref.nomenclature].sort((a, b) => a.name.localeCompare(b.name));
    const addLine = () => {
      const item = el("select", { "aria-label": "Товар" }, el("option", { value: "" }, "— товар (или сумма вручную) —"),
        ...goods.map((n) => el("option", { value: n.id }, n.article ? `${n.article} ${n.name}` : n.name)));
      const quantity = el("input", { type: "text", inputmode: "decimal", placeholder: "кол-во", "aria-label": "Количество", size: 8 });
      const amount = el("input", { type: "text", inputmode: "decimal", placeholder: "сумма вручную", "aria-label": "Сумма вручную", size: 10 });
      const title = el("input", { type: "text", placeholder: "что (если без товара)", "aria-label": "Что передаётся" });
      const tr = el("tr", {}, el("td", {}, item, title), el("td", {}, quantity), el("td", {}, amount),
        el("td", {}, el("button", { type: "button", class: "link danger", onclick: () => tr.remove() }, "✕")));
      tr.read = () => ({ nomenclature_id: item.value ? Number(item.value) : null, quantity: parseNumber(quantity.value),
        amount: parseNumber(amount.value), title: title.value.trim() });
      lines.append(tr);
    };
    addLine();
    const body = () => ({ on_date: onDate.value, from_cfo_id: Number(fromCfo.value), to_cfo_id: Number(toCfo.value),
      description: description.value.trim(),
      lines: [...lines.children].map((tr) => tr.read()).filter((l) => l.nomenclature_id || l.amount) });
    const check = () => {
      if (!fromCfo.value || !toCfo.value) { toast("Выберите, кто передаёт и кто получает", "error"); return false; }
      if (!body().lines.length) { toast("Добавьте товар с количеством или сумму", "error"); return false; }
      return true;
    };
    box.append(
      el("div", { class: "filters" }, onDate, fromCfo, toCfo),
      el("div", { class: "filters" }, description),
      el("table", { class: "recon transfer-lines" }, el("thead", {}, el("tr", {}, ...["Товар", "Количество", "Сумма вручную", ""].map((t) => el("th", {}, t)))), lines),
      el("div", { class: "filters" }, el("button", { type: "button", class: "link", onclick: addLine }, "+ строка")),
      result,
      el("div", { class: "form-actions" },
        el("button", { type: "button", class: "secondary", onclick: async () => {
          if (!check()) return;
          try {
            const p = await api.post(`/api/organizations/${org.id}/internal-transfers/preview`, body());
            result.replaceChildren(linesTable(p.lines), el("p", { class: "strong" }, `Итого: ${money(p.total)} ₽`));
          } catch (err) {
            result.replaceChildren(el("p", { class: "error" }, err.message));
          }
        } }, "Посчитать"),
        el("button", { type: "button", onclick: async () => {
          if (!check()) return;
          try {
            const d = await api.post(`/api/organizations/${org.id}/internal-transfers`, body());
            toast(`Передача № ${d.transfer_number} проведена (операция № ${d.number}), ${money(d.total)} ₽`);
            box.close();
            await changed();
          } catch (err) {
            result.replaceChildren(el("p", { class: "error" }, err.message));
          }
        } }, "Провести"),
        el("button", { type: "button", class: "secondary", onclick: () => box.close() }, "Отмена")),
    );
  }

  // перемещения товара между складами разных направлений в журнале → передачи
  async function fromJournal() {
    const period = `date_from=${from.value}&date_to=${to.value}`;
    let props;
    try {
      props = await api.get(`/api/organizations/${org.id}/internal-transfers/journal?${period}`);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const box = dialogBox(`Перемещения в журнале за ${ruDate(from.value)} — ${ruDate(to.value)}`);
    if (!props.length) {
      box.append(el("p", { class: "muted" }, "Перемещений между складами разных направлений без передачи нет. Склад относится "
        + "к направлению через свой ЦФО (у ЦФО должно быть «Звено»)."),
      el("div", { class: "form-actions" }, el("button", { type: "button", onclick: () => box.close() }, "Закрыть")));
      return;
    }
    const checks = props.map((p) => el("input", { type: "checkbox", checked: !p.error, disabled: !!p.error,
      "aria-label": `Перемещение № ${p.operation_number}` }));
    box.append(el("table", { class: "recon journal-moves" },
      el("thead", {}, el("tr", {}, ...["", "Операция", "Дата", "Откуда → куда", "Товары", "Сумма"].map((t) => el("th", {}, t)))),
      el("tbody", {}, ...props.map((p, i) => el("tr", {},
        el("td", {}, checks[i]),
        el("td", {}, `№ ${p.operation_number}`),
        el("td", {}, ruDate(p.on_date)),
        el("td", {}, `${p.from_name} → ${p.to_name}`),
        el("td", {}, p.error ? el("span", { class: "error" }, p.error)
          : p.lines.map((l) => `${l.title} × ${l.quantity}`).join(", ")),
        el("td", { class: "num" }, p.total ? money(p.total) : ""))))),
    el("div", { class: "form-actions" },
      el("button", { type: "button", onclick: async () => {
        const ids = props.filter((_, i) => checks[i].checked).map((p) => p.operation_id);
        if (!ids.length) { toast("Отметьте перемещения", "error"); return; }
        try {
          const r = await api.post(`/api/organizations/${org.id}/internal-transfers/from-journal`,
            { operation_ids: ids, date_from: from.value, date_to: to.value });
          toast(`Создано передач: ${r.created.length}${r.errors.length ? `; не создано: ${r.errors.join("; ")}` : ""}`,
            r.errors.length ? "error" : undefined);
          box.close();
          await changed();
        } catch (err) {
          toast(err.message, "error");
        }
      } }, "Создать передачи"),
      el("button", { type: "button", class: "secondary", onclick: () => box.close() }, "Закрыть")));
  }

  load();
}

function linesTable(lines) {
  return el("table", { class: "recon" },
    el("thead", {}, el("tr", {}, ...["Что", "Количество", "Цена без НДС", "Сумма"].map((t) => el("th", {}, t)))),
    el("tbody", {}, ...lines.map((l) => el("tr", {},
      el("td", {}, l.title, el("div", { class: "muted small" }, l.price_title)),
      el("td", { class: "num" }, l.quantity ?? ""),
      el("td", { class: "num" }, l.price ? money(l.price) : ""),
      el("td", { class: "num" }, money(l.amount))))));
}

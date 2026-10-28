// Разделение проводки на части по разным ЦФО (docs/TZ.md, раздел 21, «Детали 11.3»): суммами или процентами,
// остаток до копейки — в последнюю часть. Деньги считаются в копейках (BigInt), без float.
import { fromKopecks, money, parseNumber, toKopecks } from "./format.js";
import { el, toast } from "./ui.js";

const MAX_PARTS = 10;

// «33,33» → сотые доли процента (3333n); не число — null
function hundredths(text) {
  const clean = parseNumber(text);
  if (clean === null || !/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  return toKopecks(clean);
}

/**
 * amount — сумма проводки ("1234.50"); current — {cfo, executor} проводки (станут первой частью);
 * onApply([{cfo, executor, amount}]) — части, сумма частей = amount
 */
export function openCfoSplit({ ref, amount, current = {}, onApply }) {
  const total = toKopecks(amount);
  let mode = "amount";
  const rows = el("div", { class: "split-rows" });
  const summary = el("p", { class: "muted", role: "status" });
  const apply = el("button", { type: "button" }, "Разделить");

  const cfoOptions = [["", "— ЦФО —"], ...ref.cfos.map((c) => [String(c.id), ref.cfoLabel.get(c.id)])];
  const executorOptions = [["", "— без ЦФУ —"], ...ref.executors.map((e) => [String(e.id), ref.executorLabel.get(e.id)])
    .sort((a, b) => a[1].localeCompare(b[1]))];
  const select = (options, value, label) => {
    const node = el("select", { "aria-label": label });
    for (const [v, title] of options) node.append(el("option", { value: v, selected: String(value ?? "") === v }, title));
    return node;
  };

  function addRow(values = {}) {
    if (rows.children.length >= MAX_PARTS) return;
    const n = rows.children.length + 1;
    const value = el("input", { inputmode: "decimal", "aria-label": `Сумма части ${n}`, class: "split-value" });
    const row = el("div", { class: "split-row" },
      select(cfoOptions, values.cfo, `ЦФО части ${n}`), select(executorOptions, values.executor, `ЦФУ части ${n}`),
      value, el("span", { class: "split-amount muted" }),
      el("button", { type: "button", class: "link", onclick: () => { if (rows.children.length > 2) { row.remove(); update(); } } }, "убрать"));
    value.addEventListener("input", update);
    rows.append(row);
    update();
  }

  // Суммы частей в копейках; последняя — остаток. Ошибка — строка с причиной
  function compute() {
    const parts = [...rows.children];
    const amounts = [];
    let used = 0n;
    for (const [i, row] of parts.entries()) {
      if (i === parts.length - 1) break;
      const input = row.querySelector(".split-value");
      if (mode === "amount") {
        const clean = parseNumber(input.value);
        if (clean === null || !/^\d+(\.\d{1,2})?$/.test(clean)) return { error: `Часть ${i + 1}: укажите сумму` };
        amounts.push(toKopecks(clean));
      } else {
        const pct = hundredths(input.value);
        if (pct === null) return { error: `Часть ${i + 1}: укажите процент` };
        amounts.push((total * pct + 5000n) / 10000n); // округление до копейки (половина — вверх)
      }
      used += amounts[amounts.length - 1];
    }
    const rest = total - used;
    if (amounts.some((a) => a <= 0n)) return { error: "Каждая часть должна быть больше нуля" };
    if (rest <= 0n) return { error: `Части больше суммы проводки (${money(fromKopecks(total))})` };
    return { amounts: [...amounts, rest] };
  }

  function update() {
    const parts = [...rows.children];
    parts.forEach((row, i) => {
      const input = row.querySelector(".split-value");
      const last = i === parts.length - 1;
      input.style.visibility = last ? "hidden" : "";
      input.placeholder = mode === "amount" ? "0,00" : "%";
    });
    const result = compute();
    parts.forEach((row, i) => {
      row.querySelector(".split-amount").textContent = result.amounts
        ? (i === parts.length - 1 ? `остаток ${money(fromKopecks(result.amounts[i]))}` : money(fromKopecks(result.amounts[i])))
        : (i === parts.length - 1 ? "остаток" : "");
    });
    summary.textContent = result.error ?? `Сумма проводки ${money(fromKopecks(total))} делится на ${parts.length} части.`;
    summary.classList.toggle("error", Boolean(result.error));
    apply.disabled = Boolean(result.error);
  }

  const modeSelect = select([["amount", "суммами"], ["percent", "процентами"]], mode, "Как делить");
  modeSelect.addEventListener("change", () => {
    mode = modeSelect.value;
    for (const input of rows.querySelectorAll(".split-value")) input.value = "";
    update();
  });

  apply.addEventListener("click", () => {
    const result = compute();
    if (result.error) return;
    const parts = [...rows.children].map((row, i) => {
      const [cfo, executor] = row.querySelectorAll("select");
      return { cfo: Number(cfo.value) || null, executor: Number(executor.value) || null, amount: fromKopecks(result.amounts[i]) };
    });
    onApply(parts);
    box.close();
    toast(`Проводка разделена на ${parts.length} части`);
  });

  const box = el("dialog", { class: "dialog wide" },
    el("h2", {}, "Разделить по ЦФО"),
    el("p", { class: "muted" }, "Проводка заменится несколькими: те же счета и аналитика, свой ЦФО и сумма у каждой части. "
      + "Последняя часть получает остаток — до копейки."),
    el("label", { class: "block" }, "Делить", modeSelect),
    rows,
    el("button", { type: "button", class: "link", onclick: () => addRow() }, "+ часть"),
    summary,
    el("div", { class: "form-actions" },
      el("button", { type: "button", class: "secondary", onclick: () => box.close() }, "Отмена"), apply));
  box.addEventListener("close", () => box.remove());
  document.body.append(box);
  addRow(current);
  addRow();
  box.showModal();
}

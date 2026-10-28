// Форматы для экрана: деньги, количество, даты. Расчёты с деньгами — только на сервере.

// "1234567.5" → "1 234 567,50"
export function money(value) {
  if (value === null || value === undefined || value === "") return "";
  const [whole, frac = ""] = String(value).split(".");
  const sign = whole.startsWith("-") ? "-" : "";
  const digits = whole.replace("-", "").replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return `${sign}${digits},${frac.padEnd(2, "0").slice(0, 2)}`;
}

// "10.500" → "10,5"
export function quantity(value) {
  if (value === null || value === undefined || value === "") return "";
  return String(value).replace(/\.?0+$/, "").replace(".", ",");
}

// Ввод пользователя «1 234,5» → «1234.5» для сервера; пустое → null
export function parseNumber(text) {
  const clean = String(text ?? "").replace(/[\s ]/g, "").replace(",", ".");
  return clean === "" ? null : clean;
}

// "2026-10-05" → "05.10.2026"
export function date(iso) {
  return iso ? iso.split("-").reverse().join(".") : "";
}

export function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function monthBounds(d = new Date()) {
  return [isoDate(new Date(d.getFullYear(), d.getMonth(), 1)), isoDate(new Date(d.getFullYear(), d.getMonth() + 1, 0))];
}

// Денежная строка ("1234.50") ↔ копейки (BigInt), без float
export function toKopecks(value) {
  const negative = String(value).startsWith("-");
  const [whole, frac = ""] = String(value).replace("-", "").split(".");
  const k = BigInt(whole || "0") * 100n + BigInt((frac + "00").slice(0, 2));
  return negative ? -k : k;
}

export function fromKopecks(kopecks) {
  const negative = kopecks < 0n;
  const abs = negative ? -kopecks : kopecks;
  return `${negative ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}

// Точная сумма денежных строк в копейках → "12345.67"
export function sumMoney(values) {
  let kopecks = 0n;
  for (const v of values) {
    if (v === null || v === undefined || v === "") continue;
    kopecks += toKopecks(v);
  }
  return fromKopecks(kopecks);
}

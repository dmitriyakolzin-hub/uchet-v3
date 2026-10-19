// «Отчёты»: общие фильтры и вкладки отчётов. Каждый отчёт раскрывается до проводок.
import { api } from "../api.js";
import { ddsReport } from "../reports/dds.js";
import { osvReport } from "../reports/osv.js";
import { plReport } from "../reports/pl.js";
import { reportFilters } from "../reports/common.js";
import { el, toast } from "../ui.js";
import { loadReferences } from "./journal.js";

const ORG_KEY = "uchet3_org";
const TAB_KEY = "uchet3_report";

function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* не страшно */ }
}

const REPORTS = {
  osv: { title: "ОСВ", make: osvReport },
  dds: { title: "ДДС", make: ddsReport },
  pl: { title: "ОПиУ", make: plReport },
};

export async function renderReports(view) {
  const orgs = await api.get("/api/organizations");
  if (!orgs.length) {
    view.replaceChildren(el("h1", {}, "Отчёты"), el("p", { class: "muted" }, "У вас нет доступа ни к одной организации."));
    return;
  }
  let org = orgs.find((o) => String(o.id) === recall(ORG_KEY)) ?? orgs[0];
  let ref = await loadReferences(org.id);
  let tab = REPORTS[recall(TAB_KEY)] ? recall(TAB_KEY) : "osv";
  let report = null;

  const orgSelect = el("select", { class: "inline-select", "aria-label": "Организация" },
    ...orgs.map((o) => el("option", { value: o.id, selected: o.id === org.id }, o.name)));
  const filters = reportFilters({ ref, onChange: () => reload(), onExcel: () => excel() });
  const tabs = el("div", { class: "tabs" });
  const box = el("div");

  view.replaceChildren(
    el("div", { class: "page-head" }, el("h1", {}, "Отчёты"), el("label", { class: "inline" }, "Организация ", orgSelect)),
    tabs, filters.box, box,
  );

  const ctx = { org: () => org, ref: () => ref, filters, reload: () => reload() };

  async function reload() {
    if (!report || !filters.valid()) return;
    try {
      await report.load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function excel() {
    if (!filters.valid()) return;
    try {
      await report.excel();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function open(key) {
    tab = key;
    remember(TAB_KEY, key);
    tabs.replaceChildren(...Object.entries(REPORTS).map(([k, r]) =>
      el("button", { type: "button", class: `tab${k === key ? " active" : ""}`, onclick: () => open(k) }, r.title)));
    const reportBox = el("div");
    box.replaceChildren(reportBox);
    report = REPORTS[key].make({ box: reportBox, ctx });
    await report.ready;
    await reload();
  }

  orgSelect.addEventListener("change", async () => {
    org = orgs.find((o) => String(o.id) === orgSelect.value);
    remember(ORG_KEY, org.id);
    ref = await loadReferences(org.id);
    filters.setRef(ref);
    await report.reset();
    await reload();
  });

  await open(tab);
}

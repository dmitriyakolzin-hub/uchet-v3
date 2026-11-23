// Вкладка «Лизинг» на странице «Имущество» (docs/TZ.md, раздел 28, «Детали 21.2»): по каждому договору лизинга —
// лизингодатель, платёж и срок, всё имущество договора, заявки, по которым оно пришло, и платежи (оплачено / осталось).
import { api } from "../api.js";
import { date as ruDate, money } from "../format.js";
import { el, instruction, toast } from "../ui.js";

/** box — контейнер вкладки; ctx: { org, openItem } (openItem — открыть карточку единицы). */
export function assetLeasingTab(box, ctx) {
  const listBox = el("div");
  const open = new Set();

  function render() {
    box.replaceChildren(
      instruction("Договор лизинга появляется, когда финансовый директор заполняет лизинг по заявке на имущество "
        + "(«Заполнить лизинг…»), или заводится в справочнике договоров с видом «Лизинг». Один договор может закрывать "
        + "несколько заявок: платёж договора — сумма платежей его заявок, срок и даты общие. Имущество, принятое по "
        + "заявкам договора, привязывается само; имеющееся имущество привяжите в реестре (карточка или «Изменить "
        + "выбранные» → «Договор лизинга»). Оплачено — по заявкам на оплату периодического платежа «Лизинг: договор № …»."),
      listBox);
  }

  async function load() {
    let rows;
    try {
      rows = await api.get(`/api/organizations/${ctx.org().id}/leasing`);
    } catch (err) {
      listBox.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    if (!rows.length) {
      listBox.replaceChildren(el("p", { class: "muted" }, "Договоров лизинга пока нет."));
      return;
    }
    listBox.replaceChildren(el("table", { class: "recon lease-contracts" },
      el("thead", {}, el("tr", {}, ...["Договор", "Лизингодатель", "Юрлицо", "Платёж в мес.", "Срок", "Стоимость", "Оплачено", "Осталось", "Имущество"]
        .map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows.flatMap((c) => {
        const tr = el("tr", { class: "lease-row", "data-contract": c.id },
          el("td", {}, `${open.has(c.id) ? "▾" : "▸"} ${c.title}`),
          el("td", {}, c.counterparty_name), el("td", {}, c.legal_entity_name),
          el("td", { class: "num" }, c.payment ? money(c.payment) : "не заполнен"),
          el("td", {}, c.months ? `${c.months} мес.: ${ruDate(c.start)} — ${ruDate(c.end)}` : ""),
          el("td", { class: "num" }, c.total ? money(c.total) : ""),
          el("td", { class: "num" }, money(c.paid)),
          el("td", { class: "num" }, c.remaining !== null ? money(c.remaining) : ""),
          el("td", { class: "num" }, String(c.items.length)));
        tr.addEventListener("click", () => { if (open.has(c.id)) open.delete(c.id); else open.add(c.id); load(); });
        return open.has(c.id) ? [tr, el("tr", { class: "lease-details" }, el("td", { colspan: 9 }, details(c)))] : [tr];
      }))));
  }

  function details(c) {
    return el("div", { class: "lease-box" },
      el("h4", {}, "Имущество"),
      c.items.length ? el("table", { class: "recon" },
        el("thead", {}, el("tr", {}, ...["Инв. №", "Наименование", "Место", "Статус", "Цена с удорожанием", "Заявка"].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...c.items.map((i) => {
          const tr = el("tr", { class: "clickable" }, el("td", {}, i.number), el("td", {}, i.name), el("td", {}, i.cfo_name),
            el("td", {}, i.status_title), el("td", { class: "num" }, i.upgraded_price ? money(i.upgraded_price) : ""),
            el("td", {}, i.request_number ? `№ ${i.request_number}` : "привязано вручную"));
          tr.addEventListener("click", () => ctx.openItem(i.id));
          return tr;
        }))) : el("p", { class: "muted" }, "Имущества пока нет: оно появится после приёмки по заявкам договора."),
      el("h4", {}, "Заявки на имущество"),
      c.requests.length ? el("ul", {}, ...c.requests.map((r) => el("li", {},
        `№ ${r.number} — ${r.name} × ${r.quantity}${r.lease_payment ? `, ${money(r.lease_payment)} в мес.` : ""} (${r.status_title})`)))
        : el("p", { class: "muted" }, "Нет"),
      el("h4", {}, "Платежи"),
      c.payments.length ? el("ul", {}, ...c.payments.map((p) => el("li", {},
        `Заявка на оплату № ${p.number} — ${ruDate(p.pay_date)}, ${money(p.amount)} ₽, ${p.status_title}`)))
        : el("p", { class: "muted" }, "Заявки на оплату появятся на 3 месяца вперёд после заполнения лизинга."));
  }

  return { show: async () => { render(); await load().catch((err) => toast(err.message, "error")); } };
}

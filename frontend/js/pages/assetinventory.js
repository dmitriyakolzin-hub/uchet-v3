// Инвентаризация имущества (docs/TZ.md, раздел 28, «Детали 19.7»): список, создание, отметка найденного с телефона
// (камера через BarcodeDetector или ввод инв. / серийного номера), завершение с переносом и выбытием ненайденного.
import { api } from "../api.js";
import { date as ruDate } from "../format.js";
import { el, instruction, openForm, toast } from "../ui.js";

const ACTIVE_KEY = "uchet3_inventory_active";
const options = (items, label, empty) => [["", empty], ...items.map((i) => [String(i.id), label(i)])];

/** Инвентаризация, открытая в этом браузере (для кнопки в карточке единицы после перехода по QR). */
export function activeInventory(orgId) {
  try {
    const value = JSON.parse(localStorage.getItem(ACTIVE_KEY) ?? "null");
    return value && value.org === orgId ? value : null;
  } catch {
    return null;
  }
}

function setActive(inv, orgId) {
  try {
    if (inv) localStorage.setItem(ACTIVE_KEY, JSON.stringify({ id: inv.id, number: inv.number, org: orgId }));
    else localStorage.removeItem(ACTIVE_KEY);
  } catch { /* без памяти браузера — только ввод номера */ }
}

/** Отметить единицу в инвентаризации (кнопка в карточке). */
export async function markInActive(orgId, itemId) {
  const active = activeInventory(orgId);
  if (!active) return;
  try {
    const r = await api.post(`/api/asset-inventories/${active.id}/scan`, { code: `?item=${itemId}` });
    toast(r.message, r.result === "surplus" ? "error" : undefined);
  } catch (err) {
    toast(err.message, "error");
  }
}

/** Вкладка «Инвентаризация». */
export function assetInventoryTab(box, ctx) {
  let current = null; // открытая инвентаризация (со строками)
  let stopCamera = null;

  function closeCamera() {
    stopCamera?.();
    stopCamera = null;
  }

  async function showList() {
    closeCamera();
    current = null;
    const org = ctx.org();
    const canEdit = org.permissions.includes("assets_edit");
    let rows;
    try {
      rows = await api.get(`/api/organizations/${org.id}/asset-inventories`);
    } catch (err) {
      box.replaceChildren(el("p", { class: "error" }, err.message));
      return;
    }
    box.replaceChildren(
      instruction("Инвентаризация: оператор АХО, бухгалтер или ФД создаёт её по месту (ЦФО с подчинёнными), складу и / или МОЛ — "
        + "система запоминает, что там должно быть. Затем с телефона: «Сканировать» (камера читает QR-этикетку) или введите "
        + "инвентарный / серийный номер. Если камера в браузере не работает, откройте QR-код обычной камерой телефона — "
        + "в карточке единицы появится кнопка «Отметить в инвентаризации». Найденное, которое числится в другом месте, "
        + "попадает в «Не на своём месте». Отмечать могут АХО, инженер, бухгалтер, ФД и МОЛ своей инвентаризации. "
        + "При завершении найденное не на месте можно перенести сюда, а ненайденное — отправить на выбытие "
        + "(гибель / утрата), его согласует финансовый директор."),
      el("div", { class: "filters" }, canEdit ? el("button", { type: "button", onclick: createForm }, "+ Инвентаризация") : ""),
      rows.length ? el("table", { class: "recon asset-inventories" },
        el("thead", {}, el("tr", {}, ...["№", "Дата", "Что проверяем", "Найдено", "Не найдено", "Не на месте", "Статус"].map((t) => el("th", {}, t)))),
        el("tbody", {}, ...rows.map((inv) => el("tr", { class: "clickable-row", "data-number": inv.number, onclick: () => openInventory(inv.id) },
          el("td", {}, String(inv.number)),
          el("td", {}, ruDate(inv.on_date)),
          el("td", {}, scopeText(inv), inv.note ? el("div", { class: "muted small" }, inv.note) : ""),
          el("td", { class: "num" }, `${inv.found_count} из ${inv.expected_count}`),
          el("td", { class: "num" }, String(inv.missing_count)),
          el("td", { class: "num" }, String(inv.surplus_count)),
          el("td", {}, el("span", { class: `badge inventory-${inv.status}` }, inv.status_title))))))
        : el("p", { class: "muted" }, "Инвентаризаций пока не было."),
    );
  }

  const scopeText = (inv) => [inv.place, inv.warehouse && `склад: ${inv.warehouse}`, inv.employee_name && `МОЛ: ${inv.employee_name}`]
    .filter(Boolean).join(" · ");

  function createForm() {
    const ref = ctx.ref();
    openForm({
      title: "Новая инвентаризация",
      fields: [
        { name: "on_date", label: "Дата", type: "date", required: true },
        { name: "cfo_id", label: "Место (ЦФО)", type: "select", options: options(ref.cfos, (c) => ref.cfoLabel.get(c.id) ?? c.name, "— любое —"),
          hint: "Вместе с подчинёнными ЦФО" },
        { name: "warehouse_id", label: "Склад", type: "select", options: options(ref.warehouses, (w) => w.name, "— любой —") },
        { name: "employee_id", label: "МОЛ", type: "select", options: options(ref.employees, (e) => e.full_name, "— любой —") },
        { name: "note", label: "Примечание", type: "textarea" },
      ],
      values: { on_date: new Date().toISOString().slice(0, 10) },
      submitText: "Начать",
      onSubmit: async (v) => {
        const inv = await api.post(`/api/organizations/${ctx.org().id}/asset-inventories`, {
          on_date: v.on_date, cfo_id: v.cfo_id ? Number(v.cfo_id) : null, warehouse_id: v.warehouse_id ? Number(v.warehouse_id) : null,
          employee_id: v.employee_id ? Number(v.employee_id) : null, note: v.note ?? "" });
        toast(`Инвентаризация № ${inv.number}: должно быть ${inv.expected_count} ед.`);
        await openInventory(inv.id);
      },
    });
  }

  async function openInventory(id) {
    try {
      current = await api.get(`/api/asset-inventories/${id}`);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    const org = ctx.org();
    if (current.status === "open" && current.can_scan) setActive(current, org.id);
    else if (activeInventory(org.id)?.id === current.id) setActive(null);
    renderInventory();
  }

  const message = el("p", { class: "scan-message", role: "status" });

  async function scan(code) {
    code = code.trim();
    if (!code) return;
    try {
      const r = await api.post(`/api/asset-inventories/${current.id}/scan`, { code });
      current = r.inventory;
      message.className = `scan-message scan-${r.result}`;
      message.textContent = r.message;
      navigator.vibrate?.(r.result === "found" ? 80 : [80, 60, 80]);
      renderInventory(true);
    } catch (err) {
      message.className = "scan-message scan-error";
      message.textContent = err.message;
    }
  }

  async function startCamera(videoBox) {
    if (!("BarcodeDetector" in window) || !navigator.mediaDevices?.getUserMedia) {
      toast("Этот браузер не читает QR-коды сам — откройте этикетку камерой телефона или введите номер", "error");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
    } catch (err) {
      toast(`Камера недоступна: ${err.message}`, "error");
      return;
    }
    const video = el("video", { playsinline: true, muted: true, class: "scan-video" });
    video.srcObject = stream;
    videoBox.replaceChildren(video, el("button", { type: "button", class: "secondary", onclick: () => { closeCamera(); videoBox.replaceChildren(); } }, "Остановить камеру"));
    await video.play();
    const detector = new window.BarcodeDetector({ formats: ["qr_code"] });
    let alive = true;
    let last = "";
    let lastAt = 0;
    stopCamera = () => { alive = false; stream.getTracks().forEach((t) => t.stop()); };
    const tick = async () => {
      if (!alive) return;
      try {
        const codes = await detector.detect(video);
        const value = codes[0]?.rawValue;
        // одну и ту же этикетку перед камерой не отмечаем повторно чаще раза в 3 секунды
        if (value && (value !== last || Date.now() - lastAt > 3000)) {
          last = value;
          lastAt = Date.now();
          await scan(value);
        }
      } catch { /* кадр не прочитан */ }
      setTimeout(tick, 300);
    };
    tick();
  }

  let videoBox = el("div", { class: "scan-camera" });

  function renderInventory(keepCamera = false) {
    if (!keepCamera) {
      closeCamera();
      videoBox = el("div", { class: "scan-camera" });
      message.textContent = "";
    }
    const inv = current;
    const input = el("input", { type: "text", placeholder: "Инв. № или серийный номер", "aria-label": "Код единицы", autocomplete: "off" });
    const scanForm = el("form", { class: "filters scan-form", onsubmit: (e) => { e.preventDefault(); scan(input.value); input.value = ""; } },
      input, el("button", { type: "submit" }, "Отметить"),
      el("button", { type: "button", class: "secondary", onclick: () => startCamera(videoBox) }, "Сканировать"));
    const lineList = (title, lines, canUndo) => (lines.length ? el("div", { class: "inventory-group" },
      el("h3", {}, `${title}: ${lines.length}`),
      el("table", { class: "recon" }, el("tbody", {}, ...lines.map((l) => el("tr", { "data-item": l.number },
        el("td", {}, l.number), el("td", {}, l.name),
        el("td", { class: "muted small" }, [l.place, l.employee_name].filter(Boolean).join(" · ")),
        el("td", {}, canUndo && inv.can_scan ? el("button", { type: "button", class: "link", onclick: async () => {
          try {
            current = await api.delete(`/api/asset-inventory-lines/${l.id}`);
            renderInventory(true);
          } catch (err) {
            toast(err.message, "error");
          }
        } }, "Снять отметку") : "")))))) : "");
    const lines = inv.lines;
    box.replaceChildren(
      el("div", { class: "filters" }, el("button", { type: "button", class: "link", onclick: showList }, "← Все инвентаризации")),
      el("h2", {}, `Инвентаризация № ${inv.number} от ${ruDate(inv.on_date)}`),
      el("p", {}, scopeText(inv), " ", el("span", { class: `badge inventory-${inv.status}` }, inv.status_title)),
      el("p", { class: "inventory-progress" }, `Найдено ${inv.found_count} из ${inv.expected_count}`
        + (inv.surplus_count ? `, не на своём месте — ${inv.surplus_count}` : "")),
      inv.can_scan ? scanForm : "",
      inv.can_scan ? videoBox : "",
      message,
      lineList("Не на своём месте", lines.filter((l) => !l.expected), true),
      lineList(inv.status === "open" ? "Не найдено" : "Не найдено (недостача)", lines.filter((l) => l.expected && !l.found), false),
      lineList("Найдено", lines.filter((l) => l.expected && l.found), true),
      inv.can_close ? el("div", { class: "form-actions" },
        el("button", { type: "button", onclick: closeForm }, "Завершить"),
        el("button", { type: "button", class: "secondary danger", onclick: async () => {
          if (!confirm(`Удалить инвентаризацию № ${inv.number}? Отметки пропадут.`)) return;
          try {
            await api.delete(`/api/asset-inventories/${inv.id}`);
            setActive(null);
            await showList();
          } catch (err) {
            toast(err.message, "error");
          }
        } }, "Удалить")) : "",
    );
    if (inv.can_scan && !keepCamera) input.focus();
  }

  function closeForm() {
    const inv = current;
    openForm({
      title: `Завершить инвентаризацию № ${inv.number}`,
      fields: [
        { name: "move_surplus", label: `Найденное не на своём месте (${inv.surplus_count}) — перенести сюда`, type: "checkbox" },
        { name: "lost_to_disposal", label: `Ненайденное (${inv.missing_count}) — на выбытие «Гибель / утрата»`, type: "checkbox",
          hint: "Акт выбытия согласует финансовый директор" },
      ],
      submitText: "Завершить",
      onSubmit: async (v) => {
        const r = await api.post(`/api/asset-inventories/${inv.id}/close`, { move_surplus: !!v.move_surplus, lost_to_disposal: !!v.lost_to_disposal });
        setActive(null);
        toast(r.disposal_number ? `Завершено. Акт выбытия № ${r.disposal_number} — на согласовании ФД` : "Инвентаризация завершена");
        current = r.inventory;
        renderInventory();
        await ctx.reload?.();
      },
    });
  }

  return {
    show: async () => {
      const active = activeInventory(ctx.org().id);
      if (current && current.status === "open") renderInventory();
      else if (active) await openInventory(active.id);
      else await showList();
    },
    hide: closeCamera,
  };
}

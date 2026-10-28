// Статьи организации деревом (docs/TZ.md, раздел 19): подписи с кодом и путём, статьи без подстатей, системные статьи.

// «2.3.2. Аренда / переменная часть / газ» — так статья показывается в списках выбора
export function articleIndex(articles) {
  const byId = new Map(articles.map((a) => [a.id, a]));
  const path = (a) => {
    const names = [];
    for (let x = a, guard = 0; x && guard < 50; x = byId.get(x.parent_id), guard += 1) names.unshift(x.name);
    return names.join(" / ");
  };
  const label = new Map(articles.map((a) => [a.id, `${a.code ? `${a.code} ` : ""}${path(a)}`]));
  const groups = new Set(articles.map((a) => a.parent_id).filter((id) => id !== null));
  const leaves = articles.filter((a) => !groups.has(a.id))
    .sort((a, b) => label.get(a.id).localeCompare(label.get(b.id), "ru", { numeric: true }));
  const byKey = new Map(articles.filter((a) => a.system_key).map((a) => [a.system_key, a]));
  return { label, leaves, byKey };
}

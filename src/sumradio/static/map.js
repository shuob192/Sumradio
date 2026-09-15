export const categories = {
  road_blocked: {label: "通行障害", symbol: "×"},
  water: {label: "飲料水・給水", symbol: "💧"},
  rescue: {label: "救助・搬送", symbol: "✚"},
  supplies: {label: "物資", symbol: "▣"},
  evacuation: {label: "避難支援", symbol: "↗"},
  power: {label: "停電・電源", symbol: "ϟ"},
  other: {label: "その他", symbol: "!"},
};

export const positionLabels = {
  unresolved: "場所未確認", queued: "場所検索待ち", searching: "場所検索中",
  suggested: "位置候補・未確認", ambiguous: "複数の位置候補", confirmed: "位置確認済み",
  not_found: "場所が見つかりません", failed: "場所検索失敗",
};

export const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
export const categoryHTML = (values = ["other"]) => [...new Set(values)].map((value) => {
  const item = categories[value] || categories.other;
  return `<span class="map-category cat-${escapeHTML(value)}"><b aria-hidden="true">${item.symbol}</b> ${item.label}</span>`;
}).join("");

export function safeSourceLink(place) {
  if (!place?.source_url || !/^https?:\/\//i.test(place.source_url)) return "";
  return `<a href="${escapeHTML(place.source_url)}" target="_blank" rel="noopener noreferrer">位置の出典</a>`;
}

export function makeMap(id, config, onTileError = () => {}) {
  if (!window.L) throw new Error("地図ライブラリを読み込めません。地点一覧から操作してください。");
  const map = L.map(id, {scrollWheelZoom: false}).setView([35.45, 139.63], 12);
  // Keep attribution visible, even when tiles fail or a local tile source is used.
  map.attributionControl.setPrefix(false);
  map.attributionControl.addAttribution('© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>');
  if (config.tile_attribution) map.attributionControl.addAttribution(escapeHTML(config.tile_attribution));
  if (config.tile_url) {
    const tiles = L.tileLayer(config.tile_url, {maxZoom: 19, keepBuffer: 1, updateWhenIdle: true}).addTo(map);
    tiles.on("tileerror", onTileError);
  }
  return map;
}

export function leafletBounds(bounds) {
  return [[bounds.south, bounds.west], [bounds.north, bounds.east]];
}

export class ResponseMap {
  constructor({onTask, onLocation, onCommunication, toast}) {
    Object.assign(this, {onTask, onLocation, onCommunication, toast});
    this.markers = new Map();
    this.regionId = null;
  }

  render(state) {
    this.state = state;
    const area = state.active_area;
    document.querySelector("#map-area-name").textContent = area?.name || "対象地域を指定してください";
    document.querySelector("#map-legend").innerHTML = categoryHTML(Object.keys(categories));
    document.querySelector("#map-training-label").hidden = !area?.training;
    const catalog = state.map?.catalog;
    document.querySelector("#catalog-status").textContent = catalog?.error || `登録地点 ${catalog?.count || 0}件 · 位置を照合してから地図へ表示します`;
    if (!this.map && window.L) {
      this.map = makeMap("response-map", state.map || {}, () => {
        document.querySelector("#map-network-message").textContent = "背景地図を取得できません。保存済みのピンと地点一覧は利用できます。";
      });
    }
    const tasks = (state.tasks || []).filter((task) => ["candidate", "open"].includes(task.state) && task.area?.id === area?.id && area);
    this.visibleTasks = tasks;
    document.querySelector("#map-task-count").textContent = `${tasks.filter((task) => task.position?.selected).length} / ${tasks.length}件を表示`;
    const groups = new Map();
    for (const task of tasks) {
      const place = task.position?.selected;
      if (!place) continue;
      const key = `${place.lat.toFixed(6)},${place.lon.toFixed(6)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(task);
    }
    if (this.map) {
      if (area && this.regionId !== area.id) {
        this.regionId = area.id;
        this.map.fitBounds(leafletBounds(area.bounds));
      }
      for (const [key, marker] of this.markers) {
        if (!groups.has(key)) { marker.remove(); this.markers.delete(key); }
      }
      for (const [key, members] of groups) {
        const place = members[0].position.selected;
        const tentative = members.some((task) => task.state === "candidate" || task.position.status !== "confirmed");
        const types = [...new Set(members.flatMap((task) => task.map_category || ["other"]))];
        const symbols = types.map((value) => categories[value]?.symbol || "!").join("");
        const status = members.some((task) => task.state === "candidate") ? "候補" : "未対応";
        const html = `<div class="task-pin ${tentative ? "tentative" : "confirmed"}"><span aria-hidden="true">${symbols}</span>${members.length > 1 ? `<b class="pin-count">${members.length}</b>` : ""}<small>${status}</small></div>`;
        const width = Math.max(62, types.length * 22 + 12);
        const icon = L.divIcon({className: "response-pin", html, iconSize: [width, 54], iconAnchor: [width / 2, 50]});
        let marker = this.markers.get(key);
        const signature = members.map((task) => `${task.id}:${task.version}:${Boolean(task.is_evidence_stale)}`).join("|");
        if (!marker) {
          marker = L.marker([place.lat, place.lon], {icon, keyboard: true, title: `${place.name} ${members.length}件`}).addTo(this.map);
          this.markers.set(key, marker);
        }
        if (marker.taskSignature !== signature) {
          marker.setIcon(icon);
          marker.getElement()?.setAttribute("aria-label", `${place.name} ${members.length}件 ${types.map((value) => categories[value]?.label || "その他").join("・")} ${status}`);
          const content = document.createElement("div");
          const heading = document.createElement("strong");
          heading.textContent = place.name;
          content.append(heading);
          for (const task of members) content.append(this.popupTask(task));
          if (marker.getPopup()) marker.setPopupContent(content);
          else marker.bindPopup(content, {maxWidth: 350, maxHeight: 300});
          marker.taskSignature = signature;
        }
      }
    }
    const list = document.querySelector("#map-location-list");
    list.replaceChildren();
    for (const task of tasks.sort((a, b) => Number(Boolean(a.position?.selected)) - Number(Boolean(b.position?.selected)))) {
      const row = document.createElement("div");
      row.className = "location-row";
      row.innerHTML = `<strong>${escapeHTML(task.title)}</strong><span>${escapeHTML(task.position?.selected?.name || task.location || "場所未確認")}</span><small>${positionLabels[task.position?.status] || "場所未確認"}</small>`;
      const button = document.createElement("button");
      button.textContent = "場所を確認・訂正";
      button.addEventListener("click", () => this.onLocation(task));
      row.append(button);
      list.append(row);
    }
    if (!tasks.length) list.innerHTML = '<p class="empty">この地域の対応中タスクはありません。</p>';
  }

  popupTask(task) {
    const div = document.createElement("section");
    div.className = "map-popup-task";
    div.innerHTML = `<small>${escapeHTML(task.id)}</small><h3>${escapeHTML(task.title)}</h3>
      <p>${task.kind === "request" ? "要請" : "状況確認"} · ${task.state === "candidate" ? "タスク候補・未確認" : "未対応"} · ${positionLabels[task.position.status]}</p>
      ${categoryHTML(task.map_category)}<p>${escapeHTML(task.action)}</p>
      <p>${task.position.selected?.precision === "exact" ? "手動指定位置" : "代表位置（入口などの詳細地点は未確認）"}</p>
      ${task.is_evidence_stale ? '<p class="helper error">根拠交信が訂正されています。現在の内容を確認してください。</p>' : ""}
      <blockquote>${(task.evidence_quotes || []).map(escapeHTML).join("<br>")}</blockquote>`;
    const detail = document.createElement("button");
    detail.textContent = "カード・詳細";
    detail.onclick = () => this.onTask(task);
    div.append(detail);
    for (const id of task.evidence_communication_ids || []) {
      const button = document.createElement("button");
      button.textContent = "元の交信";
      button.onclick = () => this.onCommunication(id);
      div.append(button);
    }
    return div;
  }

  focus(task) {
    if (task.area?.id !== this.state.active_area?.id) return this.toast("このタスクの対象地域へ切り替えてください", true);
    const place = task.position?.selected;
    if (!place) return this.onLocation(task);
    const key = `${place.lat.toFixed(6)},${place.lon.toFixed(6)}`;
    const marker = this.markers.get(key);
    if (!marker || !this.map) return this.onLocation(task);
    document.querySelector("#map-panel").scrollIntoView({behavior: "smooth", block: "start"});
    this.map.setView([place.lat, place.lon], Math.max(15, this.map.getZoom()));
    marker.openPopup();
  }

  fitTasks() {
    const points = this.visibleTasks.filter((task) => task.position?.selected).map((task) => [task.position.selected.lat, task.position.selected.lon]);
    if (points.length && this.map) this.map.fitBounds(L.latLngBounds(points).pad(0.15), {maxZoom: 16});
  }
}

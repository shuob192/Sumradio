import {ResponseMap, categories, categoryHTML, positionLabels, escapeHTML as esc, safeSourceLink, makeMap, leafletBounds} from "./map.js";

const $ = (selector) => document.querySelector(selector);

export function setupGeography({api, toast, getState, reload, onTask, onCommunication}) {
  let areaMap, locationMap, locationMarker, locationTask, initialPromptShown = false;
  const responseMap = new ResponseMap({onTask, onLocation: openLocation, onCommunication, toast});

  function fillArea(area) {
    $("#area-name").value = area.name;
    $("#area-training").checked = area.training ?? true;
    for (const key of ["south", "west", "north", "east"]) $(`#area-${key}`).value = area.bounds[key];
    if (areaMap) areaMap.fitBounds(leafletBounds(area.bounds));
  }

  function openArea() {
    const state = getState();
    const area = state.active_area || state.map.default_area;
    $("#area-error").textContent = "";
    $("#area-query").value = area.name;
    $("#area-dialog").showModal();
    if (!areaMap) {
      try { areaMap = makeMap("area-map", state.map); }
      catch (error) { $("#area-error").textContent = error.message; }
    }
    areaMap?.invalidateSize();
    fillArea(area);
  }

  $("#change-area").onclick = openArea;
  $("#fit-map-tasks").onclick = () => responseMap.fitTasks();
  $("#search-area").onclick = async () => {
    const query = $("#area-query").value.trim();
    if (!query) return;
    $("#search-area").disabled = true;
    $("#area-results").textContent = "地域を検索しています…";
    $("#area-error").textContent = "";
    try {
      const result = await api("/api/areas/search", {method: "POST", body: JSON.stringify({query})});
      $("#area-results").replaceChildren();
      for (const area of result.areas) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = area.name;
        button.onclick = () => fillArea(area);
        $("#area-results").append(button);
      }
      if (!result.areas.length) $("#area-results").textContent = "候補がありません。地図の範囲を直接指定できます。";
    } catch (error) { $("#area-error").textContent = error.message; $("#area-results").textContent = ""; }
    finally { $("#search-area").disabled = false; }
  };
  $("#area-query").onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); $("#search-area").click(); } };
  $("#use-map-bounds").onclick = () => {
    if (!areaMap) return;
    const box = areaMap.getBounds();
    fillArea({name: $("#area-name").value || $("#area-query").value || "手動指定地域", training: $("#area-training").checked,
      bounds: {south: box.getSouth(), west: box.getWest(), north: box.getNorth(), east: box.getEast()}});
  };
  $("#area-form").onsubmit = async (event) => {
    event.preventDefault();
    const bounds = Object.fromEntries(["south", "west", "north", "east"].map((key) => [key, Number($(`#area-${key}`).value)]));
    try {
      await api("/api/area", {method: "PUT", body: JSON.stringify({name: $("#area-name").value.trim(), bounds, training: $("#area-training").checked})});
      $("#area-dialog").close();
      sessionStorage.setItem("sumradio-area-boot", getState().runtime.boot_id);
      await reload();
      toast("対象地域を設定しました");
    } catch (error) { $("#area-error").textContent = error.message; }
  };

  async function openLocation(task) {
    try { task = await api(`/api/tasks/${encodeURIComponent(task.id)}`); }
    catch (error) { return toast(error.message, true); }
    locationTask = task;
    const position = task.position || {};
    $("#location-title").textContent = task.title;
    $("#location-status").textContent = `${task.area?.name || "対象地域未設定"} · ${positionLabels[position.status] || "場所未確認"}`;
    $("#location-detail").textContent = position.error || `報告された場所：${task.location || "未確認"}${task.map_info?.location?.detail ? ` / ${task.map_info.location.detail}` : ""}`;
    $("#location-error").textContent = "";
    $("#location-candidates").innerHTML = (position.candidates || []).map((place, index) => `<label class="place-candidate"><input type="radio" name="location-candidate" value="${esc(place.id)}" ${index === 0 ? "checked" : ""} /><span><strong>${esc(place.name)}</strong><small>${esc(place.address)}</small><small>${place.lat.toFixed(6)}, ${place.lon.toFixed(6)} · ${place.precision === "exact" ? "手動指定位置" : "代表位置・詳細地点未確認"}</small>${safeSourceLink(place)}</span></label>`).join("") || '<p class="helper">位置候補がありません。場所の名称を編集して再検索するか、地図で指定できます。</p>';
    $("#confirm-location-candidate").disabled = !position.candidates?.length;
    $("#retry-location").disabled = !["candidate", "open"].includes(task.state) || !task.area;
    $("#location-name").value = position.selected?.name || task.location || task.title;
    $("#location-lat").value = position.selected?.lat ?? "";
    $("#location-lon").value = position.selected?.lon ?? "";
    if (!$("#location-dialog").open) $("#location-dialog").showModal();
    try {
      if (!locationMap) {
        locationMap = makeMap("location-map", getState().map);
        locationMap.on("click", (event) => setManualPoint(event.latlng.lat, event.latlng.lng));
      }
      locationMap.invalidateSize();
      if (locationMarker) { locationMarker.remove(); locationMarker = null; }
      if (position.selected) {
        locationMap.setView([position.selected.lat, position.selected.lon], 16);
        drawPoint(position.selected.lat, position.selected.lon);
      } else if (task.area) locationMap.fitBounds(leafletBounds(task.area.bounds));
    } catch (error) { $("#location-error").textContent = error.message; }
  }

  function drawPoint(lat, lon) {
    if (!locationMap) return;
    if (locationMarker) locationMarker.setLatLng([lat, lon]);
    else locationMarker = L.circleMarker([lat, lon], {radius: 9, color: "#25664d", fillOpacity: 0.7}).addTo(locationMap);
  }
  function setManualPoint(lat, lon) {
    $("#location-lat").value = lat.toFixed(7);
    $("#location-lon").value = lon.toFixed(7);
    drawPoint(lat, lon);
  }
  $("#location-candidates").onchange = (event) => {
    const candidate = locationTask.position.candidates.find((place) => place.id === event.target.value);
    if (candidate && locationMap) { locationMap.setView([candidate.lat, candidate.lon], 16); drawPoint(candidate.lat, candidate.lon); }
  };
  async function confirmLocation(data) {
    try {
      await api(`/api/tasks/${encodeURIComponent(locationTask.id)}/location`, {method: "PUT", body: JSON.stringify({version: locationTask.version, actor: "operator", ...data})});
      $("#location-dialog").close();
      await reload();
      toast("位置を確定しました。タスクの承認状態は保持されます。");
    } catch (error) { $("#location-error").textContent = `${error.message}。変更が競合した場合は画面を開き直してください。`; }
  }
  $("#confirm-location-candidate").onclick = () => {
    const selected = document.querySelector('input[name="location-candidate"]:checked');
    if (selected) confirmLocation({candidate_id: selected.value});
  };
  $("#manual-location-form").onsubmit = (event) => {
    event.preventDefault();
    confirmLocation({name: $("#location-name").value.trim(), lat: Number($("#location-lat").value), lon: Number($("#location-lon").value)});
  };
  $("#retry-location").onclick = async () => {
    if (locationTask.position.status === "confirmed" && !window.confirm("確定位置を未確認に戻して再検索しますか？以前の位置は履歴に残ります。")) return;
    try {
      await api(`/api/tasks/${encodeURIComponent(locationTask.id)}/location/search`, {method: "POST", body: JSON.stringify({version: locationTask.version})});
      $("#location-dialog").close();
      await reload();
      toast("場所の検索を受け付けました");
    } catch (error) { $("#location-error").textContent = error.message; }
  };
  $("#show-archived").onclick = async () => {
    try {
      const result = await api("/api/tasks?include_discarded=true");
      const tasks = result.tasks.filter((task) => ["done", "discarded"].includes(task.state));
      $("#archive-list").replaceChildren();
      for (const task of tasks) {
        const button = document.createElement("button");
        button.className = "archive-item";
        button.textContent = `${task.state === "done" ? "対応済み" : "破棄"} · ${task.title} · ${task.area?.name || "地域未設定"}`;
        button.onclick = () => { $("#archive-dialog").close(); onTask(task); };
        $("#archive-list").append(button);
      }
      if (!tasks.length) $("#archive-list").textContent = "対応済み・破棄したタスクはありません。";
      $("#archive-dialog").showModal();
    } catch (error) { toast(error.message, true); }
  };
  return {
    openLocation, focus: (task) => responseMap.focus(task),
    render(state) {
      $("#active-area-name").textContent = state.active_area?.name || "対象地域を設定してください";
      $("#training-label").hidden = !state.active_area?.training;
      $("#change-area").disabled = Boolean(state.runtime.recording);
      $("#new-task").disabled = !state.active_area;
      try { responseMap.render(state); } catch (error) { $("#map-network-message").textContent = error.message; }
      if (!initialPromptShown && state.map) {
        initialPromptShown = true;
        if (!state.active_area || sessionStorage.getItem("sumradio-area-boot") !== state.runtime.boot_id) openArea();
      }
    },
  };
}

export function positionHistoryHTML(task, time) {
  const selected = task.position?.selected;
  const entries = (task.history || []).map((entry) => {
    const before = entry.before?.selected || entry.before?.position?.selected;
    const after = entry.after?.selected || entry.after?.position?.selected;
    const position = (point) => point ? `${point.name} (${point.lat.toFixed(6)}, ${point.lon.toFixed(6)})` : "位置なし";
    const stateLabels = {candidate: "タスク候補", open: "未対応", done: "対応済み", discarded: "破棄"};
    const action = {created: "登録", edited: "編集", transitioned: "状態変更", location_reset: "場所の検索受付", location_searched: "場所の検索結果", location_confirmed: "位置確定"}[entry.action] || entry.action;
    const detail = entry.action === "transitioned" ? `${stateLabels[entry.before?.state]} → ${stateLabels[entry.after.state]}` : before || after ? `${position(before)} → ${position(after)}` : "";
    const classifications = entry.after?.map_category ? categoryHTML(entry.after.map_category) : "";
    return `<div class="history">${esc(time(entry.created_at))} · ${esc(entry.actor)} · ${esc(action)}<p>${esc(detail)}</p>${classifications}</div>`;
  }).join("");
  return `<section class="detail-section"><h3>地図の情報</h3>${categoryHTML(task.map_category)}<p>${esc(task.area?.name || "対象地域未設定")} · ${positionLabels[task.position?.status] || "場所未確認"}</p>
    ${selected ? `<p>${esc(selected.name)} · ${selected.lat.toFixed(6)}, ${selected.lon.toFixed(6)}<br>${selected.precision === "exact" ? "手動指定位置" : "代表位置（詳細地点は未確認）"} ${safeSourceLink(selected)}</p>` : ""}
    <button type="button" id="task-open-location">場所を確認・訂正</button>
    <h3>根拠の交信</h3>${(task.evidence_communication_ids || []).map((id) => `<button type="button" data-evidence-id="${esc(id)}">${esc(id)}</button>`).join("") || "<p>手動登録：関連交信なし</p>"}
    <blockquote>${(task.evidence_quotes || []).map(esc).join("<br>")}</blockquote>
    <h3>履歴</h3>${entries}</section>`;
}

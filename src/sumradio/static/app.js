const appState = { communications: [], tasks: [], runtime: {}, selectedCommunication: null, editingTask: null };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const lines = (value) => (value || "").split("\n").map((item) => item.trim()).filter(Boolean);

function parsePeople(value) {
  return lines(value).map((line, index) => {
    const [description, count = ""] = line.split(/[|｜]/).map((item) => item.trim());
    if (!description) throw new Error(`人数の${index + 1}行目に説明が必要です`);
    const parsed = count === "" ? null : Number(count);
    if (parsed !== null && (!Number.isInteger(parsed) || parsed < 0)) throw new Error(`人数の${index + 1}行目は0以上の整数にしてください`);
    return { description, count: parsed };
  });
}

function parseResources(value) {
  return lines(value).map((line, index) => {
    const [item, quantity = "", unit = "", detail = ""] = line.split(/[|｜]/).map((part) => part.trim());
    if (!item) throw new Error(`物資の${index + 1}行目に品目が必要です`);
    const parsed = quantity === "" ? null : Number(quantity);
    if (parsed !== null && (!Number.isFinite(parsed) || parsed < 0)) throw new Error(`物資の${index + 1}行目は0以上の数量にしてください`);
    return { item, quantity: parsed, unit: unit || null, detail: detail || null };
  });
}

const formatPeople = (items) => (items || []).map((item) => `${item.description}｜${item.count ?? ""}`).join("\n");
const formatResources = (items) => (items || []).map((item) => `${item.item}｜${item.quantity ?? ""}｜${item.unit || ""}｜${item.detail || ""}`).join("\n");

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `処理に失敗しました (${response.status})`);
  return body;
}

function toast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.className = `show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.className = ""; }, 3200);
}

function localTime(value) {
  if (!value) return "未確認";
  return new Intl.DateTimeFormat("ja-JP", { month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit" }).format(new Date(value));
}

function statusLabel(status) {
  return ({pending:"整理待ち", running:"AI整理中", succeeded:"整理済み", failed:"AI整理失敗", stale:"旧版"})[status] || status;
}

function transcriptionLabel(status) {
  return ({pending:"待機中", running:"文字起こし中", succeeded:"完了", failed:"文字起こし失敗"})[status] || status;
}

function confirmationLabel(type) {
  return ({completion_report:"完了報告", ambiguity:"不明点", contradiction:"矛盾", duplicate:"重複候補", negation_or_hold:"未完了・保留"})[type] || type;
}

function renderStatus() {
  const runtime = appState.runtime || {};
  const whisper = runtime.whisper || {};
  const codex = runtime.codex || {};
  const whisperLabels = {starting:"起動中", loading:"モデル読込中", ready:"準備完了", error:"エラー", skipped:"停止中"};
  const codexLabels = {starting:"起動中", ready:`${codex.model || "Codex"} 準備完了`, error:"エラー"};
  const whisperNode = $("#whisper-status");
  whisperNode.textContent = `文字起こし · ${whisperLabels[whisper.status] || whisper.status || "確認中"}`;
  whisperNode.className = `status-pill ${whisper.status === "ready" ? "ready" : whisper.status === "error" ? "error" : ""}`;
  const codexNode = $("#codex-status");
  codexNode.textContent = `AI整理 · ${codexLabels[codex.status] || codex.status || "確認中"}`;
  codexNode.className = `status-pill ${codex.status === "ready" ? "ready" : codex.status === "error" ? "error" : ""}`;
  const recording = Boolean(runtime.recording);
  document.body.classList.toggle("recording", recording);
  $("#start-recording").disabled = recording || whisper.status !== "ready";
  $("#finalize-recording").disabled = !recording;
  $("#stop-recording").disabled = !recording;
  $("#device-select").disabled = recording;
  $("#refresh-devices").disabled = recording;
  const error = runtime.audio_error || whisper.error || codex.error;
  $("#audio-message").textContent = error || (recording ? "受信中です。無音5秒または「今の交信を確定」で区切ります。" : "無音5秒で自動的に1件の交信として確定します。");
  $("#audio-message").className = `helper${error ? " error" : ""}`;
}

function renderCommunications() {
  $("#communication-count").textContent = appState.communications.length;
  const container = $("#communications");
  if (!appState.communications.length) {
    container.innerHTML = '<div class="empty">まだ交信記録はありません。<br>マイクから受信を開始してください。</div>';
    return;
  }
  container.innerHTML = appState.communications.map((item) => {
    const facts = item.communication_facts || {};
    const title = facts.sender || "発信者 未確認";
    const text = item.transcript_current || (item.transcription_status === "failed" ? item.transcription_error : "文字起こし中…");
    const displayStatus = item.transcription_status === "failed" ? "failed" : item.extraction_status;
    const displayLabel = item.transcription_status === "failed" ? "文字起こし失敗" : statusLabel(item.extraction_status);
    return `<button class="communication-item" data-communication-id="${esc(item.id)}">
      <span class="communication-meta"><span>${esc(localTime(item.started_at))}</span><span class="mini-status ${esc(displayStatus)}">${esc(displayLabel)}</span></span>
      <strong>${esc(title)}${facts.location ? ` · ${esc(facts.location)}` : ""}</strong><p>${esc(text)}</p></button>`;
  }).join("");
}

function taskCard(task) {
  const kindLabel = task.kind === "request" ? "要請" : "状況確認";
  const facts = [];
  if (task.location) facts.push(`場所: ${task.location}`);
  if (task.assignee) facts.push(`担当: ${task.assignee}`);
  if (task.condition) facts.push(`条件: ${task.condition}`);
  const people = (task.people || []).map((item) => `${item.description} ${item.count ?? "未確認"}人`);
  const resources = (task.resources || []).map((item) => `${item.item} ${item.quantity ?? "未確認"}${item.unit || ""}`);
  facts.push(...people);
  facts.push(...resources);
  const actions = task.state === "candidate"
    ? `<button data-action="approve" data-task-id="${task.id}" class="primary">承認</button><button data-action="discard" data-task-id="${task.id}">破棄</button>`
    : task.state === "open" ? `<button data-action="complete" data-task-id="${task.id}" class="primary">対応済みにする</button>` : "";
  return `<article class="task-card ${esc(task.kind)}">
    <div class="task-labels"><span class="kind-badge ${esc(task.kind)}">${kindLabel}</span>${task.is_evidence_stale ? '<span class="warning-badge">根拠が旧版</span>' : ""}${(task.related_task_ids || []).length ? '<span class="warning-badge">重複候補</span>' : ""}</div>
    <h4>${esc(task.title)}</h4><p class="action">${esc(task.action)}</p>
    <div class="task-facts">${facts.map((fact) => `<span>${esc(fact)}</span>`).join("")}</div>
    <div class="task-actions">${actions}<button data-action="edit" data-task-id="${task.id}">詳細・編集</button></div>
  </article>`;
}

function renderBoard() {
  for (const state of ["candidate", "open", "done"]) {
    const tasks = appState.tasks.filter((item) => item.state === state);
    $(`#${state}-count`).textContent = tasks.length;
    $(`#${state}-tasks`).innerHTML = tasks.length ? tasks.map(taskCard).join("") : '<div class="empty">カードはありません</div>';
  }
}

function render() { renderStatus(); renderCommunications(); renderBoard(); }

async function loadState() {
  try {
    const state = await api("/api/state");
    Object.assign(appState, state);
    render();
  } catch (error) { toast(error.message, true); }
}

async function loadDevices() {
  const select = $("#device-select");
  try {
    const result = await api("/api/devices");
    select.innerHTML = result.devices.length
      ? result.devices.map((item) => `<option value="${item.id}" ${item.is_default ? "selected" : ""}>${esc(item.name)} · ${item.default_sample_rate}Hz${item.is_default ? " · 既定" : ""}</option>`).join("")
      : '<option value="">入力マイクが見つかりません</option>';
  } catch (error) {
    select.innerHTML = '<option value="">マイクを取得できません</option>';
    toast(error.message, true);
  }
}

function showCommunication(id) {
  const item = appState.communications.find((entry) => entry.id === id);
  if (!item) return;
  appState.selectedCommunication = item;
  $("#communication-title").textContent = localTime(item.started_at);
  const facts = item.communication_facts || {};
  const confirmations = item.confirmations || [];
  const people = (facts.people || []).map((entry) => `${entry.description}: ${entry.count ?? "未確認"}人`).join("、");
  const resources = (facts.resources || []).map((entry) => `${entry.item}: ${entry.quantity ?? "未確認"}${entry.unit || ""}`).join("、");
  const phonetics = (item.phonetic_interpretations || []).map((entry) => `${entry.source_text} → ${entry.interpreted_value}`).join("、");
  $("#communication-detail").innerHTML = `
    <section class="detail-section"><h3>原音</h3><audio controls src="/api/communications/${encodeURIComponent(item.id)}/audio/original"></audio></section>
    <section class="detail-section"><h3>整理状態</h3><div class="detail-grid">
      <div class="detail-value"><span>文字起こし</span><strong>${esc(transcriptionLabel(item.transcription_status))}</strong></div>
      <div class="detail-value"><span>AI整理</span><strong>${esc(statusLabel(item.extraction_status))}</strong></div>
      <div class="detail-value"><span>発信者</span><strong>${esc(facts.sender || "未確認")}</strong></div>
      <div class="detail-value"><span>宛先</span><strong>${esc(facts.recipient || "未確認")}</strong></div>
      <div class="detail-value"><span>場所</span><strong>${esc(facts.location || "未確認")}</strong></div>
      <div class="detail-value"><span>文字版</span><strong>${item.transcript_revision}</strong></div>
    </div>
    ${facts.reported_situation ? `<p class="original-transcript"><strong>報告状況:</strong> ${esc(facts.reported_situation)}</p>` : ""}
    ${people ? `<p class="original-transcript"><strong>人数:</strong> ${esc(people)}</p>` : ""}
    ${resources ? `<p class="original-transcript"><strong>物資:</strong> ${esc(resources)}</p>` : ""}
    ${phonetics ? `<p class="original-transcript"><strong>通話表の解釈:</strong> ${esc(phonetics)}</p>` : ""}
    ${item.extraction_error ? `<p class="helper error">${esc(item.extraction_error)}</p>` : ""}</section>
    <section class="detail-section"><h3>Whisper認識原文</h3><div class="original-transcript">${esc(item.transcript_original || "文字起こし待ち")}</div></section>
    <section class="detail-section"><h3>確認・訂正</h3><textarea id="correction-text" maxlength="30000">${esc(item.transcript_current)}</textarea><div class="task-actions"><button id="save-correction" class="primary">訂正を保存して再整理</button><button id="retry-extraction">AI整理を再試行</button></div></section>
    ${confirmations.length ? `<section class="detail-section"><h3>確認事項</h3><ul>${confirmations.map((entry) => `<li><strong>${esc(confirmationLabel(entry.type))}</strong>: ${esc(entry.summary)}</li>`).join("")}</ul></section>` : ""}
    ${(item.corrections || []).length ? `<section class="detail-section"><h3>訂正履歴</h3>${item.corrections.map((entry) => `<div class="history">版${entry.revision} · ${esc(localTime(entry.created_at))} · ${esc(entry.author)}</div>`).join("")}</section>` : ""}`;
  $("#communication-dialog").showModal();
  $("#save-correction").addEventListener("click", async () => {
    const text = $("#correction-text").value.trim();
    if (!text || text === item.transcript_current) return toast("訂正内容に変更がありません", true);
    try {
      await api(`/api/communications/${encodeURIComponent(item.id)}/transcript`, { method:"PATCH", body:JSON.stringify({version:item.version, text, author:"operator"}) });
      $("#communication-dialog").close(); toast("訂正を保存し、再整理を開始しました"); await loadState();
    } catch (error) { toast(error.message, true); }
  });
  $("#retry-extraction").addEventListener("click", async () => {
    try {
      await api(`/api/communications/${encodeURIComponent(item.id)}/retry`, { method:"POST", body:JSON.stringify({version:item.version}) });
      $("#communication-dialog").close(); toast("AI整理を再試行します"); await loadState();
    } catch (error) { toast(error.message, true); }
  });
}

function openTaskDialog(task = null) {
  appState.editingTask = task;
  $("#task-dialog-title").textContent = task ? "タスクを編集" : "手動で候補を登録";
  $("#task-id").value = task?.id || "";
  $("#task-version").value = task?.version || "";
  $("#task-kind").value = task?.kind || "request";
  $("#task-kind").disabled = Boolean(task);
  for (const field of ["title","action","target","location","condition","assignee","deadline"]) $(`#task-${field}`).value = task?.[field] || "";
  $("#task-people").value = formatPeople(task?.people);
  $("#task-resources").value = formatResources(task?.resources);
  $("#task-communications").value = (task?.evidence_communication_ids || []).join("\n");
  $("#task-quotes").value = (task?.evidence_quotes || []).join("\n");
  $("#task-readonly-detail").innerHTML = task ? `<section class="detail-section"><h3>履歴</h3>${(task.history || []).map((entry) => `<div class="history">${esc(localTime(entry.created_at))} · ${esc(entry.actor)} · ${esc(entry.action)}</div>`).join("")}</section>` : "";
  $("#task-dialog").showModal();
}

async function saveTask(event) {
  event.preventDefault();
  const task = appState.editingTask;
  try {
    const common = {
      title:$("#task-title").value.trim(), action:$("#task-action").value.trim(), target:$("#task-target").value.trim() || null,
      location:$("#task-location").value.trim() || null, condition:$("#task-condition").value.trim() || null,
      assignee:$("#task-assignee").value.trim() || null, deadline:$("#task-deadline").value.trim() || null,
      people:parsePeople($("#task-people").value), resources:parseResources($("#task-resources").value),
      evidence_communication_ids:lines($("#task-communications").value), evidence_quotes:lines($("#task-quotes").value), actor:"operator"
    };
    if (task) await api(`/api/tasks/${encodeURIComponent(task.id)}`, {method:"PATCH", body:JSON.stringify({...common, version:task.version})});
    else await api("/api/tasks", {method:"POST", body:JSON.stringify({...common, kind:$("#task-kind").value})});
    $("#task-dialog").close(); toast(task ? "タスクを更新しました" : "タスク候補を登録しました"); await loadState();
  } catch (error) { toast(error.message, true); }
}

async function transitionTask(task, target) {
  const labels = {open:"この候補を承認し、未対応へ移動しますか？", discarded:"この候補を破棄しますか？記録と履歴は保存されます。", done:"対応または対応要否の判断が完了しましたか？"};
  if (!window.confirm(labels[target])) return;
  try {
    await api(`/api/tasks/${encodeURIComponent(task.id)}/transition`, {method:"POST", body:JSON.stringify({version:task.version, target_state:target, actor:"operator"})});
    toast("状態を更新しました"); await loadState();
  } catch (error) { toast(error.message, true); }
}

$("#refresh-devices").addEventListener("click", loadDevices);
$("#start-recording").addEventListener("click", async () => {
  try {
    const value = $("#device-select").value;
    await api("/api/recording/start", {method:"POST", body:JSON.stringify({device_id:value === "" ? null : Number(value)})});
    toast("録音を開始しました"); await loadState();
  } catch (error) { toast(error.message, true); }
});
$("#finalize-recording").addEventListener("click", async () => {
  try { const result = await api("/api/recording/finalize", {method:"POST"}); toast(result.finalized ? "現在の交信を確定しました" : "確定できる発話はありません"); }
  catch (error) { toast(error.message, true); }
});
$("#stop-recording").addEventListener("click", async () => {
  try { await api("/api/recording/stop", {method:"POST"}); toast("録音を停止しました"); await loadState(); }
  catch (error) { toast(error.message, true); }
});
$("#new-task").addEventListener("click", () => openTaskDialog());
$("#task-form").addEventListener("submit", saveTask);
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => $(`#${button.dataset.close}`).close()));
$("#communications").addEventListener("click", (event) => { const button = event.target.closest("[data-communication-id]"); if (button) showCommunication(button.dataset.communicationId); });
$(".board").addEventListener("click", (event) => {
  const button = event.target.closest("[data-task-id]"); if (!button) return;
  const task = appState.tasks.find((item) => item.id === button.dataset.taskId); if (!task) return;
  if (button.dataset.action === "edit") openTaskDialog(task);
  if (button.dataset.action === "approve") transitionTask(task, "open");
  if (button.dataset.action === "discard") transitionTask(task, "discarded");
  if (button.dataset.action === "complete") transitionTask(task, "done");
});

const events = new EventSource("/api/events");
let refreshTimer;
const scheduleRefresh = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(loadState, 120); };
events.addEventListener("connected", scheduleRefresh);
for (const type of ["communication.created","communication.updated","task.updated","system.status"]) events.addEventListener(type, scheduleRefresh);
events.addEventListener("caption.partial", (event) => { const data = JSON.parse(event.data); $("#partial-caption").textContent = data.text || "次の交信を待っています。"; });
events.addEventListener("audio.level", (event) => { const data = JSON.parse(event.data); $("#input-level").style.width = `${Math.round(Math.max(0, Math.min(1, data.level)) * 100)}%`; });
events.onerror = () => { $("#codex-status").textContent = "画面更新 · 再接続中"; };

await Promise.all([loadState(), loadDevices()]);

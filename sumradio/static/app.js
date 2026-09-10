"use strict";
const $ = (id) => document.getElementById(id);
const state = {events: new Map(), tasks: new Map(), cursor: 0};
const statusNames = {idle:"○ 入力待機",recording:"● 録音中",processing:"◌ 認識中",disconnected:"⚠ 音声入力切断",failed:"⚠ 失敗"};
const sourceNames = {live_radio:"ライブ無線",recorded_radio:"録音WAV",mock:"保存済み台本・推論なし",none:"入力なし"};
const highlightNames = {place:"地名",number:"数量・人数",time:"時刻",negative:"否定・保留",unit:"部隊",phonetic:"通話表"};
let eventSource;
let saveEditor;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, body, method="POST") {
  const options = {method};
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) { options.headers = {"Content-Type":"application/json"}; options.body = JSON.stringify(body); }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "入力内容を確認してください");
  return data;
}
function action(label, fn, kind="secondary") {
  const button = node("button", label, kind);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true; error("");
    try { await fn(); } catch (e) { error(e.message); }
    finally { button.disabled = false; }
  });
  return button;
}
function actor() {
  const value = $("actor").value.trim();
  if (!value) { $("actor").focus(); throw new Error("確認者の名前を入力してください"); }
  localStorage.setItem("sumradio-actor", value);
  return value;
}
function time(value) { return value ? new Date(value).toLocaleString("ja-JP") : "—"; }
function live(value) {
  $("state").textContent = statusNames[value.state] || value.state;
  $("source").textContent = sourceNames[value.source] || value.source;
  $("live-text").textContent = value.partial_text || (value.state === "recording" ? "暫定字幕を認識中…" : "交信の入力を待っています");
  $("preview").textContent = value.state === "recording" && value.partial_preview && value.partial_preview !== value.partial_text ? `認識途中（変わる可能性があります）：${value.partial_preview}` : "";
  $("live-message").textContent = value.message;
  $("meter").textContent = [value.pending ? `認識待ち ${value.pending}件` : "", value.silence ? "無音3秒以上" : "", value.clipping ? "⚠ 入力クリッピング" : ""].filter(Boolean).join(" / ");
}
function editDialog(title, fields, save) {
  $("edit-title").textContent = title;
  $("edit-fields").replaceChildren();
  for (const field of fields) {
    const label = node("label", field.label);
    const input = node(field.multiline ? "textarea" : "input");
    input.name = field.name; input.value = field.value || ""; input.required = !!field.required;
    input.maxLength = field.max || 1000;
    label.append(input); $("edit-fields").append(label);
  }
  saveEditor = save; $("editor").showModal();
}
async function editEvent(event) {
  const by = actor();
  editDialog("交信を訂正（ASR原文は保持されます）", [{name:"corrected_text",label:"訂正文",value:event.corrected_text ?? event.raw_text,multiline:true,max:10000}], async values => {
    const updated = await api(`/api/events/${event.id}`, {...values,actor:by,status:"needs_review"}, "PATCH");
    state.events.set(updated.id, updated); renderTimeline();
  });
}
function taskFields(task) {
  return [
    {name:"title",label:"内容",value:task.title,multiline:true,required:true},
    {name:"place",label:"場所（候補）",value:task.place,max:200},
    {name:"target",label:"対象",value:task.target,max:200},
    {name:"assignee",label:"担当（候補）",value:task.assignee,max:200},
    {name:"due_at",label:"期限（候補・原文の表現）",value:task.due_at,max:200},
  ];
}
function taskDialog(task, event) {
  const by = actor();
  const source = task || {title:event.corrected_text ?? event.raw_text};
  editDialog(task ? "タスクを編集" : "交信からタスク候補を作成", taskFields(source), async values => {
    for (const key of ["place","target","assignee","due_at"]) values[key] ||= null;
    const updated = task
      ? await api(`/api/tasks/${task.id}`, {...values,actor:by}, "PATCH")
      : await api("/api/tasks", {...values,source_event_ids:[event.id],actor:by});
    state.tasks.set(updated.id, updated); renderBoard();
  });
}
async function changeTask(task, status, eventId) {
  const changes = {actor:actor(),status};
  if (eventId) changes.source_event_ids = [...new Set([...task.source_event_ids,eventId])];
  const updated = await api(`/api/tasks/${task.id}`, changes, "PATCH");
  state.tasks.set(updated.id, updated); renderBoard(); renderTimeline();
}
async function history(id) {
  const rows = await api(`/api/history/${id}`, undefined, "GET");
  $("history-content").replaceChildren();
  for (const row of rows) {
    const section = node("section");
    section.append(node("p", `${time(row.changed_at)} / ${row.actor}`));
    const changes = {};
    for (const [key,value] of Object.entries(row.after)) if (JSON.stringify(row.before?.[key]) !== JSON.stringify(value)) changes[key] = value;
    section.append(node("pre", JSON.stringify(changes,null,2))); $("history-content").append(section);
  }
  $("history-dialog").showModal();
}
function eventCard(event) {
  const card = node("article", undefined, "event"); card.id = `event-${event.id}`;
  const top = node("div",undefined,"event-top");
  top.append(node("time",time(event.received_at)),node("span",sourceNames[event.source] || event.source));
  const confirmed = event.status === "confirmed";
  top.append(node("span", confirmed ? "✓ 人間が確認済み" : event.status === "processing" ? "◌ 認識中" : event.status === "failed" ? "⚠ 認識失敗" : "⚠ AI文字起こし・要確認",`review${confirmed ? " confirmed" : ""}`));
  const mainText = event.status === "failed" ? "認識に失敗しました。原音を確認してください。" : (event.corrected_text ?? event.raw_text) || "認識結果を待っています…";
  card.append(top,node("p",mainText,"event-text"));
  if (event.corrected_text !== null && event.corrected_text !== undefined) card.append(node("p",`訂正：${event.corrected_by} / ${time(event.corrected_at)}。重要語ラベルはASR原文を対象としています。`,"metadata"));
  const highlights = node("div",undefined,"highlights");
  for (const h of event.highlights || []) highlights.append(node("span",`${highlightNames[h.type] || h.type}：${h.text}`,`highlight ${h.type}`));
  card.append(highlights);
  for (const warning of [...(event.warnings || []),...(event.error ? [event.error] : [])]) card.append(node("p",`⚠ ${warning}`,"warning"));
  if (event.extraction_notes?.length) card.append(node("p",`否定・訂正・保留を検出：${event.extraction_notes.join("、")}。自動提案を抑制しました。`,"metadata"));
  if (event.raw_text) {
    const details = node("details"); details.append(node("summary","原文・正規化候補・認識情報"));
    details.append(node("p",`ASR原文：${event.raw_text}`),node("p",`正規化候補：${event.normalized_text}`),node("p",`${event.model} / ${event.preprocess_profile} / ${event.source === "mock" ? "推論なし" : `${event.latency_ms ?? "—"} ms`} / logprob ${event.avg_logprob ?? "取得なし"}`,"metadata")); card.append(details);
  }
  if (event.audio_ref) {
    const details = node("details"); details.append(node("summary","原音・処理後音声を確認"));
    for (const [variant,label] of [["raw","生音声"],["processed","処理後（16 kHz PCM）"]]) {
      const player = node("audio"); player.controls = true; player.preload = "none";
      player.src = `/api/audio/${event.id}/${variant}`;
      details.append(node("p",label,"audio-label"),player);
    } card.append(details);
  } else if (event.source === "mock") card.append(node("p","台本再生：音声は付属していません。","metadata"));
  const actions = node("div",undefined,"actions");
  if (["needs_review","confirmed"].includes(event.status)) {
    actions.append(action(confirmed ? "要確認へ戻す" : "内容を確認済みにする", async () => {
      const updated = await api(`/api/events/${event.id}`, {actor:actor(),status:confirmed ? "needs_review" : "confirmed"}, "PATCH");
      state.events.set(updated.id,updated); renderTimeline();
    }),action("訂正",()=>editEvent(event)),action("タスク作成",()=>taskDialog(null,event)));
  }
  actions.append(action("履歴",()=>history(event.id))); card.append(actions);
  for (const suggestion of event.completion_suggestions || []) {
    const task = state.tasks.get(suggestion.task_id);
    if (!task || !["candidate","open"].includes(task.status)) continue;
    const box = node("div",`完了報告の候補：${task.title}。この交信が根拠です。内容を確認して確定してください。`,"completion");
    box.append(action("根拠を確認して対応済みにする",()=>changeTask(task,"done",event.id))); card.append(box);
  }
  return card;
}
function renderTimeline() {
  const query = $("search").value.trim();
  const events = [...state.events.values()].filter(e=>!query || `${e.raw_text} ${e.corrected_text || ""} ${e.received_at}`.includes(query)).reverse();
  $("event-count").textContent = state.events.size;
  $("timeline").replaceChildren(...events.map(eventCard));
  if (!events.length) $("timeline").append(node("p",query ? "一致する交信はありません。" : "ここに時刻付きの交信が並びます。","empty"));
}
function taskCard(task) {
  const card = node("article",undefined,"task");
  card.append(node("h4",task.title));
  if (task.status === "candidate") card.append(node("span","⚠ 候補・要確認","review"));
  for (const [key,label] of [["place","場所"],["target","対象"],["assignee","担当候補"],["due_at","期限候補"]]) if (task[key]) card.append(node("p",`${label}：${task[key]}`));
  if (task.duplicate_candidates?.length) card.append(node("p","◇ 同じ場所・対象の候補があります。根拠を比較してください。","warning"));
  if (task.matched_rules?.length) card.append(node("p",`抽出語：${task.matched_rules.join("、")}`,"metadata"));
  for (const id of task.source_event_ids) {
    const e = state.events.get(id);
    const link = node("a",`↗ 根拠交信 ${e ? time(e.received_at) : id}`,"task-source");
    link.href = `#event-${id}`;
    link.addEventListener("click",()=>{ if ($("search").value) { $("search").value=""; renderTimeline(); } });
    card.append(link);
  }
  if (task.completed_at) card.append(node("p",`完了確認：${time(task.completed_at)} / ${task.confirmed_by}`,"metadata"));
  const actions = node("div",undefined,"actions");
  if (task.status === "candidate") actions.append(action("承認して未対応へ",()=>changeTask(task,"open"),"primary"));
  if (task.status === "open") actions.append(action("確認して対応済みへ",()=>changeTask(task,"done"),"primary"));
  if (task.status === "done") actions.append(action("未対応へ戻す",()=>changeTask(task,"open")));
  actions.append(action("編集",()=>taskDialog(task)),action("履歴",()=>history(task.id)));
  if (task.status !== "done" && task.status !== "dismissed") actions.append(action("取り下げ",()=>changeTask(task,"dismissed")));
  if (task.status === "dismissed") actions.append(action("候補へ戻す",()=>changeTask(task,"candidate")));
  card.append(actions); return card;
}
function renderBoard() {
  for (const status of ["candidate","open","done","dismissed"]) {
    const tasks = [...state.tasks.values()].filter(t=>t.status===status).sort((a,b)=>(b.completed_at || b.created_at).localeCompare(a.completed_at || a.created_at));
    $(status).replaceChildren(...tasks.map(taskCard));
    if ($(status+"-count")) $(status+"-count").textContent=tasks.length;
    if (!tasks.length) $(status).append(node("p","タスクはありません","empty"));
  }
}
async function connect() {
  const snapshot = await api("/api/snapshot",undefined,"GET");
  state.events = new Map(snapshot.events.map(e=>[e.id,e])); state.tasks = new Map(snapshot.tasks.map(t=>[t.id,t]));
  state.cursor = snapshot.cursor; live(snapshot.live); renderTimeline(); renderBoard();
  if (eventSource) eventSource.close();
  eventSource = new EventSource(`/api/stream?after=${state.cursor}`);
  eventSource.onopen = ()=>{$("connection").textContent="● ローカル接続中";};
  eventSource.onerror = ()=>{$("connection").textContent="⚠ 接続中断・再接続中";};
  for (const kind of ["event","task","status"]) eventSource.addEventListener(kind,e=>{
    if (+e.lastEventId <= state.cursor) return;
    state.cursor=+e.lastEventId;
    const value=JSON.parse(e.data);
    if (kind === "status") live(value);
    else if (kind === "event") { state.events.set(value.id,value); renderTimeline(); }
    else { state.tasks.set(value.id,value); renderBoard(); renderTimeline(); }
  });
}
function bind(id, fn) { $(id).addEventListener("click",async()=>{error("");$(id).disabled=true;try{await fn();}catch(e){error(e.message);}finally{$(id).disabled=false;}}); }
bind("demo",()=>api("/api/demo/start")); bind("demo-stop",()=>api("/api/demo/stop"));
bind("devices",async()=>{const devices=await api("/api/devices",undefined,"GET");$("device").replaceChildren();for(const d of devices){const option=node("option",`${d.id}: ${d.name}`);option.value=d.id;$("device").append(option);}if(!devices.length)throw new Error("入力デバイスがありません");});
bind("capture",()=>{if($("device").value === "")throw new Error("入力デバイスを選択してください");return api("/api/capture/start",{device:Number($("device").value),mode:$("mode").value});});
bind("capture-stop",()=>api("/api/capture/stop")); bind("manual-start",()=>api("/api/capture/manual/start")); bind("manual-end",()=>api("/api/capture/manual/end"));
$("wav").addEventListener("change",async()=>{const file=$("wav").files[0];if(!file)return;const data=new FormData();data.append("file",file);error("");try{await api("/api/audio",data);}catch(e){error(e.message);}finally{$("wav").value="";}});
$("search").addEventListener("input",renderTimeline);
$("cancel").addEventListener("click",()=>$("editor").close()); $("history-close").addEventListener("click",()=>$("history-dialog").close());
$("edit-form").addEventListener("submit",async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await saveEditor(Object.fromEntries(new FormData(e.target)));$("editor").close();}catch(err){error(err.message);$("editor").close();}finally{button.disabled=false;}});
$("actor").value=localStorage.getItem("sumradio-actor") || "";
connect().catch(e=>{error(`接続できません：${e.message}`);$("connection").textContent="⚠ 接続失敗・ページを再読込してください";});

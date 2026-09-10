// DOM contract tests without a browser. These do not verify rendering or accessibility.
export async function runUiTests(appSource) {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  class Element {
    constructor(tag = "div") {
      this.tagName = tag; this.children = []; this.listeners = {}; this.value = "";
      this.textContent = ""; this.hidden = false; this.disabled = false;
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(type, callback) { this.listeners[type] = callback; }
    focus() { this.focused = true; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    set innerHTML(value) { throw new Error(`Unsafe HTML assignment: ${value}`); }
  }
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement(tag) { return new Element(tag); },
  };
  const event = {
    id: "event-1", received_at: "2026-09-09T10:00:00+09:00", source: "mock",
    status: "needs_review", raw_text: "<img src=x onerror=alert(1)>青葉避難所へ飲料水を手配願う",
    normalized_text: "青葉避難所", corrected_text: null, warnings: [],
    highlights: [{type:"place",text:"青葉避難所"}], model: "saved-demo",
  };
  const task = {
    id:"task-1", title:"飲料水を手配", status:"candidate", source_event_ids:[event.id],
    created_at:event.received_at, place:"青葉避難所", target:"飲料水",
  };
  const requests = [];
  const fetch = async (path, options) => {
    requests.push({path,options});
    let value;
    if (path === "/api/snapshot") value = {events:[event],tasks:[task],cursor:4,
      live:{state:"idle",message:"入力待機",source:"mock"}};
    else if (path === "/api/tasks/task-1") {
      Object.assign(task, JSON.parse(options.body)); value = {...task};
    } else throw new Error(`Unexpected API call: ${path}`);
    return {ok:true,json:async()=>value};
  };
  let stream;
  class EventSource {
    constructor(url) { this.url=url; this.listeners={}; stream=this; }
    addEventListener(kind, callback) { this.listeners[kind]=callback; }
    close() {}
  }
  const storage = new Map();
  const localStorage = {getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)};
  new Function("document", "fetch", "EventSource", "localStorage", "FormData", appSource)(document,fetch,EventSource,localStorage,class FormData {});
  for (let i=0; i<12; i++) await Promise.resolve();
  const get = document.getElementById;
  assert(stream?.url === "/api/stream?after=4", `Snapshot cursor must initialize SSE: ${get("error").textContent}`);
  assert(get("timeline").children[0].id === "event-event-1", "Timeline must render snapshot");
  assert(get("candidate").children[0].tagName === "article", "Candidate card must render");
  const all = element => [element, ...element.children.flatMap(all)];
  assert(all(get("timeline")).some(e=>e.textContent === event.raw_text), "ASR text must be literal text");
  const approve = all(get("candidate")).find(e=>e.textContent === "承認して未対応へ");
  await approve.listeners.click();
  assert(requests.length === 1, "Approval requires an actor");
  get("actor").value="テスト担当";
  await approve.listeners.click();
  assert(JSON.parse(requests.at(-1).options.body).status === "open", "Human approval must PATCH open");
  assert(get("open").children[0].tagName === "article", "Approved task must move to open board");
  stream.listeners.event({lastEventId:"5",data:JSON.stringify({...event,status:"confirmed"})});
  assert(all(get("timeline")).some(e=>e.textContent === "✓ 人間が確認済み"), "SSE must update review state");
  stream.listeners.event({lastEventId:"4",data:JSON.stringify({...event,status:"needs_review"})});
  assert(all(get("timeline")).some(e=>e.textContent === "✓ 人間が確認済み"), "Stale SSE must be ignored");
  stream.listeners.event({lastEventId:"6",data:JSON.stringify({...event,status:"failed"})});
  assert(all(get("timeline")).some(e=>e.className === "event-text" && e.textContent === "認識に失敗しました。原音を確認してください。"), "Rejected ASR must not be displayed as the main transcript");
  get("search").value="存在しない語";
  get("search").listeners.input();
  assert(get("timeline").children[0].textContent === "一致する交信はありません。", "Search must filter records");
  return "UI DOM contract: snapshot, literal text, actor validation, approval, SSE ordering, search passed";
}

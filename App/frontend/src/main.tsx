import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import L from "leaflet";
import {
  Radio,
  Mic,
  Square,
  Plus,
  MapPin,
  Check,
  X,
  History,
  ArrowRight,
  ChevronLeft,
  RefreshCw,
  Headphones,
  Pencil,
  Droplets,
  Activity,
  Map as MapIcon,
} from "lucide-react";
import type { components } from "./api-types";
import "leaflet/dist/leaflet.css";
import "./style.css";

type Run = components["schemas"]["RunView"];
type InterpretationLocationOption =
  components["schemas"]["InterpretationLocationOption"];
type Task = components["schemas"]["Task"];
type Comm = components["schemas"]["Communication"];
type Profile = components["schemas"]["Profile"];
type Place = NonNullable<Task["location"]["confirmed"]>;
type Evidence = components["schemas"]["Evidence"];
type Quantity = components["schemas"]["Quantity"];
type Category = components["schemas"]["Category"];
type Region = NonNullable<Run["region"]>;
type RunSummary = Pick<
  Run,
  "id" | "title" | "demo_profile_id" | "created_at" | "region"
>;
const regionLabel = (region: Region | null | undefined) =>
  region ? region.prefecture + region.municipality : "地域指定なし";
type Health = {
  whisper: string;
  whisper_error: string | null;
  phonetic_error: string | null;
  storage_errors: string[];
  codex_available: boolean;
  codex_model: string;
  recording_run_id: string | null;
  audio_error: string | null;
  tile_url: string;
  data_dir: string;
};
const categoryLabels: Record<Category, string> = {
  injury: "負傷者",
  rescue: "救助・搬送",
  medical: "体調不良・医療",
  water: "飲料水・給水",
  road_blocked: "通行障害",
  fire: "火災・煙",
  flood: "浸水・冠水",
  building_damage: "建物被害",
  communication: "通信障害",
  missing_person: "行方不明・安否確認",
  sanitation: "トイレ・衛生",
  supplies: "物資",
  evacuation: "避難支援",
  power: "停電・電源",
  other: "その他",
};
const symbols: Record<Category, string> = {
  injury: "🩸",
  medical: "🩺",
  water: "💧",
  rescue: "🚑",
  road_blocked: "×",
  fire: "🔥",
  flood: "🌊",
  building_damage: "🏚️",
  communication: "📡",
  missing_person: "🔍",
  sanitation: "🚻",
  supplies: "📦",
  evacuation: "🏃",
  power: "⚡",
  other: "!",
};
const statuses = {
  candidate: "タスク候補",
  unhandled: "未対応",
  completed: "対応済み",
  discarded: "破棄",
};
const extractionLabels = {
  waiting: "整理待ち",
  running: "AI整理中",
  done: "整理済み",
  failed: "整理失敗",
};
const time = (s: string) =>
  new Date(s).toLocaleTimeString("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
const date = (s: string) => new Date(s).toLocaleString("ja-JP");
const currentText = (c: Comm) => c.corrections.at(-1)?.text ?? c.original_text;
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch("/api" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}

function MapPanel({
  tasks,
  initialLocations,
  tileUrl,
  onSelect,
  selected,
  onPick,
}: {
  tasks: Task[];
  initialLocations: Place[];
  tileUrl: string;
  onSelect: (id: string) => void;
  selected: string | null;
  onPick?: (lat: number, lon: number) => void;
}) {
  const host = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    layer = useRef<L.LayerGroup | null>(null);
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;
  const [tileError, setTileError] = useState(false);
  useEffect(() => {
    if (!host.current) return;
    const m = L.map(host.current, { zoomControl: false }).setView(
      [35.6895, 139.6917],
      12,
    );
    map.current = m;
    L.control.zoom({ position: "bottomright" }).addTo(m);
    const tiles = L.tileLayer(tileUrl, {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>',
    }).addTo(m);
    tiles.on("tileerror", () => setTileError(true));
    layer.current = L.layerGroup().addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) =>
      onPickRef.current?.(e.latlng.lat, e.latlng.lng),
    );
    const observer = new ResizeObserver(() => m.invalidateSize());
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      m.remove();
      map.current = null;
    };
  }, [tileUrl]);
  useEffect(() => {
    const points = initialLocations.map((p) => [p.lat, p.lon] as L.LatLngTuple);
    if (points.length)
      map.current?.fitBounds(points, { padding: [38, 38], maxZoom: 14 });
  }, [initialLocations, map.current]);
  useEffect(() => {
    if (!layer.current || !map.current) return;
    layer.current.clearLayers();
    const groups = new Map<string, { place: Place; tasks: Task[] }>();
    for (const t of tasks.filter((t) =>
      ["candidate", "unhandled"].includes(t.status),
    )) {
      const p =
        t.location.confirmed ??
        (t.location.candidates.length === 1 ? t.location.candidates[0] : null);
      if (!p) continue;
      const key = `${p.lat},${p.lon}`;
      const g = groups.get(key) ?? { place: p, tasks: [] };
      g.tasks.push(t);
      groups.set(key, g);
    }
    for (const { place, tasks: items } of groups.values()) {
      const pending = items.some(
        (t) => t.status === "candidate" || t.location.status !== "confirmed",
      );
      // Catalog order is presentation only, never a severity/triage ranking.
      const present = new Set(items.flatMap((t) => t.map_categories));
      const categories = (Object.keys(categoryLabels) as Category[]).filter(
        (c) => present.has(c),
      );
      const visible = categories.slice(0, 3);
      const extra = categories.length - visible.length;
      const width = Math.max(34, visible.length * 26 + 8 + (extra ? 24 : 0));
      const icon = L.divIcon({
        className: "map-marker",
        html: `<div class="pin ${pending ? "pending" : ""} ${items.some((t) => t.id === selected) ? "selected" : ""}" title="${categories.map((c) => categoryLabels[c]).join("・")}">${visible.map((c) => `<span class="pin-symbol" data-category="${c}" role="img" aria-label="${categoryLabels[c]}">${symbols[c]}</span>`).join("")}${extra ? `<span class="pin-more" aria-label="ほか${extra}分類">+${extra}</span>` : ""}${items.length > 1 ? `<small aria-label="${items.length}件のタスク">${items.length}</small>` : ""}</div>`,
        iconSize: [width, 40],
        iconAnchor: [width / 2, 40],
      });
      const marker = L.marker([place.lat, place.lon], {
        icon,
        title: place.name,
      }).addTo(layer.current);
      const content = document.createElement("div");
      content.className = "pin-popup";
      const title = document.createElement("strong");
      title.textContent = place.name;
      content.append(title);
      for (const t of items) {
        const button = document.createElement("button");
        button.textContent = `${statuses[t.status]} · ${t.title}`;
        button.onclick = () => onSelect(t.id);
        content.append(button);
        const labels = document.createElement("p");
        labels.className = "popup-categories";
        labels.textContent = t.map_categories
          .map((c) => `${symbols[c]} ${categoryLabels[c]}`)
          .join(" ／ ");
        content.append(labels);
      }
      marker.bindPopup(content);
    }
  }, [tasks, selected, onSelect]);
  const selectedTask = tasks.find((t) => t.id === selected);
  const selectedPlace =
    selectedTask?.location.confirmed ?? selectedTask?.location.candidates[0];
  useEffect(() => {
    if (selectedPlace)
      map.current?.panTo([selectedPlace.lat, selectedPlace.lon]);
  }, [selected, selectedPlace?.lat, selectedPlace?.lon]);
  return (
    <>
      <div className={"map-canvas " + (onPick ? "picking" : "")} ref={host} />
      {tileError && (
        <div className="map-notice">
          背景地図を取得できません。保存済みの位置と一覧は利用できます。
        </div>
      )}
      {onPick && <div className="map-notice">地図をクリックして位置を指定</div>}
    </>
  );
}

function TaskEditor({
  task,
  run,
  onSave,
  onClose,
}: {
  task: Task | null;
  run: Run;
  onSave: (body: unknown) => Promise<void>;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(task?.title ?? ""),
    [description, setDescription] = useState(task?.description ?? "");
  const [kind, setKind] = useState(task?.kind ?? "request"),
    [place, setPlace] = useState(task?.place?.expression ?? "");
  const [municipality, setMunicipality] = useState(
      task?.place?.municipality ?? "",
    ),
    [detail, setDetail] = useState(task?.place?.detail ?? "");
  const [cats, setCats] = useState<Category[]>(
    task?.map_categories ?? ["other"],
  );
  const [reason, setReason] = useState(task?.manual_reason ?? ""),
    [assignee, setAssignee] = useState(task?.assignee ?? ""),
    [deadline, setDeadline] = useState(task?.deadline ?? "");
  const [evidence, setEvidence] = useState<Evidence[]>(task?.evidence ?? []),
    [related, setRelated] = useState<string[]>(task?.related_task_ids ?? []);
  const [quantities, setQuantities] = useState<Quantity[]>(
      task?.quantities ?? [],
    ),
    [busy, setBusy] = useState(false);
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={task ? "タスクを編集" : "手動タスク登録"}
      >
        <div className="section-head">
          <h2>{task ? "タスクを編集" : "タスク候補を手動登録"}</h2>
          <button className="icon-button" onClick={onClose} aria-label="閉じる">
            <X size={20} />
          </button>
        </div>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              await onSave({
                title,
                description,
                kind,
                place: place
                  ? {
                      expression: place,
                      search_name: place,
                      municipality: municipality || null,
                      detail: detail || null,
                    }
                  : null,
                map_categories: cats,
                manual_reason: reason,
                assignee: assignee || null,
                deadline: deadline || null,
                evidence,
                related_task_ids: related,
                quantities,
                uncertainties: task?.uncertainties ?? [],
              });
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            種別
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as Task["kind"])}
            >
              <option value="request">要請</option>
              <option value="situation_confirmation">状況確認</option>
            </select>
          </label>
          <label>
            概要
            <input
              required
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            内容
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <div className="form-grid">
            <label>
              場所
              <input value={place} onChange={(e) => setPlace(e.target.value)} />
            </label>
            <label>
              自治体
              <input
                value={municipality}
                onChange={(e) => setMunicipality(e.target.value)}
              />
            </label>
          </div>
          <label>
            入口・橋詰などの詳細
            <input value={detail} onChange={(e) => setDetail(e.target.value)} />
          </label>
          <fieldset>
            <legend>地図記号</legend>
            <div className="checks">
              {Object.entries(categoryLabels).map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    aria-label={label}
                    checked={cats.includes(key as Category)}
                    onChange={(e) =>
                      setCats(
                        e.target.checked
                          ? [...cats, key as Category]
                          : cats.filter((c) => c !== key),
                      )
                    }
                  />
                  <span aria-hidden="true">{symbols[key as Category]}</span>{" "}
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="form-grid">
            <label>
              担当
              <input
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
              />
            </label>
            <label>
              期限
              <input
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
              />
            </label>
          </div>
          <fieldset>
            <legend>物資・数量</legend>
            {quantities.map((q, i) => (
              <div className="quantity-row" key={i}>
                <input
                  aria-label="物資名"
                  placeholder="品目"
                  value={q.description}
                  onChange={(e) =>
                    setQuantities(
                      quantities.map((v, j) =>
                        j === i ? { ...v, description: e.target.value } : v,
                      ),
                    )
                  }
                />
                <input
                  aria-label="数量"
                  type="number"
                  value={q.value ?? ""}
                  onChange={(e) =>
                    setQuantities(
                      quantities.map((v, j) =>
                        j === i
                          ? {
                              ...v,
                              value:
                                e.target.value === ""
                                  ? null
                                  : Number(e.target.value),
                            }
                          : v,
                      ),
                    )
                  }
                />
                <input
                  aria-label="単位"
                  placeholder="箱・名など"
                  value={q.unit ?? ""}
                  onChange={(e) =>
                    setQuantities(
                      quantities.map((v, j) =>
                        j === i ? { ...v, unit: e.target.value } : v,
                      ),
                    )
                  }
                />
                <button
                  type="button"
                  onClick={() =>
                    setQuantities(quantities.filter((_, j) => j !== i))
                  }
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              className="text-button"
              onClick={() =>
                setQuantities([
                  ...quantities,
                  { description: "", value: null, unit: null },
                ])
              }
            >
              ＋ 数量を追加
            </button>
          </fieldset>
          <fieldset>
            <legend>根拠となる交信</legend>
            {Object.values(run.communications)
              .filter((c) => currentText(c))
              .map((c) => (
                <label className="check-line" key={c.id}>
                  <input
                    type="checkbox"
                    checked={evidence.some((x) => x.communication_id === c.id)}
                    onChange={(e) =>
                      setEvidence(
                        e.target.checked
                          ? [
                              ...evidence,
                              {
                                communication_id: c.id,
                                revision: c.revision,
                                quote: currentText(c),
                              },
                            ]
                          : evidence.filter((x) => x.communication_id !== c.id),
                      )
                    }
                  />
                  {time(c.received_at)} {currentText(c).slice(0, 70)}
                </label>
              ))}
            {evidence.map((ev, i) => (
              <label key={i}>
                根拠引用 r{ev.revision}
                <textarea
                  value={ev.quote}
                  onChange={(e) =>
                    setEvidence(
                      evidence.map((v, j) =>
                        i === j ? { ...v, quote: e.target.value } : v,
                      ),
                    )
                  }
                />
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend>関連タスク</legend>
            {Object.values(run.tasks)
              .filter((t) => t.id !== task?.id)
              .map((t) => (
                <label className="check-line" key={t.id}>
                  <input
                    type="checkbox"
                    checked={related.includes(t.id)}
                    onChange={(e) =>
                      setRelated(
                        e.target.checked
                          ? [...related, t.id]
                          : related.filter((id) => id !== t.id),
                      )
                    }
                  />
                  {t.title}
                </label>
              ))}
          </fieldset>
          <label>
            登録・編集の理由（根拠）
            <textarea
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <footer>
            <button type="button" onClick={onClose}>
              キャンセル
            </button>
            <button className="primary" disabled={busy || !cats.length}>
              {busy ? "保存中…" : "保存する"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}

function App() {
  const [profiles, setProfiles] = useState<Profile[]>([]),
    [runs, setRuns] = useState<RunSummary[]>([]),
    [run, setRun] = useState<Run | null>(null);
  const [prefectures, setPrefectures] = useState<string[]>([]),
    [prefecture, setPrefecture] = useState(""),
    [municipality, setMunicipality] = useState(""),
    [initialLocations, setInitialLocations] = useState<Place[]>([]);
  const [health, setHealth] = useState<Health | null>(null),
    [error, setError] = useState(""),
    [devices, setDevices] = useState<{ id: number; name: string }[]>([]);
  const [device, setDevice] = useState(""),
    [manual, setManual] = useState(false),
    [operator, setOperator] = useState(
      localStorage.getItem("sumradio-operator") || "本部担当",
    );
  const [subtitle, setSubtitle] = useState(""),
    [level, setLevel] = useState(0),
    [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState<string | null>(null),
    [selectedComm, setSelectedComm] = useState<string | null>(null),
    [editor, setEditor] = useState<Task | null | undefined>(undefined);
  const [historyOpen, setHistoryOpen] = useState(false),
    [correcting, setCorrecting] = useState<Comm | null>(null),
    [correction, setCorrection] = useState("");
  const [locForm, setLocForm] = useState(false),
    [lat, setLat] = useState(""),
    [lon, setLon] = useState(""),
    [locName, setLocName] = useState("");
  const [confirm, setConfirm] = useState<{
      task: Task;
      status: Task["status"];
    } | null>(null),
    [busy, setBusy] = useState(false);
  const runRef = useRef<string | null>(null),
    versionRef = useRef(0);
  const currentRun = useRef<Run | null>(null);
  const interpretingLocation = useRef(false);
  const [approvingLocation, setApprovingLocation] = useState(false);
  const seenStatuses = useRef(new Map<string, string>());
  useEffect(() => {
    currentRun.current = run;
    if (!run) return;
    Object.values(run.communications).forEach((c) => {
      const previous = seenStatuses.current.get(c.id);
      seenStatuses.current.set(c.id, c.transcription_status);
      if (
        previous &&
        previous !== "done" &&
        c.transcription_status === "done" &&
        c.revision === 1
      ) {
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            if (
              runRef.current !== run.id ||
              document.visibilityState !== "visible"
            )
              return;
            api(
              `/runs/${run.id}/communications/${c.id}/display-metrics`,
              "POST",
              {
                actor: "画面計測",
                expected_version: c.version,
                kind: "final",
                displayed_at_epoch: Date.now() / 1000,
              },
            ).catch(() => {});
          }),
        );
      }
    });
  }, [run]);
  const refreshHealth = async () => setHealth(await api<Health>("/health"));
  const reload = async (id = runRef.current) => {
    if (!id) return;
    const next = await api<Run>(`/runs/${id}`);
    if (runRef.current === id && next.version >= versionRef.current) {
      versionRef.current = next.version;
      setRun(next);
    }
  };
  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
      await reload().catch(() => {});
    }
  };
  useEffect(() => {
    act(async () => {
      setProfiles(await api<Profile[]>("/profiles"));
      setRuns(await api<RunSummary[]>("/runs"));
      setPrefectures(
        (await api<{ prefectures: string[] }>("/regions")).prefectures,
      );
      await refreshHealth();
      try {
        setDevices(await api("/devices"));
      } catch (e) {
        setError((e as Error).message);
      }
    });
    const events = new EventSource("/api/events");
    events.onopen = () => setConnected(true);
    events.onerror = () => setConnected(false);
    events.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.type === "health") refreshHealth().catch(() => {});
      if (["state", "resync"].includes(data.type)) {
        if (data.type === "resync" || data.run_id === runRef.current)
          reload().catch(() => {});
        if (data.type === "resync") refreshHealth().catch(() => {});
      }
      if (data.run_id === runRef.current) {
        if (data.type === "subtitle") {
          setSubtitle(data.text);
          requestAnimationFrame(() =>
            requestAnimationFrame(() => {
              const c =
                currentRun.current?.communications[data.communication_id];
              if (
                !c ||
                runRef.current !== data.run_id ||
                document.visibilityState !== "visible"
              )
                return;
              api(
                `/runs/${data.run_id}/communications/${c.id}/display-metrics`,
                "POST",
                {
                  actor: "画面計測",
                  expected_version: c.version,
                  kind: "subtitle",
                  displayed_at_epoch: Date.now() / 1000,
                  captured_at_epoch: data.captured_at,
                },
              ).catch(() => {});
            }),
          );
        }
        if (data.type === "level") setLevel(data.level);
        if (data.type === "audio_error") setError(data.error);
      }
    };
    return () => events.close();
  }, []);
  const openRun = async (id: string) => {
    if (health?.recording_run_id)
      throw new Error("録音を停止してから切り替えてください");
    runRef.current = id;
    versionRef.current = 0;
    setSelected(null);
    setSelectedComm(null);
    setSubtitle("");
    setLocForm(false);
    setInitialLocations([]);
    await reload(id);
    const locations = await api<Place[]>(`/runs/${id}/locations`);
    if (runRef.current === id) setInitialLocations(locations);
  };
  const tasks = run ? Object.values(run.tasks) : [],
    comms = run ? Object.values(run.communications) : [];
  const task = tasks.find((t) => t.id === selected),
    profile = profiles.find((p) => p.id === run?.demo_profile_id);
  const recording = !!run && health?.recording_run_id === run.id;
  const chooseTask = (id: string) => {
    setSelected(id);
    setLocForm(false);
  };
  const refreshRuns = async () => setRuns(await api<RunSummary[]>("/runs"));
  const saveTask = async (body: unknown) => {
    if (!run) return;
    await act(async () => {
      if (editor)
        await api(`/runs/${run.id}/tasks/${editor.id}`, "PUT", {
          ...(body as object),
          actor: operator,
          expected_version: editor.version,
        });
      else
        await api(`/runs/${run.id}/tasks`, "POST", {
          ...(body as object),
          actor: operator,
        });
      await reload();
      setEditor(undefined);
    });
  };
  const transition = async () => {
    if (!run || !confirm) return;
    setBusy(true);
    await act(async () => {
      await api(`/runs/${run.id}/tasks/${confirm.task.id}/transition`, "POST", {
        expected_version: confirm.task.version,
        actor: operator,
        status: confirm.status,
      });
      setConfirm(null);
      await reload();
    });
    setBusy(false);
  };
  const locationConfirm = async (candidateId?: string) => {
    if (!task || !run) return;
    await act(async () => {
      const body = candidateId
        ? { candidate_id: candidateId }
        : {
            manual: {
              id: "manual_" + crypto.randomUUID(),
              name: locName,
              municipality: task.place?.municipality || "",
              lat: Number(lat),
              lon: Number(lon),
              precision: "specific",
              source: "manual",
            },
          };
      await api(`/runs/${run.id}/tasks/${task.id}/location`, "POST", {
        ...body,
        actor: operator,
        expected_version: task.version,
      });
      setLocForm(false);
      await reload();
    });
  };
  const approveInterpretedLocation = async (
    option: InterpretationLocationOption,
  ) => {
    if (!run || interpretingLocation.current) return;
    const sourceRun = run.id;
    interpretingLocation.current = true;
    setApprovingLocation(true);
    try {
      await act(async () => {
        await api(
          `/runs/${sourceRun}/tasks/${option.task_id}/interpretation-location`,
          "POST",
          {
            actor: operator,
            expected_version: option.task_version,
            communication_id: option.communication_id,
            revision: option.revision,
            interpretation_index: option.interpretation_index,
            location_id: option.location.id,
          },
        );
        await reload(sourceRun);
        if (runRef.current === sourceRun) chooseTask(option.task_id);
      });
    } finally {
      interpretingLocation.current = false;
      setApprovingLocation(false);
    }
  };
  const interpretationLocations = (
    comm: Comm,
    index: number,
    taskId?: string,
  ) => {
    const options = (run?.interpretation_locations ?? []).filter(
      (p) =>
        p.communication_id === comm.id &&
        p.revision === comm.revision &&
        p.interpretation_index === index &&
        (!taskId || p.task_id === taskId),
    );
    const approved = tasks.filter((t) => {
      const p = t.location.interpretation;
      return (
        p?.communication_id === comm.id &&
        p.revision === comm.revision &&
        p.interpretation_index === index &&
        (!taskId || t.id === taskId)
      );
    });
    return (
      <>
        {options.map((p) => (
          <div
            className="interpretation-location location-choice"
            key={p.task_id}
          >
            <strong>
              {p.location.name} · {p.location.municipality}
            </strong>
            <small>{tasks.find((t) => t.id === p.task_id)?.title}</small>
            <small>
              {p.location.precision === "representative"
                ? "公園・橋などの代表点です。現場や入口の正確な位置ではありません。"
                : "登録済みの詳細地点です。"}
            </small>
            <button
              className="primary"
              disabled={approvingLocation || !operator.trim()}
              onClick={() => approveInterpretedLocation(p)}
            >
              <Check size={14} />「{p.location.name}」として承認・地図表示
            </button>
            <small>
              地名の解釈と場所だけを確認します。タスクの状態・数量は変更しません。
            </small>
          </div>
        ))}
        {approved.map((t) => (
          <p className="interpretation-approved" key={t.id}>
            <Check size={14} />
            場所の解釈を確認済み：{t.location.confirmed?.name}（{t.title}）
          </p>
        ))}
      </>
    );
  };
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-icon">
            <Radio size={25} />
          </span>
          <strong>
            sumradio<span>交信を、次の対応へ。</span>
          </strong>
        </div>
        <div className="top-right">
          <span className={"connection " + (connected ? "online" : "")}>
            ● {connected ? "ローカル接続" : "再接続中"}
          </span>
          <label className="operator">
            操作者
            <input
              aria-label="操作者"
              value={operator}
              onChange={(e) => {
                setOperator(e.target.value);
                localStorage.setItem("sumradio-operator", e.target.value);
              }}
            />
          </label>
        </div>
      </header>
      {error && (
        <div className="error-banner" role="alert">
          {error}
          <button onClick={() => setError("")} aria-label="エラーを閉じる">
            ×
          </button>
        </div>
      )}
      {!run || !profile ? (
        <main className="welcome">
          <span className="eyebrow">RADIO OPERATIONS WORKSPACE</span>
          <h1>
            交信を記録し、
            <br />
            状況をひとつの画面に。
          </h1>
          <p className="intro">
            音声を文字に。報告と要請を整理して、
            <br />
            本部で確認する次の対応につなげます。
          </p>
          <section className="region-setup" aria-labelledby="region-title">
            <div>
              <h2 id="region-title">
                <MapPin size={18} /> 対象地域を指定
              </h2>
              <p>
                実在デモ・通常利用の地名候補を絞ります。指定せずに始めることもできます。
              </p>
            </div>
            <div className="region-fields">
              <label>
                都道府県
                <select
                  aria-label="都道府県"
                  value={prefecture}
                  onChange={(e) => {
                    setPrefecture(e.target.value);
                    setMunicipality("");
                  }}
                >
                  <option value="">指定しない</option>
                  {prefectures.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                市区町村（任意）
                <input
                  value={municipality}
                  onChange={(e) => setMunicipality(e.target.value)}
                  disabled={!prefecture}
                  maxLength={50}
                  placeholder="例：台東区、横浜市港北区"
                  list="region-municipalities"
                />
              </label>
              <datalist id="region-municipalities">
                {Array.from(
                  new Set(
                    profiles
                      .flatMap((p) =>
                        p.locations.flatMap((l) => l.municipality.split("・")),
                      )
                      .filter(
                        (name) => prefecture && name.startsWith(prefecture),
                      )
                      .map((name) => name.slice(prefecture.length)),
                  ),
                ).map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            </div>
            <p className="region-note">
              {prefecture
                ? `今回の対象：${prefecture}${municipality.trim()}。`
                : "現在は地域指定なし。"}
              地域は実施回ごとに保存します。架空デモは青葉地区固定です。
            </p>
          </section>
          <div className="profile-grid">
            {profiles.map((p, i) => (
              <button
                key={p.id}
                className={"profile-card profile-" + p.id}
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  act(async () => {
                    const r = await api<Run>("/runs", "POST", {
                      demo_profile_id: p.id,
                      region:
                        p.id !== "aoba" && prefecture
                          ? { prefecture, municipality: municipality.trim() }
                          : null,
                    });
                    await openRun(r.id);
                    await refreshRuns();
                  }).finally(() => setBusy(false));
                }}
              >
                <div className="profile-top">
                  <span>0{i + 1}</span>
                  {p.training ? <MapIcon size={22} /> : <Radio size={22} />}
                </div>
                <h2>{p.name}</h2>
                <p>
                  {p.training
                    ? "訓練：状況は架空です"
                    : "登録地点と設定済みの地名検索を使用"}
                </p>
                <div className="profile-bottom">
                  {p.locations.length} 登録地点 <ArrowRight size={20} />
                </div>
              </button>
            ))}
          </div>
          <section className="past-runs">
            <h2>過去の実施回</h2>
            {runs.length ? (
              runs.map((r) => (
                <button key={r.id} onClick={() => act(() => openRun(r.id))}>
                  <span>{r.title}</span>
                  <span className="past-region">{regionLabel(r.region)}</span>
                  <small>{date(r.created_at)}</small>
                  <ArrowRight size={16} />
                </button>
              ))
            ) : (
              <p>まだ記録はありません。デモを選んで始めましょう。</p>
            )}
          </section>
          {health && (
            <details className="health-details">
              <summary>起動状態を確認</summary>
              <p>
                音声認識：{health.whisper} ／ 整理モデル：{health.codex_model}
              </p>
              {health.whisper_error && <p>{health.whisper_error}</p>}
              {health.phonetic_error && <p>{health.phonetic_error}</p>}
              <p>
                {health.codex_available
                  ? "Codex CLIを検出しました"
                  : "Codex CLIが見つかりません"}
              </p>
              <p>保存先：{health.data_dir}</p>
              {health.storage_errors.map((e, i) => (
                <p key={i}>{e}</p>
              ))}
            </details>
          )}
        </main>
      ) : (
        <main className="workspace">
          <div className="workspace-title">
            <div>
              <button
                className="text-button back"
                disabled={recording}
                onClick={() =>
                  act(async () => {
                    await refreshRuns();
                    runRef.current = null;
                    setRun(null);
                  })
                }
              >
                <ChevronLeft size={15} /> 実施回を切り替え
              </button>
              <h1>{run.title}</h1>
              <div className="region-badge" aria-label="この実施回の対象地域">
                <MapPin size={14} /> 対象地域：
                {run.demo_profile_id === "aoba"
                  ? "架空の青葉地区"
                  : regionLabel(run.region)}
              </div>
              <p>
                <span className="training-label">
                  {profile.training ? "訓練：状況は架空" : "通常利用"}
                </span>
                <span>{date(run.created_at)}</span>
                <span>
                  {profile.training
                    ? "事前登録の位置を使用"
                    : "登録地点・地名検索"}
                </span>
              </p>
            </div>
            <button onClick={() => setHistoryOpen(true)}>
              <History size={16} /> 操作履歴
            </button>
          </div>
          <section className="capture-bar">
            <div className="capture-controls">
              <label>
                <span>入力マイク</span>
                <select
                  aria-label="入力マイク"
                  disabled={recording}
                  value={device}
                  onChange={(e) => setDevice(e.target.value)}
                >
                  <option value="">システムの既定</option>
                  {devices.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="check-line">
                <input
                  type="checkbox"
                  checked={manual}
                  disabled={recording}
                  onChange={(e) => setManual(e.target.checked)}
                />
                手動で区切る
              </label>
              <button
                className={recording ? "stop-button" : "primary"}
                disabled={busy || !operator.trim()}
                onClick={() => {
                  setBusy(true);
                  act(async () => {
                    await api(
                      `/runs/${run.id}/recording/${recording ? "stop" : "start"}`,
                      "POST",
                      recording
                        ? undefined
                        : {
                            device: device === "" ? null : Number(device),
                            manual_mode: manual,
                          },
                    );
                    await refreshHealth();
                    if (recording) setSubtitle("");
                  }).finally(() => setBusy(false));
                }}
              >
                {recording ? <Square size={16} /> : <Mic size={16} />}{" "}
                {recording ? "録音を停止" : "録音を開始"}
              </button>
            </div>
            <div className="live-line">
              <div className={"live-dot " + (recording ? "active" : "")} />
              <span>{recording ? "収音中" : "待機中"}</span>
              <div className="meter">
                <i style={{ width: Math.min(level * 400, 100) + "%" }} />
              </div>
              <small>
                {manual ? "停止ボタンで交信を確定" : "無音5秒で交信を確定"}
              </small>
            </div>
          </section>
          <section className="subtitle-panel">
            <span className="eyebrow">
              <Activity size={13} /> LIVE TRANSCRIPT
            </span>
            <p className={subtitle ? "" : "placeholder"}>
              {subtitle ||
                (recording
                  ? "交信を受信しています…"
                  : "録音を開始すると、発話中の字幕がここに表示されます。")}
            </p>
            <small>暫定字幕 · 発話終了後に確定</small>
          </section>
          {health?.whisper !== "ready" && (
            <div className="subtle-alert">
              音声認識モデル：
              {health?.whisper === "loading" ? "読み込み中" : "未準備"}。
              {health?.whisper_error &&
                "セットアップ手順でモデルを準備してください。"}
              録音・手動登録は利用できます。
            </div>
          )}
          <div className="main-grid">
            <section className="communications panel">
              <div className="section-head">
                <h2>
                  <Headphones size={17} />
                  交信記録 <span className="count">{comms.length}</span>
                </h2>
                <span className="muted">受信順</span>
              </div>
              <div className="comm-scroll">
                {!comms.length ? (
                  <div className="empty-state">
                    <Radio size={30} />
                    <h3>最初の交信を待っています</h3>
                    <p>
                      確定した文字記録と原音を
                      <br />
                      ここから確認できます。
                    </p>
                  </div>
                ) : (
                  comms
                    .slice()
                    .reverse()
                    .map((c) => (
                      <article
                        id={c.id}
                        key={c.id}
                        className={
                          "comm-card " +
                          (selectedComm === c.id ? "focused" : "")
                        }
                      >
                        <div className="comm-meta">
                          <time>{time(c.received_at)}</time>
                          <span className={"badge " + c.extraction_status}>
                            {c.transcription_status !== "done"
                              ? {
                                  recording: "録音中",
                                  queued: "文字起こし待ち",
                                  running: "文字起こし中",
                                  failed: "文字起こし失敗",
                                  interrupted: "中断",
                                }[c.transcription_status]
                              : extractionLabels[c.extraction_status]}
                          </span>
                        </div>
                        <h3>
                          {c.extraction?.sender || "発信者 未確認"}{" "}
                          <span>
                            → {c.extraction?.recipient || "宛先 未確認"}
                          </span>
                        </h3>
                        <p className="transcript">
                          {currentText(c) || "音声を記録しています…"}
                        </p>
                        {c.corrections.length > 0 && (
                          <details>
                            <summary>
                              認識原文・訂正履歴（現在 r{c.revision}）
                            </summary>
                            <p>{c.original_text}</p>
                            {c.corrections.map((x) => (
                              <p key={x.revision}>
                                {date(x.at)} {x.actor} r{x.revision}
                                <br />
                                {x.text}
                              </p>
                            ))}
                          </details>
                        )}
                        {c.original_audio && (
                          <audio
                            controls
                            preload="none"
                            src={`/api/runs/${run.id}/audio/${c.id}/original`}
                          />
                        )}
                        <div className="comm-actions">
                          <button
                            className="text-button"
                            disabled={[
                              "recording",
                              "queued",
                              "running",
                            ].includes(c.transcription_status)}
                            onClick={() => {
                              setCorrecting(c);
                              setCorrection(currentText(c));
                            }}
                          >
                            <Pencil size={13} />
                            訂正
                          </button>
                          {c.extraction_status === "failed" &&
                            c.transcription_status === "done" && (
                              <button
                                className="text-button"
                                onClick={() =>
                                  act(async () => {
                                    await api(
                                      `/runs/${run.id}/communications/${c.id}/retry`,
                                      "POST",
                                      {
                                        expected_version: c.version,
                                        actor: operator,
                                      },
                                    );
                                    await reload();
                                  })
                                }
                              >
                                <RefreshCw size={13} />
                                整理を再試行
                              </button>
                            )}
                        </div>
                        {c.error && <p className="error-text">{c.error}</p>}
                        {c.metrics.audio_warning && (
                          <p className="error-text">
                            {c.metrics.audio_warning}
                          </p>
                        )}
                        {c.extraction &&
                          c.extraction_revision !== c.revision && (
                            <p className="notice">
                              訂正前の整理結果です。再整理を待っています。
                            </p>
                          )}
                        {c.extraction && c.extraction.people.length > 0 && (
                          <p className="notice">
                            人数・内訳：
                            {c.extraction.people
                              .map(
                                (q) =>
                                  `${q.description} ${q.value ?? "未確認"}${q.unit ?? ""}`,
                              )
                              .join(" ／ ")}
                          </p>
                        )}
                        {c.extraction?.notices.map((n, i) => (
                          <div className="notice" key={i}>
                            {n.text}
                            {n.related_task_ids.map((id) => (
                              <button
                                key={id}
                                className="text-button"
                                onClick={() => chooseTask(id)}
                              >
                                関連タスク
                              </button>
                            ))}
                          </div>
                        ))}
                        {!!c.extraction?.transcript_interpretations?.length && (
                          <div
                            className="notice"
                            aria-label="聞き取りの確認候補"
                          >
                            <strong>
                              聞き取り要確認（原文は変更していません）
                            </strong>
                            {c.extraction.transcript_interpretations.map(
                              (p, i) => (
                                <div key={i}>
                                  <p>
                                    解釈候補：
                                    {p.possible_meaning ||
                                      "特定できません。原音を確認してください。"}
                                  </p>
                                  <p>{p.reason}</p>
                                  <blockquote>{p.evidence.quote}</blockquote>
                                  {interpretationLocations(c, i)}
                                </div>
                              ),
                            )}
                          </div>
                        )}
                        {!!c.extraction?.phonetic_interpretations.length && (
                          <details>
                            <summary>
                              通話表の解釈（原文は変更しません）
                            </summary>
                            {c.extraction.phonetic_interpretations.map(
                              (p, i) => (
                                <p key={i}>
                                  「{p.original}」→ {p.characters.join("・")}
                                  {p.interpreted && ` ／ ${p.interpreted}`}
                                  <br />
                                  根拠 r{p.evidence.revision}：「
                                  {p.evidence.quote}」
                                </p>
                              ),
                            )}
                          </details>
                        )}
                      </article>
                    ))
                )}
              </div>
            </section>
            <section className="board panel">
              <div className="section-head">
                <h2>対応ボード</h2>
                <button className="text-button" onClick={() => setEditor(null)}>
                  <Plus size={16} />
                  手動登録
                </button>
              </div>
              <div className="board-columns">
                {(["candidate", "unhandled", "completed"] as const).map(
                  (status) => (
                    <section key={status} className={"board-column " + status}>
                      <div className="column-head">
                        <span className="status-dot" />
                        <h3>{statuses[status]}</h3>
                        <span className="count">
                          {tasks.filter((t) => t.status === status).length}
                        </span>
                      </div>
                      <div className="cards">
                        {tasks
                          .filter((t) => t.status === status)
                          .map((t) => (
                            <button
                              key={t.id}
                              className={
                                "task-card " +
                                (t.id === selected ? "selected" : "")
                              }
                              onClick={() => chooseTask(t.id)}
                            >
                              <span className={"kind " + t.kind}>
                                {t.kind === "request" ? "要請" : "状況確認"}
                              </span>
                              <h4>{t.title}</h4>
                              <p>
                                <MapPin size={12} />
                                {t.location.confirmed?.name ||
                                  t.place?.expression ||
                                  "場所未確認"}
                              </p>
                              <div className="task-tags">
                                {t.map_categories.map((c) => (
                                  <span key={c}>
                                    {symbols[c]} {categoryLabels[c]}
                                  </span>
                                ))}
                              </div>
                              {t.location.status !== "confirmed" && (
                                <small className="location-pending">
                                  場所未確認
                                </small>
                              )}
                              {t.uncertainties.length > 0 && (
                                <small className="notice-count">
                                  内容の確認事項 {t.uncertainties.length}件
                                </small>
                              )}
                              {t.related_task_ids.length > 0 && (
                                <small>
                                  関連タスク {t.related_task_ids.length}件
                                </small>
                              )}
                              {t.notices.length > 0 && (
                                <small className="notice-count">
                                  確認事項 {t.notices.length}件
                                </small>
                              )}
                            </button>
                          ))}
                        {!tasks.some((t) => t.status === status) && (
                          <p className="column-empty">
                            {status === "candidate"
                              ? "交信から候補を整理します"
                              : status === "unhandled"
                                ? "確認・承認した候補"
                                : "対応が完了したタスク"}
                          </p>
                        )}
                      </div>
                    </section>
                  ),
                )}
              </div>
            </section>
          </div>
          <section className="map-section panel">
            <div className="section-head map-heading">
              <h2>
                <MapPin size={17} />
                対応マップ
              </h2>
              <div className="map-legend" aria-label="地図記号の凡例">
                {Object.entries(categoryLabels).map(([c, label]) => (
                  <span key={c}>
                    {symbols[c as Category]} {label}
                  </span>
                ))}
              </div>
              <p className="map-symbol-note">
                🩸は負傷者の記号です。出血量・重症度・対応の優先順位は示しません。複数分類は最大3記号と残りの数を表示します。
              </p>
            </div>
            <div className="map-layout">
              <div className="map-wrap">
                <MapPanel
                  tasks={tasks}
                  initialLocations={initialLocations}
                  tileUrl={
                    health?.tile_url ||
                    "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                  }
                  onSelect={chooseTask}
                  selected={selected}
                  onPick={
                    locForm
                      ? (lat, lon) => {
                          setLat(lat.toFixed(7));
                          setLon(lon.toFixed(7));
                        }
                      : undefined
                  }
                />
              </div>
              <aside className="task-detail">
                {task ? (
                  <>
                    <div className="section-head">
                      <span className="kind">
                        {task.kind === "request" ? "要請" : "状況確認"} ·{" "}
                        {statuses[task.status]}
                      </span>
                      <button
                        className="text-button"
                        onClick={() => setEditor(task)}
                      >
                        <Pencil size={14} />
                        編集
                      </button>
                    </div>
                    <h2>{task.title}</h2>
                    <p>{task.description}</p>
                    <p className="muted">
                      担当：{task.assignee || "未確認"} ／ 期限：
                      {task.deadline || "未確認"}
                    </p>
                    {task.quantities.map((q, i) => (
                      <p key={i}>
                        {q.description}：{q.value ?? "未確認"} {q.unit}
                      </p>
                    ))}
                    <h3>場所</h3>
                    <p>
                      {task.location.confirmed?.name ||
                        task.place?.expression ||
                        "場所未確認"}
                    </p>
                    {task.location.confirmed && (
                      <p className="muted">
                        {task.location.confirmed.precision === "representative"
                          ? "代表点（現場の詳細位置ではありません）"
                          : "詳細地点"}{" "}
                        · {task.location.confirmed.lat.toFixed(5)},{" "}
                        {task.location.confirmed.lon.toFixed(5)}
                      </p>
                    )}
                    {task.location.reason && (
                      <p className="muted">{task.location.reason}</p>
                    )}
                    {comms.map((c) =>
                      c.extraction?.transcript_interpretations?.map((_, i) => (
                        <React.Fragment key={`${c.id}-${i}`}>
                          {interpretationLocations(c, i, task.id)}
                        </React.Fragment>
                      )),
                    )}
                    {task.location.candidates.map((p) => (
                      <div className="location-choice" key={p.id}>
                        <strong>{p.name}</strong>
                        <small>
                          {p.municipality} ·{" "}
                          {p.precision === "representative"
                            ? "代表点"
                            : "詳細地点"}
                        </small>
                        <button onClick={() => locationConfirm(p.id)}>
                          <Check size={14} />
                          この位置を確認
                        </button>
                      </div>
                    ))}
                    <button
                      className="text-button"
                      onClick={() => {
                        setLocForm(!locForm);
                        setLocName(
                          task.location.confirmed?.name ||
                            task.place?.expression ||
                            "",
                        );
                        setLat(task.location.confirmed?.lat.toString() || "");
                        setLon(task.location.confirmed?.lon.toString() || "");
                      }}
                    >
                      <MapPin size={14} />
                      手動で位置を指定・訂正
                    </button>
                    {locForm && (
                      <form
                        className="location-form"
                        onSubmit={(e) => {
                          e.preventDefault();
                          locationConfirm();
                        }}
                      >
                        <label>
                          表示名
                          <input
                            required
                            value={locName}
                            onChange={(e) => setLocName(e.target.value)}
                          />
                        </label>
                        <div className="form-grid">
                          <label>
                            緯度
                            <input
                              required
                              type="number"
                              step="any"
                              min="-90"
                              max="90"
                              value={lat}
                              onChange={(e) => setLat(e.target.value)}
                            />
                          </label>
                          <label>
                            経度
                            <input
                              required
                              type="number"
                              step="any"
                              min="-180"
                              max="180"
                              value={lon}
                              onChange={(e) => setLon(e.target.value)}
                            />
                          </label>
                        </div>
                        <small>地図クリックでも入力できます。</small>
                        <button className="primary">位置を確定</button>
                      </form>
                    )}
                    <h3>根拠となる交信</h3>
                    {task.manual_reason && (
                      <p>人間の記録：{task.manual_reason}</p>
                    )}
                    {task.evidence.map((ev, i) => (
                      <blockquote key={i}>
                        <p>{ev.quote}</p>
                        <button
                          className="text-button"
                          onClick={() => {
                            setSelectedComm(ev.communication_id);
                            document
                              .getElementById(ev.communication_id)
                              ?.scrollIntoView({
                                behavior: "smooth",
                                block: "center",
                              });
                          }}
                        >
                          交信を開く · r{ev.revision} <ArrowRight size={12} />
                        </button>
                        {run.communications[ev.communication_id]?.revision !==
                          ev.revision && (
                          <strong className="error-text">
                            この根拠の交信は後から訂正されています
                          </strong>
                        )}
                      </blockquote>
                    ))}
                    {task.uncertainties.map((n, i) => (
                      <p className="notice" key={i}>
                        {n}
                      </p>
                    ))}
                    {task.notices.map((n, i) => (
                      <div className="notice" key={i}>
                        {n.text}
                        {n.evidence.map((e, j) => (
                          <blockquote key={j}>{e.quote}</blockquote>
                        ))}
                      </div>
                    ))}
                    {task.related_task_ids.map((id) => (
                      <button
                        className="text-button"
                        key={id}
                        onClick={() => chooseTask(id)}
                      >
                        関連：{run.tasks[id]?.title || id}
                      </button>
                    ))}
                    <div className="task-actions">
                      {task.status === "candidate" && (
                        <>
                          <button
                            className="primary"
                            onClick={() =>
                              setConfirm({ task, status: "unhandled" })
                            }
                          >
                            <Check size={15} />
                            承認して未対応へ
                          </button>
                          <button
                            onClick={() =>
                              setConfirm({ task, status: "discarded" })
                            }
                          >
                            破棄
                          </button>
                        </>
                      )}
                      {task.status === "unhandled" && (
                        <button
                          className="primary"
                          onClick={() =>
                            setConfirm({ task, status: "completed" })
                          }
                        >
                          <Check size={15} />
                          対応済みにする
                        </button>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="empty-state">
                    <MapPin size={28} />
                    <h3>場所と根拠を確認</h3>
                    <p>
                      カードか地図のピンを選ぶと
                      <br />
                      詳細と操作が表示されます。
                    </p>
                    <small>
                      破線のピン：未確認
                      <br />
                      実線のピン：未対応・位置確認済み
                    </small>
                  </div>
                )}
              </aside>
            </div>
          </section>
          <footer className="workspace-footer">
            <span>録音と文字記録はこのPCに保存</span>
            <span>
              {
                tasks.filter(
                  (t) =>
                    t.location.status !== "confirmed" &&
                    ["candidate", "unhandled"].includes(t.status),
                ).length
              }
              件の場所が未確認 · AI整理結果は人間が確認
            </span>
          </footer>
        </main>
      )}
      {editor !== undefined && run && (
        <TaskEditor
          task={editor}
          run={run}
          onSave={saveTask}
          onClose={() => setEditor(undefined)}
        />
      )}
      {correcting && run && (
        <div className="modal-backdrop">
          <section className="modal" role="dialog" aria-label="交信を訂正">
            <h2>文字記録を訂正</h2>
            <p>認識原文を残し、訂正版から再整理します。</p>
            <textarea
              rows={7}
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
            />
            <footer>
              <button onClick={() => setCorrecting(null)}>キャンセル</button>
              <button
                className="primary"
                disabled={!correction.trim()}
                onClick={() =>
                  act(async () => {
                    await api(
                      `/runs/${run.id}/communications/${correcting.id}/corrections`,
                      "POST",
                      {
                        expected_version: correcting.version,
                        actor: operator,
                        text: correction,
                      },
                    );
                    setCorrecting(null);
                    await reload();
                  })
                }
              >
                訂正を保存
              </button>
            </footer>
          </section>
        </div>
      )}
      {confirm && (
        <div className="modal-backdrop">
          <section
            className="modal small-modal"
            role="dialog"
            aria-label="状態変更を確認"
          >
            <h2>{statuses[confirm.status]}へ移しますか？</h2>
            <p>{confirm.task.title}</p>
            {confirm.status === "completed" && (
              <p>
                {confirm.task.kind === "situation_confirmation"
                  ? "報告を確認し、対応の要否を判断したことを確認してください。"
                  : "要請された対応が完了したことを確認してください。"}
              </p>
            )}
            <footer>
              <button onClick={() => setConfirm(null)}>キャンセル</button>
              <button
                className="primary"
                disabled={busy || !operator.trim()}
                onClick={transition}
              >
                確認して変更
              </button>
            </footer>
          </section>
        </div>
      )}
      {historyOpen && run && (
        <div className="modal-backdrop">
          <section
            className="modal history-modal"
            role="dialog"
            aria-label="操作履歴"
          >
            <div className="section-head">
              <h2>操作履歴</h2>
              <button onClick={() => setHistoryOpen(false)}>
                <X size={17} />
                閉じる
              </button>
            </div>
            {!run.history.length && <p>まだ操作履歴はありません。</p>}
            {run.history
              .slice()
              .reverse()
              .map((h) => (
                <article className="history-item" key={h.id}>
                  <small>
                    {date(h.at)} · {h.actor}
                  </small>
                  <p>
                    {{
                      task_created: "候補を登録",
                      task_edited: "タスクを編集",
                      task_transition: "状態を変更",
                      location_confirmed: "位置を確認",
                      location_interpretation_confirmed:
                        "地名の聞き取りを承認・地図表示",
                      transcript_corrected: "交信を訂正",
                      communication_extracted:
                        "交信を整理（結果と対象版を保存）",
                    }[h.action] || h.action}{" "}
                    — {run.tasks[h.target_id]?.title || h.target_id}
                  </p>
                  {h.action === "task_transition" && (
                    <p>
                      {statuses[h.before?.status as Task["status"]]} →{" "}
                      {statuses[h.after?.status as Task["status"]]}
                    </p>
                  )}
                  <details>
                    <summary>変更内容・位置の記録</summary>
                    <pre>
                      {JSON.stringify(
                        { before: h.before, after: h.after },
                        null,
                        2,
                      )}
                    </pre>
                  </details>
                  {run.tasks[h.target_id] && (
                    <button
                      className="text-button"
                      onClick={() => {
                        chooseTask(h.target_id);
                        setHistoryOpen(false);
                      }}
                    >
                      詳細を開く
                    </button>
                  )}
                </article>
              ))}
          </section>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);

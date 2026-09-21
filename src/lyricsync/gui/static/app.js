"use strict";

const $ = (id) => document.getElementById(id);
const api = async (url, options) => {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
};
const postJSON = (url, body) =>
  api(url, { method: "POST", headers: { "Content-Type": "application/json" },
             body: JSON.stringify(body || {}) });

const state = {
  folder: null,
  tracks: [],
  selected: new Set(),   // library checkboxes
  review: null,          // index of the track loaded in the reviewer
  lines: [],
  starts: [],            // edited start times: the source of truth for saving
  original: [],
  offsetMs: 0,
  cursor: -1,            // selected lyric line
  active: -1,            // line currently being sung
  jobId: null,
  poll: null,
};

const fmtTime = (s) => {
  if (s === null || s === undefined) return "--:--";
  const m = Math.floor(s / 60), r = s - m * 60;
  return `${String(m).padStart(2, "0")}:${r.toFixed(2).padStart(5, "0")}`;
};
const fmtDuration = (s) => {
  if (!s) return "";
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s - m * 60)).padStart(2, "0")}`;
};
const dirty = () => state.starts.some((s, i) => s !== state.original[i]);

function setStatus(text, kind) {
  const el = $("status");
  el.textContent = text;
  el.className = kind || "";
}

/* ------------------------------------------------------------------ tabs */

function showTab(name) {
  document.querySelectorAll("nav.tabs button").forEach((b) =>
    b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "history") loadHistory();
  if (name === "inspect") loadInspect();
}

/* --------------------------------------------------------------- library */

async function loadLibrary(path) {
  try {
    const data = path
      ? await postJSON("/api/library", { path })
      : await api("/api/library");
    state.folder = data.folder;
    state.tracks = data.tracks;
    state.selected = new Set(data.tracks.map((t) => t.index));
    if ($("folder").value !== (data.folder || "")) $("folder").value = data.folder || "";
    renderLibrary();
    setStatus(`${data.tracks.length} pista(s) en la carpeta`);
  } catch (err) {
    setStatus(String(err.message || err), "err");
  }
}

/** Build the state badge as a node: the tooltip text comes from the run log. */
function trackBadge(track) {
  const el = document.createElement("span");
  el.className = "badge";

  if (!track.hasLyrics) {
    el.classList.add("none");
    el.textContent = "sin letra";
  } else if (track.lastStatus === "review") {
    // Flagged by the validator: written, but the timings need a listen.
    el.classList.add("warn");
    el.textContent = "revisar";
    el.title = track.lastMessage || "Los tiempos no convencieron al validador";
  } else {
    el.classList.add("ok");
    el.textContent = track.wordLevel ? "por palabra" : "sincronizada";
  }
  return el;
}

function renderLibrary() {
  const body = $("libbody");
  body.innerHTML = "";

  if (!state.tracks.length) {
    $("libempty").style.display = "block";
    $("libtable").style.display = "none";
    updateLibraryControls();
    return;
  }
  $("libempty").style.display = "none";
  $("libtable").style.display = "table";

  for (const track of state.tracks) {
    const row = document.createElement("tr");

    const pick = document.createElement("td");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.selected.has(track.index);
    box.onchange = () => {
      box.checked ? state.selected.add(track.index) : state.selected.delete(track.index);
      updateLibraryControls();
    };
    pick.append(box);

    const name = document.createElement("td");
    name.className = "name";
    const main = document.createElement("span");
    main.textContent = [track.artist, track.title].filter(Boolean).join(" — ") || track.name;
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = track.name;
    name.append(main, sub);

    const dur = document.createElement("td");
    dur.className = "num";
    dur.textContent = fmtDuration(track.duration);

    const badge = document.createElement("td");
    badge.append(trackBadge(track));

    const lines = document.createElement("td");
    lines.className = "num";
    lines.textContent = track.lines || "";

    const actions = document.createElement("td");
    const open = document.createElement("button");
    open.className = "act";
    open.textContent = "Revisar";
    open.disabled = !track.hasLyrics;
    open.onclick = () => openReview(track.index);
    actions.append(open);

    row.append(pick, name, dur, badge, lines, actions);
    body.append(row);
  }
  updateLibraryControls();
}

function updateLibraryControls() {
  const count = state.selected.size;
  const busy = state.jobId !== null;
  $("count").textContent = count ? `${count} seleccionada(s)` : "ninguna seleccionada";
  for (const id of ["dosync", "dostrip"]) $(id).disabled = !count || busy;
  $("selall").disabled = !state.tracks.length;
}

/* ----------------------------------------------------------------- jobs */

async function startJob(kind, options) {
  if (!state.selected.size) return;
  const indices = [...state.selected].sort((a, b) => a - b);
  try {
    const job = await postJSON(`/api/jobs/${kind}`, { indices, options });
    state.jobId = job.id;
    $("job").classList.remove("hidden");
    $("joblog").innerHTML = "";
    renderJob(job);
    updateLibraryControls();
    state.poll = setInterval(pollJob, 400);
  } catch (err) {
    setStatus(String(err.message || err), "err");
  }
}

async function pollJob() {
  if (!state.jobId) return;
  try {
    const job = await api(`/api/jobs/${state.jobId}`);
    renderJob(job);
    if (job.status !== "running") finishJob(job);
  } catch (err) {
    finishJob(null);
    setStatus(String(err.message || err), "err");
  }
}

function renderJob(job) {
  $("jobtitle").textContent =
    `${job.kind === "sync" ? "Sincronizando" : "Limpiando"} — ${job.done}/${job.total}`;
  $("jobbar").max = job.total;
  $("jobbar").value = job.done;
  $("jobnow").textContent = job.current ? job.current.split(/[/\\]/).pop() : "";

  const log = $("joblog");
  for (let i = log.childElementCount; i < job.events.length; i++) {
    const event = job.events[i];
    const row = document.createElement("div");
    row.className = event.status;
    const detail = event.message || (event.lines ? `${event.lines} líneas` : "");
    row.textContent = `${event.status.padEnd(8)} ${event.item}${detail ? "  " + detail : ""}`;
    log.append(row);
    for (const warning of event.warnings || []) {
      const note = document.createElement("div");
      note.className = "review";
      note.textContent = `         ! ${warning}`;
      log.append(note);
    }
  }
  log.scrollTop = log.scrollHeight;
}

function finishJob(job) {
  clearInterval(state.poll);
  state.poll = null;
  state.jobId = null;
  updateLibraryControls();
  if (job) {
    const counts = {};
    for (const e of job.events) counts[e.status] = (counts[e.status] || 0) + 1;
    const summary = Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(", ");
    setStatus(
      job.status === "cancelled" ? `Cancelado tras ${job.done} pista(s)` : `Terminado: ${summary || "sin cambios"}`,
      job.status === "failed" ? "err" : "ok",
    );
  }
  loadLibrary();
}

function syncOptions() {
  return {
    offline: $("o-offline").checked,
    demucs: $("o-demucs").checked,
    asr: $("o-asr").checked,
    force: $("o-force").checked,
    backup: $("o-backup").checked,
    dryRun: $("o-dry").checked,
    enhanced: $("o-enhanced").checked,
    device: $("o-device").value,
    leadIn: parseFloat($("o-lead").value) || 0,
    sidecarOnly: $("o-sidecaronly").checked,
    translate: $("o-translate").checked,
    bilingual: $("o-bilingual").value,
  };
}

/* ---------------------------------------------------------------- review */

async function openReview(index) {
  if (dirty() && !confirm("Hay cambios sin guardar. ¿Descartarlos?")) return;
  showTab("review");
  try {
    const data = await api(`/api/track/${index}`);
    state.review = index;
    state.lines = data.lines;
    state.starts = data.lines.map((l) => l.start);
    state.original = state.starts.slice();
    state.offsetMs = 0;
    state.cursor = -1;
    state.active = -1;

    $("now").textContent = [data.artist, data.title].filter(Boolean).join(" — ") || data.name;
    $("player").src = `/api/audio/${index}`;
    renderLines();
    refreshReview();
    setStatus(data.lines.length
      ? `${data.lines.length} líneas · origen: ${data.source}`
      : "Esta pista no tiene letra sincronizada.");
  } catch (err) {
    setStatus(String(err.message || err), "err");
  }
}

function renderLines() {
  const box = $("lyrics");
  box.innerHTML = "";
  if (!state.lines.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Esta pista no tiene letra sincronizada todavía.";
    box.append(p);
    return;
  }
  state.lines.forEach((line, i) => {
    const row = document.createElement("div");
    row.className = "line";
    row.dataset.index = i;

    const stamp = document.createElement("span");
    stamp.className = "stamp";
    const text = document.createElement("span");
    text.className = "ltext";
    text.textContent = line.text;

    row.append(stamp, text);
    row.onclick = () => {
      state.cursor = i;
      if (state.starts[i] !== null) $("player").currentTime = Math.max(0, state.starts[i]);
      paint();
      refreshReview();
    };
    box.append(row);
  });
  paint();
}

/** Repaint stamps and classes without rebuilding the DOM. */
function paint() {
  document.querySelectorAll("#lyrics .line").forEach((row, i) => {
    row.querySelector(".stamp").textContent = fmtTime(state.starts[i]);
    row.classList.toggle("active", i === state.active);
    row.classList.toggle("selected", i === state.cursor);
    row.classList.toggle("edited", state.starts[i] !== state.original[i]);
  });
}

function refreshReview() {
  const changed = dirty();
  $("save").disabled = !changed;
  $("reset").disabled = !changed;
  $("tap").disabled = state.cursor < 0;
  $("offset").textContent = `${state.offsetMs > 0 ? "+" : ""}${state.offsetMs} ms`;
  $("offset").className = state.offsetMs !== 0 ? "changed" : "";
}

function nudge(ms) {
  if (!state.lines.length) return;
  const delta = ms / 1000;
  state.starts = state.starts.map((s) => (s === null ? null : Math.max(0, s + delta)));
  state.offsetMs += ms;
  paint();
  refreshReview();
}

function tap() {
  if (state.cursor < 0) return;
  state.starts[state.cursor] = Math.max(0, $("player").currentTime);
  paint();
  refreshReview();
  setStatus(`Línea ${state.cursor + 1} marcada en ${fmtTime(state.starts[state.cursor])}`);
}

function moveCursor(step) {
  if (!state.lines.length) return;
  state.cursor = state.cursor < 0
    ? 0
    : Math.min(state.lines.length - 1, Math.max(0, state.cursor + step));
  paint();
  refreshReview();
  scrollToLine(state.cursor);
}

function scrollToLine(index) {
  const row = document.querySelector(`#lyrics .line[data-index="${index}"]`);
  if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
}

async function saveReview() {
  if (state.review === null || !dirty()) return;
  $("save").disabled = true;
  setStatus("Guardando...");
  try {
    const data = await postJSON(`/api/track/${state.review}/save`, { starts: state.starts });
    state.original = state.starts.slice();
    state.offsetMs = 0;
    paint();
    refreshReview();
    const warn = (data.warnings || []).concat(data.errors || []);
    setStatus(warn.length ? `Guardado, con avisos: ${warn.join("; ")}` : "Guardado en el MP3 y en el .lrc",
              warn.length ? "" : "ok");
    loadLibrary();
  } catch (err) {
    setStatus(`No se pudo guardar: ${err.message || err}`, "err");
    refreshReview();
  }
}

function discard() {
  state.starts = state.original.slice();
  state.offsetMs = 0;
  paint();
  refreshReview();
  setStatus("Cambios descartados");
}

/* --------------------------------------------------------------- inspect */

async function loadInspect() {
  const index = state.review !== null ? state.review : (state.tracks[0] || {}).index;
  const box = $("inspectbox");
  if (index === undefined) {
    box.innerHTML = '<p class="empty">Carga una carpeta primero.</p>';
    return;
  }
  try {
    const data = await api(`/api/inspect/${index}`);
    box.innerHTML = "";

    const facts = document.createElement("dl");
    facts.className = "facts";
    const rows = [
      ["Archivo", data.name],
      ["Ruta", data.path],
      ["Frames de letra", data.frames.length ? data.frames.join(", ") : "ninguno"],
      ["Marca de lyricsync", data.marker || "ninguna"],
      ["Sidecar .lrc", data.sidecar ? "sí" : "no"],
      ["Tiempos por palabra", data.words ? "sí" : "no"],
    ];
    for (const [key, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      facts.append(dt, dd);
    }
    box.append(facts);

    const pre = document.createElement("pre");
    pre.className = "detail";
    pre.textContent = data.embedded || "(sin letra embebida)";
    box.append(pre);
  } catch (err) {
    box.innerHTML = "";
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = String(err.message || err);
    box.append(p);
  }
}

/* --------------------------------------------------------------- history */

async function loadHistory() {
  const body = $("histbody");
  body.innerHTML = "";
  try {
    const data = await api("/api/history");
    if (!data.runs.length) {
      $("histempty").style.display = "block";
      $("histtable").style.display = "none";
      return;
    }
    $("histempty").style.display = "none";
    $("histtable").style.display = "table";
    for (const run of data.runs) {
      const row = document.createElement("tr");
      for (const value of [run.status, run.name, run.source || "-", run.message || ""]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      body.append(row);
    }
  } catch (err) {
    setStatus(String(err.message || err), "err");
  }
}

/* ----------------------------------------------------------------- wiring */

document.querySelectorAll("nav.tabs button").forEach((b) => {
  b.onclick = () => showTab(b.dataset.tab);
});

$("open").onclick = () => loadLibrary($("folder").value.trim());
$("folder").onkeydown = (e) => { if (e.key === "Enter") loadLibrary($("folder").value.trim()); };
$("browse").onclick = async () => {
  setStatus("Esperando al selector de carpetas...");
  try {
    const data = await postJSON("/api/browse");
    if (!data.available) {
      setStatus("El selector del sistema no está disponible; escribe la ruta a mano.", "err");
      return;
    }
    if (data.path) { $("folder").value = data.path; loadLibrary(data.path); }
    else setStatus("");
  } catch (err) {
    setStatus(String(err.message || err), "err");
  }
};
$("reload").onclick = () => loadLibrary();

$("selall").onclick = () => {
  const all = state.selected.size === state.tracks.length;
  state.selected = all ? new Set() : new Set(state.tracks.map((t) => t.index));
  renderLibrary();
};
$("opts").onclick = () => $("options").classList.toggle("open");
$("dosync").onclick = () => startJob("sync", syncOptions());
$("dostrip").onclick = () => {
  const also = confirm("¿Borrar también los archivos .lrc que haya al lado?");
  startJob("strip", { backup: $("o-backup").checked, sidecar: also });
};
$("jobcancel").onclick = async () => {
  if (state.jobId) await postJSON(`/api/jobs/${state.jobId}/cancel`);
};

$("back").onclick = (e) => nudge(e.shiftKey ? -10 : -100);
$("fwd").onclick = (e) => nudge(e.shiftKey ? 10 : 100);
$("tap").onclick = tap;
$("save").onclick = saveReview;
$("reset").onclick = discard;
$("exportlrc").onclick = async () => {
  if (state.review === null) return;
  try {
    const data = await postJSON("/api/export", { index: state.review, enhanced: true });
    setStatus(`Exportado con tiempos por palabra: ${data.written}`, "ok");
  } catch (err) {
    setStatus(String(err.message || err), "err");
  }
};

$("player").addEventListener("timeupdate", () => {
  const time = $("player").currentTime;
  let found = -1;
  for (let i = 0; i < state.starts.length; i++) {
    if (state.starts[i] !== null && state.starts[i] <= time) found = i; else break;
  }
  if (found === state.active) return;
  state.active = found;
  paint();
  scrollToLine(found);
});

document.addEventListener("keydown", (e) => {
  const tag = e.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveReview(); return; }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (!$("tab-review").classList.contains("active")) return;

  switch (e.key) {
    case " ": e.preventDefault();
      $("player").paused ? $("player").play() : $("player").pause(); break;
    case "ArrowLeft": e.preventDefault(); nudge(e.shiftKey ? -10 : -100); break;
    case "ArrowRight": e.preventDefault(); nudge(e.shiftKey ? 10 : 100); break;
    case "ArrowUp": e.preventDefault(); moveCursor(-1); break;
    case "ArrowDown": e.preventDefault(); moveCursor(1); break;
    case "t": case "T": e.preventDefault(); tap(); break;
  }
});

window.addEventListener("beforeunload", (e) => {
  if (dirty()) { e.preventDefault(); e.returnValue = ""; }
});

loadLibrary().catch((err) => setStatus(String(err), "err"));

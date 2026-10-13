// Synty Asset Library dashboard. Talks only to this server's /api and /files
// (same origin, HttpOnly session cookie). Three.js is loaded from the CDN.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtSize = (s) => (s ? s.map((v) => v.toFixed(2)).join(" × ") + " m" : "not measured");
const fmtBytes = (n) => (n == null ? "–" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : (n / 1e3).toFixed(0) + " kB");

const selection = loadSelection();

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (r.status === 401) { showLogin(); throw new Error("unauthorized"); }
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

// ---------------------------------------------------------------- login
function showLogin() { $("#login").hidden = false; $("#app").hidden = true; }
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await fetch("/api/login", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: $("#token").value }) });
  if (r.ok) { $("#login").hidden = true; boot(); } else { $("#token").value = ""; $("#token").placeholder = "invalid token"; }
});

// ----------------------------------------------------------------- tabs
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));
function openTab(name) {
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  if (name === "packs") renderPacks();
  if (name === "jobs") renderJobs();
  if (name === "collection") renderSelection();
  try { localStorage.setItem("synty.tab", name); } catch {}
}

// --------------------------------------------------------------- status
async function renderStatus() {
  const s = await api("/api/status");
  const st = s.packs_by_status || {};
  $("#status-line").textContent =
    `${s.indexed_assets} assets indexed · ${st.indexed || 0}/${s.packs_owned} packs indexed · ` +
    `Blender ${s.blender ? "ok" : "missing"} · active jobs ${s.jobs_active.length}`;
  const sel = $("#f-pack");
  if (sel.options.length <= 1) {
    for (const p of s.indexed_packs) sel.insertAdjacentHTML("beforeend", `<option>${esc(p)}</option>`);
  }
}

// ---------------------------------------------------------------- search
function chips(a) {
  const av = a.available || {};
  const out = [`<span class="chip">${esc(a.class)}</span>`];
  out.push(av.source_file ? `<span class="chip ok">file</span>` : `<span class="chip bad">not extracted</span>`);
  if ((av.glb_cached || []).length) out.push(`<span class="chip ok">GLB ${esc(av.glb_cached.join(", "))}</span>`);
  if (av.preview) out.push(`<span class="chip">preview</span>`);
  if (a.kit) out.push(`<span class="chip">kit ${esc(a.kit.family)}${a.kit.role ? "/" + esc(a.kit.role) : ""}</span>`);
  return out.join("");
}

function resultRow(a) {
  const on = selection.has(a.ref);
  const el = document.createElement("div");
  el.className = "row";
  el.innerHTML = `<input type="checkbox" ${on ? "checked" : ""} aria-label="select">
    <div><div class="id">${esc(a.id)}</div>
      <div class="muted small">${esc(a.name || "")} · ${esc(a.pack)} · ${esc(fmtSize(a.size_m))}</div>
      <div class="chips">${chips(a)}</div></div>
    <span class="muted small">${a.score != null ? a.score : ""}</span>`;
  const cb = el.querySelector("input");
  cb.addEventListener("click", (e) => { e.stopPropagation(); toggleSel(a.ref, cb.checked); });
  el.addEventListener("click", () => { document.querySelectorAll(".row.sel").forEach((r) => r.classList.remove("sel")); el.classList.add("sel"); showDetail(a.ref); });
  return el;
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const p = new URLSearchParams({ q: $("#q").value, class: $("#f-class").value, pack: $("#f-pack").value,
    available: $("#f-avail").checked ? "1" : "0", all: $("#f-class").value ? "1" : "0", limit: "60" });
  const box = $("#results");
  box.innerHTML = `<p class="muted">Searching…</p>`;
  try {
    const res = await api("/api/search?" + p);
    box.innerHTML = "";
    const u = res.understood || {};
    box.insertAdjacentHTML("beforeend", `<p class="muted small">Understood as: ${esc(u.concept || "—")}
      ${u.modifiers?.length ? "+ " + esc(u.modifiers.join(", ")) : ""} · ${res.results.length} results</p>`);
    res.results.forEach((a) => box.appendChild(resultRow(a)));
    if (!res.results.length) {
      const cands = (res.owned_unindexed_candidates || []).map((c) => `<li>${esc(c.name)} <span class="muted">(${esc(c.status)}, unverified)</span></li>`).join("");
      box.insertAdjacentHTML("beforeend", `<div class="item"><p>No indexed asset matches.</p>
        ${cands ? `<p class="muted">Owned packs that probably contain it:</p><ul>${cands}</ul>` : ""}</div>`);
    }
  } catch (err) { box.innerHTML = `<p class="chip bad">${esc(err.message)}</p>`; }
});

async function showDetail(ref) {
  const box = $("#detail");
  box.innerHTML = `<p class="muted">Loading…</p>`;
  const [d, prev] = await Promise.all([api("/api/asset?ref=" + encodeURIComponent(ref)),
    api("/api/preview?ref=" + encodeURIComponent(ref)).catch(() => ({}))]);
  const g = d.geometry || {};
  const rec = d.record || {};
  box.innerHTML = `
    <h2>${esc(d.id)}</h2>
    <div class="muted">${esc(d.name || "")} — ${esc(d.pack)}</div>
    ${prev.url ? `<img class="thumb" alt="" src="${esc(prev.url)}">` : ""}
    <div class="actions">
      <button id="d-view">3D preview</button>
      <button id="d-sel" class="ghost">${selection.has(d.ref) ? "Remove from collection" : "Add to collection"}</button>
      <button id="d-thumb" class="ghost">Generate thumbnail</button>
      <button id="d-sim" class="ghost">Similar</button>
    </div>
    <dl>
      <dt>Ref</dt><dd class="id">${esc(d.ref)}</dd>
      <dt>Class</dt><dd>${esc(d.class)} · ${esc(d.type)} · ${esc(d.kind)}</dd>
      <dt>Size</dt><dd>${esc(fmtSize(d.size_m))} ${d.size_source ? `<span class="muted">(${esc(d.size_source)})</span>` : ""}</dd>
      <dt>Triangles</dt><dd>${g.triangles ?? "–"}</dd>
      <dt>Vertices</dt><dd>${g.vertices ?? "–"}</dd>
      <dt>Materials</dt><dd>${esc((g.materials || []).join(", ") || "–")}</dd>
      <dt>Animations</dt><dd>${esc((g.animations || []).join(", ") || "–")}</dd>
      <dt>Source file</dt><dd class="small">${esc(d.files.source_model || "not available")}${d.files.node_in_model ? " → node " + esc(d.files.node_in_model) : ""}</dd>
      <dt>Conversions</dt><dd>${d.conversions.map((c) => `${esc(c.preset)} (${fmtBytes(c.glb?.bytes)})`).join(", ") || "none"}</dd>
      <dt>Description</dt><dd class="small">${esc(rec.description || "")}</dd>
    </dl>
    <div id="d-extra"></div>`;
  $("#d-view").onclick = () => openViewer(d);
  $("#d-sel").onclick = () => { toggleSel(d.ref, !selection.has(d.ref)); showDetail(ref); };
  $("#d-thumb").onclick = async () => { $("#d-thumb").disabled = true; await api("/api/preview?generate=1&ref=" + encodeURIComponent(ref)).catch((e) => alert(e.message)); showDetail(ref); };
  $("#d-sim").onclick = async () => {
    const s = await api("/api/similar?ref=" + encodeURIComponent(ref));
    const ex = $("#d-extra"); ex.innerHTML = `<h3>Similar</h3>`;
    const l = document.createElement("div"); l.className = "list";
    s.similar.forEach((a) => l.appendChild(resultRow(a))); ex.appendChild(l);
  };
}

// --------------------------------------------------------------- viewer
let viewer = null;
function ensureViewer() {
  if (viewer) return viewer;
  const host = $("#viewer");
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xd9dde2);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 2000);
  const controls = new OrbitControls(camera, renderer.domElement);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2); sun.position.set(5, 10, 7); scene.add(sun);
  scene.add(new THREE.GridHelper(40, 40, 0x7a8088, 0xb8bdc4));
  // 1.8 m human-scale reference
  const ref = new THREE.Mesh(new THREE.CapsuleGeometry(0.22, 1.36, 4, 12),
    new THREE.MeshStandardMaterial({ color: 0x2aa198, transparent: true, opacity: 0.55 }));
  ref.position.set(-1.2, 0.9, 0); scene.add(ref);
  const loader = new GLTFLoader();
  const draco = new DRACOLoader(); draco.setDecoderPath("https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/libs/draco/gltf/");
  loader.setDRACOLoader(draco);
  const clock = new THREE.Clock();
  viewer = { renderer, scene, camera, controls, loader, ref, current: null, mixer: null, clock };
  const resize = () => { const w = host.clientWidth, h = host.clientHeight; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe(host);
  renderer.setAnimationLoop(() => { viewer.mixer?.update(clock.getDelta()); controls.update(); renderer.render(scene, camera); });
  return viewer;
}

async function openViewer(d) {
  const dlg = $("#viewer-dlg");
  $("#viewer-title").textContent = d.id;
  $("#viewer-dims").textContent = "converting…";
  dlg.showModal();
  const v = ensureViewer();
  let conv = d.conversions.find((c) => c.preset === "threejs_web");
  if (!conv?.url) {
    const job = await api("/api/convert", { method: "POST", body: JSON.stringify({ ref: d.ref, preset: "threejs_web" }) })
      .catch((e) => ({ status: "failed", error: e.message }));
    const done = await waitJob(job, (j) => { $("#viewer-dims").textContent = `${j.status} ${Math.round((j.progress || 0) * 100)}%`; });
    if (done.status !== "done") { $("#viewer-dims").textContent = "error: " + (done.error || "conversion failed"); return; }
    conv = done.result;
  }
  v.loader.load(conv.url, (gltf) => {
    if (v.current) v.scene.remove(v.current);
    v.current = gltf.scene; v.scene.add(gltf.scene);
    v.mixer = null;
    if (gltf.animations?.length) { v.mixer = new THREE.AnimationMixer(gltf.scene); v.mixer.clipAction(gltf.animations[0]).play(); }
    const box = new THREE.Box3().setFromObject(gltf.scene, true);
    const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
    $("#viewer-dims").textContent = `${size.x.toFixed(2)} × ${size.y.toFixed(2)} × ${size.z.toFixed(2)} m (measured in Three.js)`;
    v.ref.position.set(box.min.x - 0.6, 0.9, center.z);
    const r = Math.max(size.length(), 1.8) * 1.25;
    v.camera.position.set(center.x + r, center.y + r * 0.6, center.z + r);
    v.controls.target.copy(center); v.controls.update();
  }, undefined, (err) => { $("#viewer-dims").textContent = "load error: " + err.message; });
}
$("#viewer-close").onclick = () => $("#viewer-dlg").close();

async function waitJob(job, onTick) {
  let j = job;
  while (j && (j.status === "queued" || j.status === "running")) {
    onTick?.(j);
    await new Promise((r) => setTimeout(r, 1000));
    j = await api("/api/job/" + j.job_id);
  }
  return j;
}

// ----------------------------------------------------------------- scene
$("#scene-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const out = $("#scene-out");
  out.innerHTML = `<p class="muted">Analysing…</p>`;
  const res = await api("/api/scene?q=" + encodeURIComponent($("#scene-q").value));
  out.innerHTML = "";
  for (const it of res.items) {
    const div = document.createElement("div");
    div.className = "item";
    const badge = it.status === "found" ? "ok" : it.status === "partial" ? "warn" : "bad";
    div.innerHTML = `<h3>${esc(it.request)} <span class="chip ${badge}">${esc(it.status)}</span> <span class="muted small">×${it.qty} · ${esc(it.understood.concept || "?")}</span></h3>`;
    const l = document.createElement("div"); l.className = "list";
    it.selected.forEach((a) => l.appendChild(resultRow(a)));
    div.appendChild(l);
    if (it.alternatives.length) {
      div.insertAdjacentHTML("beforeend", `<p class="muted small">Alternatives</p>`);
      const l2 = document.createElement("div"); l2.className = "list";
      it.alternatives.forEach((a) => l2.appendChild(resultRow(a))); div.appendChild(l2);
    }
    if (it.owned_unindexed_candidates?.length) {
      div.insertAdjacentHTML("beforeend", `<p class="muted small">Owned packs not indexed yet that may contain it (unverified): ${it.owned_unindexed_candidates.map((c) => esc(c.name)).join(", ")}</p>`);
    }
    if (it.selected.length) {
      const b = document.createElement("button"); b.className = "ghost"; b.textContent = "Select the suggestions";
      b.onclick = () => it.selected.forEach((a) => toggleSel(a.ref, true));
      div.appendChild(b);
    }
    out.appendChild(div);
  }
});

// ----------------------------------------------------------------- packs
async function renderPacks() {
  const res = await api("/api/packs");
  const f = $("#pack-status").value;
  $("#packs").innerHTML = res.packs.filter((p) => !f || p.status === f).map((p) => `
    <div class="card"><h3>${esc(p.name)}</h3>
      <div class="status-${esc(p.status)}">${esc(p.status)}</div>
      <div class="muted small">${esc(p.pack_id)}${p.pack_id_confirmed ? "" : " (guessed id)"} · ${p.indexed_assets ?? 0} assets</div>
      <div class="muted small">${p.archives.length} archives · ${p.trees.map((t) => esc(t.kind) + (t.read_only ? " (read-only)" : "")).join(", ") || "no extracted tree"}</div>
    </div>`).join("");
}
$("#pack-status").addEventListener("change", renderPacks);

// ------------------------------------------------------------ collection
function loadSelection() { try { return new Map(JSON.parse(localStorage.getItem("synty.sel") || "[]")); } catch { return new Map(); } }
function saveSelection() { try { localStorage.setItem("synty.sel", JSON.stringify([...selection])); } catch {} $("#sel-count").textContent = selection.size; }
function toggleSel(ref, on) { if (on) selection.set(ref, selection.get(ref) || 1); else selection.delete(ref); saveSelection(); }
function renderSelection() {
  const l = $("#col-list");
  l.innerHTML = selection.size ? "" : `<p class="muted">No assets selected: use the checkboxes in the results.</p>`;
  for (const [ref, qty] of selection) {
    const el = document.createElement("div"); el.className = "row";
    el.innerHTML = `<input type="number" min="1" value="${qty}" style="width:64px" aria-label="quantity"><div class="id">${esc(ref)}</div><button class="ghost">Remove</button>`;
    el.querySelector("input").onchange = (e) => { selection.set(ref, Math.max(1, +e.target.value || 1)); saveSelection(); };
    el.querySelector("button").onclick = () => { selection.delete(ref); saveSelection(); renderSelection(); };
    l.appendChild(el);
  }
}
$("#col-clear").onclick = () => { selection.clear(); saveSelection(); renderSelection(); };
$("#col-go").onclick = async () => {
  const out = $("#col-out");
  const body = { name: $("#col-name").value, preset: $("#col-preset").value, engine: $("#col-engine").value,
    refs: [...selection].map(([ref, qty]) => ({ ref, qty })) };
  out.textContent = "Preparing…";
  try {
    const job = await api("/api/collection", { method: "POST", body: JSON.stringify(body) });
    const done = await waitJob(job, (j) => { out.textContent = `${j.status} ${Math.round((j.progress || 0) * 100)}% ${j.message || ""}`; });
    if (done.status !== "done") { out.textContent = "Error: " + done.error; return; }
    const r = done.result;
    out.innerHTML = `Collection ready: ${r.assets} assets${r.missing.length ? `, ${r.missing.length} missing` : ""}.\n` +
      (r.manifest_url ? `<a href="${esc(r.manifest_url)}" target="_blank">manifest.json</a>  ` : "") +
      (r.zip_url ? `<a href="${esc(r.zip_url)}">download zip</a>` : "") + `\n\n${esc(JSON.stringify(r.missing, null, 2))}`;
  } catch (err) { out.textContent = "Error: " + err.message; }
};

// ------------------------------------------------------------------ jobs
let jobsTimer = null;
async function renderJobs() {
  clearTimeout(jobsTimer);
  const res = await api("/api/jobs");
  $("#jobs").innerHTML = res.jobs.map((j) => `
    <div class="item"><strong>${esc(j.kind)}</strong> <span class="chip ${j.status === "done" ? "ok" : j.status === "failed" ? "bad" : "warn"}">${esc(j.status)}</span>
      <span class="muted small">${esc(j.job_id)} · ${new Date(j.created_at * 1000).toLocaleString()}</span>
      <div class="muted small">${esc(JSON.stringify(j.params))}</div>
      ${j.error ? `<div class="chip bad">${esc(j.error)}</div>` : ""}
      <div class="bar"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div></div>`).join("") || `<p class="muted">No jobs.</p>`;
  if ($("#tab-jobs").classList.contains("active")) jobsTimer = setTimeout(renderJobs, 3000);
}

// ------------------------------------------------------------------ boot
async function boot() {
  try { await renderStatus(); } catch { return; }
  $("#app").hidden = false;
  saveSelection();
  const hash = new URLSearchParams(location.hash.slice(1));
  if (hash.get("asset")) { openTab("search"); showDetail(hash.get("asset")); return; }
  let tab = "search"; try { tab = localStorage.getItem("synty.tab") || "search"; } catch {}
  openTab(tab);
}
boot();

// VARTA — карта (стиль за рецептом NEPTUN) + список цілей + «Моє місце» + чат з агентом.
"use strict";

const $ = (s) => document.querySelector(s);
const store = {
  get(k, d) { try { const v = localStorage.getItem("varta:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("varta:" + k, JSON.stringify(v)); } catch { /* приватний режим */ } },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- геометрія (та сама логіка, що в SDK NEPTUN і varta/geo.py) ----------
const FLY_HORIZON_MIN = { uav: 12, recon: 12, fpv: 10, missile: 5, kab: 4, ballistic: 1.5, mig31k: 6, unknown: 6 };
const MAX_GHOST_KM = { uav: 18, recon: 12, fpv: 10, missile: 30, kab: 10, ballistic: 20, mig31k: 24, unknown: 10 };
const LIVE_S = 420;
const RAD = Math.PI / 180;
const COMPASS = ["північ", "північний схід", "схід", "південний схід", "південь", "південний захід", "захід", "північний захід"];
const UA_BOUNDS = [[21.5, 44], [41, 52.8]];

function dest(lat, lon, brg, km) {
  if (km <= 0) return [lat, lon];
  const d = km / 6371, b = brg * RAD, p1 = lat * RAD, l1 = lon * RAD;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [p2 / RAD, ((l2 / RAD) + 540) % 360 - 180];
}
function distKm(a1, o1, a2, o2) {
  const dp = (a2 - a1) * RAD, dl = (o2 - o1) * RAD;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(a1 * RAD) * Math.cos(a2 * RAD) * Math.sin(dl / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)));
}
function bearing(a1, o1, a2, o2) {
  const p1 = a1 * RAD, p2 = a2 * RAD, dl = (o2 - o1) * RAD;
  const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (Math.atan2(y, x) / RAD + 360) % 360;
}
const compass = (b) => COMPASS[Math.floor(((b % 360) + 22.5) / 45) % 8];

function predict(t, now) {
  if (t.areaOnly || t.status !== "active" || t.speed == null || t.heading == null) return { lat: t.lat, lon: t.lon, flying: false };
  const ageMin = (now - t.confirmedAt) / 60000;
  if (ageMin <= 0) return { lat: t.lat, lon: t.lon, flying: false };
  const hz = FLY_HORIZON_MIN[t.type] ?? 6;
  const km = Math.min(MAX_GHOST_KM[t.type] ?? 8, t.speed * Math.min(ageMin, hz) / 60);
  const [lat, lon] = dest(t.lat, t.lon, t.heading, km);
  return { lat, lon, flying: ageMin <= hz && km > 0.05 };
}

function circle(lat, lon, km, n = 72) {
  const ring = [];
  for (let i = 0; i <= n; i++) { const [a, o] = dest(lat, lon, (i / n) * 360, km); ring.push([o, a]); }
  return ring;
}

function inRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function inGeom(lon, lat, g) {
  const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
  return polys.some((p) => inRing(lon, lat, p[0]) && !p.slice(1).some((h) => inRing(lon, lat, h)));
}

function plural(n, f) {
  const a = n % 10, b = n % 100;
  return a === 1 && b !== 11 ? f[0] : a >= 2 && a <= 4 && (b < 12 || b > 14) ? f[1] : f[2];
}
function ago(ms, now) {
  const s = Math.round((now - ms) / 1000);
  if (s < 30) return "щойно";
  if (s < 90) return "хвилину тому";
  if (s < 3600) { const m = Math.round(s / 60); return `${m} ${plural(m, ["хвилину", "хвилини", "хвилин"])} тому`; }
  const h = Math.round(s / 3600); return `${h} ${plural(h, ["годину", "години", "годин"])} тому`;
}

// ---------- стан ----------
const S = {
  tracks: [], alerts: { raions: [], oblasts: [] }, attack: null, sources: {}, skew: 0,
  filter: null, selected: null, ctx: null, answerPlace: null, focus: new Set(),
  me: store.get("me", null), radius: store.get("radius", 20), basemap: store.get("basemap", "sat"),
  inside: new Set(), raions: null, oblasts: null, myRegion: null,
};
const now = () => Date.now() + S.skew;

// ---------- карта ----------
const FONT_R = ["Noto Sans Regular"], FONT_B = ["Noto Sans Bold"];
const NAME = ["coalesce", ["get", "name:uk"], ["get", "name"]];
const Z = (a, b, c) => ["interpolate", ["linear"], ["zoom"], 4, a, 7, b, 10, c];
const empty = () => ({ type: "FeatureCollection", features: [] });
const ICON_K = ["*", ["case", ["==", ["get", "sel"], 1], 1.3, 1], ["case", ["==", ["get", "dest"], 1], 0.82, 1]];

function buildStyle() {
  const sat = S.basemap === "sat" ? "visible" : "none", ink = S.basemap === "sat" ? "none" : "visible";
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      esri: { type: "raster", tileSize: 256, maxzoom: 19,
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"] },
      dem: { type: "raster-dem", encoding: "terrarium", tileSize: 256, maxzoom: 13,
        tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"] },
      ofm: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
      dim: { type: "geojson", data: empty() }, oblasts: { type: "geojson", data: empty() },
      raions: { type: "geojson", data: empty() }, ukraine: { type: "geojson", data: empty() },
      trails: { type: "geojson", data: empty(), lineMetrics: true }, forecast: { type: "geojson", data: empty() },
      threats: { type: "geojson", data: empty() }, me: { type: "geojson", data: empty() },
      answer: { type: "geojson", data: empty() },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#0e1a28" } },
      { id: "satellite", type: "raster", source: "esri", layout: { visibility: sat },
        paint: { "raster-brightness-max": 0.46, "raster-saturation": -0.19 } },
      { id: "terrain", type: "hillshade", source: "dem", minzoom: 6, paint: {
        "hillshade-shadow-color": "rgba(2,6,14,0.55)", "hillshade-highlight-color": "rgba(125,170,215,0.16)",
        "hillshade-accent-color": "rgba(10,24,44,0.25)" } },
      { id: "water-plate", type: "fill", source: "ofm", "source-layer": "water", layout: { visibility: sat },
        paint: { "fill-color": "#0a2138", "fill-opacity": 0.43 } },
      { id: "ink-wood", type: "fill", source: "ofm", "source-layer": "landcover", filter: ["==", ["get", "class"], "wood"],
        layout: { visibility: ink }, paint: { "fill-color": "#153a2f", "fill-opacity": 0.62 } },
      { id: "ink-landuse", type: "fill", source: "ofm", "source-layer": "landuse", layout: { visibility: ink },
        filter: ["in", ["get", "class"], ["literal", ["residential", "suburb", "neighbourhood", "commercial", "industrial"]]],
        paint: { "fill-color": "#26364a", "fill-opacity": 0.6 } },
      { id: "ink-water", type: "fill", source: "ofm", "source-layer": "water", layout: { visibility: ink },
        paint: { "fill-color": "#1b3a5e" } },
      { id: "ink-roads", type: "line", source: "ofm", "source-layer": "transportation", minzoom: 5, layout: { visibility: ink },
        filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]],
        paint: { "line-color": "#3d4b63", "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 12, 2.4] } },
      { id: "boundary-country", type: "line", source: "ofm", "source-layer": "boundary",
        filter: ["all", ["<=", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1]],
        paint: { "line-color": "#b6c2d4", "line-opacity": 0.22, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 10, 1.8] } },
      { id: "roads-major", type: "line", source: "ofm", "source-layer": "transportation", minzoom: 6, layout: { visibility: sat },
        filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]],
        paint: { "line-color": "#93b0cc", "line-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.16, 10, 0.44, 14, 0.68],
          "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.4, 10, 1, 14, 2] } },
      { id: "place-city", type: "symbol", source: "ofm", "source-layer": "place", filter: ["==", ["get", "class"], "city"],
        layout: { "text-field": NAME, "text-font": FONT_B, "text-size": ["interpolate", ["linear"], ["zoom"], 4, 11, 8, 16, 12, 21],
          "text-max-width": 8, "text-padding": 4 },
        paint: { "text-color": "#ffffff", "text-halo-color": "rgba(4,9,18,0.9)", "text-halo-width": 1.6, "text-halo-blur": 0.5 } },
      { id: "place-town", type: "symbol", source: "ofm", "source-layer": "place", minzoom: 7, filter: ["==", ["get", "class"], "town"],
        layout: { "text-field": NAME, "text-font": FONT_R, "text-size": ["interpolate", ["linear"], ["zoom"], 7, 11, 12, 15] },
        paint: { "text-color": "#e7eef7", "text-halo-color": "rgba(4,9,18,0.9)", "text-halo-width": 1.4,
          "text-opacity": ["interpolate", ["linear"], ["zoom"], 7, 0, 7.6, 1] } },
      { id: "place-village", type: "symbol", source: "ofm", "source-layer": "place", minzoom: 9.5,
        filter: ["in", ["get", "class"], ["literal", ["village", "hamlet", "suburb"]]],
        layout: { "text-field": NAME, "text-font": FONT_R, "text-size": 11 },
        paint: { "text-color": "#d4dce8", "text-halo-color": "rgba(4,9,18,0.92)", "text-halo-width": 1.35 } },
      { id: "world-dim", type: "fill", source: "dim", paint: { "fill-color": "#050505", "fill-opacity": 0.67 } },
      { id: "oblast-alert", type: "fill", source: "oblasts", filter: ["in", ["get", "key"], ["literal", []]],
        paint: { "fill-color": "#ef4444", "fill-opacity": 0.28 } },
      { id: "raion-alert", type: "fill", source: "raions", filter: ["in", ["get", "key"], ["literal", []]],
        paint: { "fill-color": "#ef4444", "fill-opacity": 0.28 } },
      { id: "raion-line", type: "line", source: "raions", paint: { "line-color": "#ffffff",
        "line-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.133, 9, 0.247, 13, 0.38],
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 9, 0.8, 13, 1.2] } },
      { id: "ukraine-casing", type: "line", source: "ukraine", paint: { "line-color": "#000", "line-opacity": 0.18,
        "line-width": ["interpolate", ["linear"], ["zoom"], 4, 6.2, 8, 9.3, 11, 12.4] } },
      { id: "ukraine-line", type: "line", source: "ukraine", paint: { "line-color": "#fff", "line-opacity": 0.18,
        "line-width": ["interpolate", ["linear"], ["zoom"], 4, 2.79, 8, 4.34, 11, 6.2] } },
      { id: "answer-ring", type: "line", source: "answer", paint: { "line-color": "#ffd9a8", "line-width": 1.6, "line-dasharray": [2, 2] } },
      { id: "me-ring", type: "line", source: "me", filter: ["==", ["get", "role"], "ring"],
        paint: { "line-color": "#5aa6ff", "line-dasharray": [3, 3], "line-opacity": 0.7, "line-width": 1.4 } },
      { id: "me-dot", type: "circle", source: "me", filter: ["==", ["get", "role"], "center"],
        paint: { "circle-color": "#5aa6ff", "circle-radius": 7, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } },
      { id: "me-label", type: "symbol", source: "me", filter: ["==", ["get", "role"], "center"],
        layout: { "text-field": ["get", "name"], "text-font": FONT_R, "text-size": 11, "text-offset": [0, 1.3], "text-anchor": "top" },
        paint: { "text-color": "#bcd4ff", "text-halo-color": "rgba(4,9,18,0.9)", "text-halo-width": 1.2 } },
      { id: "trail-casing", type: "line", source: "trails", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-width": ["case", ["==", ["get", "sel"], 1], 3.5, 2.5],
          "line-gradient": ["interpolate", ["linear"], ["line-progress"], 0, "rgba(11,18,32,0)", 1, "rgba(11,18,32,0.5)"] } },
      { id: "trail", type: "line", source: "trails", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-width": ["case", ["==", ["get", "sel"], 1], 2.5, 1.5],
          "line-gradient": ["interpolate", ["linear"], ["line-progress"], 0, "rgba(255,255,255,0)", 1, "rgba(255,255,255,0.7)"] } },
      { id: "forecast", type: "line", source: "forecast",
        paint: { "line-color": "#ffffff", "line-opacity": 0.75, "line-width": 1.3, "line-dasharray": [2, 2.5] } },
      { id: "threat-icon", type: "symbol", source: "threats",
        layout: { "icon-image": ["concat", "ti-", ["get", "type"]], "icon-rotate": ["get", "rot"], "icon-rotation-alignment": "map",
          "icon-allow-overlap": true, "icon-ignore-placement": true, "symbol-sort-key": ["get", "sev"],
          "icon-size": ["interpolate", ["linear"], ["zoom"], 4, ["*", 0.72, ICON_K], 7, ["*", 0.9, ICON_K], 10, ["*", 1.12, ICON_K]] },
        paint: { "icon-opacity": ["case", ["==", ["get", "stale"], 1], 0.5, ["==", ["get", "mapa"], 1], 0.8, 1] } },
      { id: "threat-course", type: "symbol", source: "threats", filter: ["==", ["get", "course"], 1],
        layout: { "icon-image": "ti-course", "icon-rotate": ["get", "rot"], "icon-rotation-alignment": "map",
          "icon-offset": [0, -24], "icon-allow-overlap": true, "icon-ignore-placement": true,
          "icon-size": ["interpolate", ["linear"], ["zoom"], 4, 0.72, 7, 0.9, 10, 1.12] },
        paint: { "icon-opacity": ["case", ["==", ["get", "stale"], 1], 0.5, 1] } },
      { id: "threat-count", type: "symbol", source: "threats", filter: [">=", ["get", "cnt"], 2],
        layout: { "text-field": ["concat", "×", ["to-string", ["get", "cnt"]]], "text-font": FONT_B, "text-size": 13,
          "text-anchor": "left", "text-offset": [1.3, -0.6], "text-allow-overlap": true, "text-ignore-placement": true },
        paint: { "text-color": "#fff", "text-halo-color": "#0b1220", "text-halo-width": 1.6 } },
      { id: "threat-area-label", type: "symbol", source: "threats", filter: ["==", ["get", "area"], 1],
        layout: { "text-field": ["get", "label"], "text-font": FONT_R, "text-size": 12, "text-anchor": "top",
          "text-offset": [0, 1.6], "text-max-width": 13, "text-allow-overlap": true },
        paint: { "text-color": "#ffd9a8", "text-halo-color": "rgba(4,9,18,0.92)", "text-halo-width": 1.5 } },
    ],
  };
}

const map = new maplibregl.Map({
  container: "map", style: buildStyle(), center: [31.5, 48.9], zoom: 5.35, minZoom: 4, maxZoom: 12,
  maxBounds: [[16, 42], [47, 54.5]], attributionControl: false, dragRotate: false, pitchWithRotate: false,
});
map.touchZoomRotate.disableRotation();

async function loadGeo() {
  const [ukr, obl, rai] = await Promise.all(["ukraine", "oblasts", "raions"].map((n) => fetch(`/api/geo/${n}`).then((r) => r.json())));
  S.oblasts = obl; S.raions = rai;
  const g = ukr.geometry, polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  map.getSource("ukraine").setData(ukr);
  map.getSource("oblasts").setData(obl);
  map.getSource("raions").setData(rai);
  map.getSource("dim").setData({ type: "Feature", properties: {}, geometry: { type: "Polygon",
    coordinates: [[[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]], ...polys.map((p) => p[0])] } });
  locateMyRegion();
  renderAlerts();
}

// ---------- дані ----------
async function poll() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    const d = await r.json();
    S.skew = d.serverTime - Date.now();
    S.tracks = d.tracks; S.alerts = d.alerts || S.alerts; S.attack = d.attack; S.sources = d.sources || {};
    renderAll();
  } catch {
    setStatus("down", "немає звʼязку із сервером");
  }
}

function setStatus(cls, text) { const el = $("#status"); el.className = "status " + cls; el.lastElementChild.textContent = text; }

function renderAll() {
  const n = S.sources.neptun || {};
  const fresh = n.at && (Date.now() / 1000 - n.at) < 90;
  setStatus(n.ok && fresh ? "live" : n.at ? "" : "down", n.ok && fresh ? "NEPTUN наживо" : n.at ? "перепідключення до NEPTUN…" : "NEPTUN недоступний");
  renderChips(); renderList(); renderAlerts(); renderMyPlace(); renderCard(); tick();
}

const visible = () => S.tracks.filter((t) => !S.filter || t.type === S.filter);
const units = (t) => Math.max(1, t.count || 0);
const isStale = (t) => (now() - t.updatedAt) / 1000 > LIVE_S;

// оновлення позицій (dead-reckoning) щосекунди
function tick() {
  const t0 = now(), feats = [], trails = [];
  for (const t of visible()) {
    const p = predict(t, t0);
    t._p = p;
    const sel = S.selected === t.id || S.focus.has(t.id) ? 1 : 0;
    feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: {
      id: t.id, type: t.type, rot: t.heading ?? 0, course: t.heading != null && !t.areaOnly ? 1 : 0, sel,
      dest: t.destination ? 1 : 0, stale: isStale(t) ? 1 : 0, mapa: t.sources.length === 1 && t.sources[0] === "mapa" ? 1 : 0,
      cnt: t.count || 0, area: t.areaOnly ? 1 : 0, sev: { ballistic: 9, missile: 8, kab: 7, mig31k: 6 }[t.type] || 3,
      label: t.areaOnly ? `${t.title || TYPE_TITLE[t.type]} → ${t.region}` : "" } });
    if (t.trail && t.trail.length > 1 && !t.areaOnly) {
      trails.push({ type: "Feature", properties: { sel }, geometry: { type: "LineString", coordinates: [...t.trail, [p.lon, p.lat]] } });
    }
  }
  map.getSource("threats")?.setData({ type: "FeatureCollection", features: feats });
  map.getSource("trails")?.setData({ type: "FeatureCollection", features: trails });
  const sel = S.tracks.find((t) => t.id === S.selected);
  let fc = empty();
  if (sel && sel.heading != null && !sel.areaOnly && sel._p) {
    const spd = sel.speed || { uav: 160, missile: 800, ballistic: 3000, kab: 450, fpv: 70, recon: 120 }[sel.type] || 150;
    const [a, o] = dest(sel._p.lat, sel._p.lon, sel.heading, Math.min(spd * 0.25, 120));
    fc = { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString",
      coordinates: [[sel._p.lon, sel._p.lat], [o, a]] } }] };
  }
  map.getSource("forecast")?.setData(fc);
  checkMyRadius();
}

// ---------- панель: чипи, список ----------
function renderChips() {
  const by = {};
  for (const t of S.tracks) by[t.type] = (by[t.type] || 0) + units(t);
  $("#total").textContent = Object.values(by).reduce((a, b) => a + b, 0);
  const order = ["uav", "fpv", "missile", "ballistic", "kab", "mig31k", "recon", "unknown"];
  $("#chips").innerHTML = order.filter((k) => by[k]).map((k) =>
    `<button class="chip" data-type="${k}" aria-pressed="${S.filter === k}">${tileHtml(k)}${TYPE_META[k].label} ${by[k]}</button>`).join("");
}
$("#chips").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  S.filter = S.filter === b.dataset.type ? null : b.dataset.type;
  renderAll();
});

function rowSub(t) {
  if (t.areaOnly) return `лише область`;
  const where = t.locality ? (t.destination ? `у напрямку ${t.locality}` : t.locality) : (t.region || "");
  const course = t.heading != null ? ` → на ${compass(t.heading)}` : "";
  return where + course;
}

function renderList() {
  const list = visible();
  if (!list.length) {
    $("#list").innerHTML = `<div class="empty">${S.tracks.length ? "Немає цілей цього типу." : "Зараз на карті немає цілей."}</div>`;
    return;
  }
  const groups = {};
  for (const t of list) (groups[t.region || "Інше"] ||= []).push(t);
  const keys = Object.keys(groups).sort((a, b) => (a === "Інше") - (b === "Інше") || groups[b].length - groups[a].length);
  const t0 = now();
  $("#list").innerHTML = keys.map((k) => {
    const rows = groups[k].sort((a, b) => b.updatedAt - a.updatedAt).map((t) =>
      `<button class="row${S.selected === t.id ? " sel" : ""}${isStale(t) ? " stale" : ""}" data-id="${esc(t.id)}">
        ${tileHtml(t.type)}<span class="txt"><div class="t1">${esc(t.title || TYPE_TITLE[t.type])}${t.count > 1 ? ` ×${t.count}` : ""}</div>
        <div class="t2">${esc(rowSub(t))}</div></span><span class="age">${ago(t.updatedAt, t0)}</span></button>`).join("");
    return `<div class="group-h"><span>${esc(k)}</span><span>${groups[k].reduce((a, t) => a + units(t), 0)}</span></div>${rows}`;
  }).join("");
}
$("#list").addEventListener("click", (e) => { const r = e.target.closest(".row"); if (r) select(r.dataset.id, true); });

function renderAlerts() {
  const r = S.alerts.raions || [], o = S.alerts.oblasts || [];
  const red = (a) => a.level !== "yellow";
  if (map.getLayer("raion-alert")) {
    const keys = (arr) => ["literal", arr.map((a) => a.key)];
    const color = (arr) => {
      const y = arr.filter((a) => !red(a)).map((a) => a.key);
      return y.length ? ["case", ["in", ["get", "key"], ["literal", y]], "#f59e0b", "#ef4444"] : "#ef4444";
    };
    map.setFilter("raion-alert", ["in", ["get", "key"], keys(r)]);
    map.setPaintProperty("raion-alert", "fill-color", color(r));
    map.setFilter("oblast-alert", ["in", ["get", "key"], keys(o)]);
    map.setPaintProperty("oblast-alert", "fill-color", color(o));
  }
  const obl = new Set([...r, ...o].map((a) => a.oblast || a.name).filter(Boolean));
  $("#alerts-summary").innerHTML = obl.size
    ? `Тривога: <b>${obl.size} ${plural(obl.size, ["область", "області", "областей"])}</b> (${[...obl].map(esc).join(", ")})`
    : "Офіційних тривог зараз немає.";
}

// ---------- вибір цілі, картка ----------
function select(id, fly) {
  S.selected = id;
  const t = S.tracks.find((x) => x.id === id);
  if (t && fly) {
    const p = t._p || t;
    map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), t.areaOnly ? 6.5 : 8.2), duration: 900 });
  }
  renderList(); renderCard(); tick();
}
map.on("click", "threat-icon", (e) => { const f = e.features[0]; if (f) select(f.properties.id, false); });
map.on("mouseenter", "threat-icon", () => { map.getCanvas().style.cursor = "pointer"; });
map.on("mouseleave", "threat-icon", () => { map.getCanvas().style.cursor = ""; });

function renderCard() {
  const el = $("#card"), t = S.tracks.find((x) => x.id === S.selected);
  if (!t) { el.hidden = true; return; }
  const t0 = now(), p = t._p || predict(t, t0);
  const src = t.sources.map((s) => (s === "neptun" ? `<a href="https://neptun.in.ua/" target="_blank" rel="noopener">NEPTUN</a>`
    : `<a href="https://mapa.ua/" target="_blank" rel="noopener">MAPA.UA</a>`)).join(" + ");
  let me = "";
  if (S.me && !t.areaOnly) {
    const d = distKm(S.me.lat, S.me.lon, p.lat, p.lon), b = bearing(S.me.lat, S.me.lon, p.lat, p.lon);
    let mv = "";
    if (t.heading != null) {
      const to = (b + 180) % 360, off = Math.abs(((to - t.heading + 540) % 360) - 180);
      mv = off < 90 ? ", курс — у ваш бік" : ", курс — від вас";
    }
    me = `<h3>До «${esc(S.me.name)}»</h3><p><b>${Math.round(d)} км</b>, на ${compass(b)} від вас${mv}</p>`;
  }
  const conf = { high: "Висока", medium: "Середня", low: "Низька" }[t.confidence] || t.confidence;
  el.innerHTML = `
    <div class="card-h">${tileHtml(t.type)}<div><h2>${esc(t.title || TYPE_TITLE[t.type])}${t.count > 1 ? ` ×${t.count}` : ""}</h2>
      <p>${esc([t.locality, t.region].filter(Boolean).join(", "))} · оновлено ${ago(t.updatedAt, t0)}</p></div>
      <button class="icon-btn" id="card-x" aria-label="Закрити">✕</button></div>
    <h3>Звідки → куди</h3>
    <p>${esc(t.explanation || (t.target ? `прямує на ${t.target}` : rowSub(t)))}</p>
    <p>Курс: ${t.heading != null ? `на ${compass(t.heading)} (${Math.round(t.heading)}°)${t.presumptive ? ", припущений" : ""}` : "невідомий"}</p>
    <p>Швидкість: ${t.speed ? `${Math.round(t.speed)} км/год` : "невідома"}</p>
    <p>${t.areaOnly ? "Точки немає — джерела назвали лише область" : t.quality === "confirmed" ? "Розташування підтверджене" : `Розташування приблизне (±${Math.round(t.uncertaintyKm)} км)`}</p>
    ${t.advisory ? "<p>Це спостереження, а не сигнал ховатися.</p>" : ""}
    ${me}
    <span class="badge ${esc(t.confidence)}">Достовірність: ${esc(conf)} · джерел: ${t.sourceCount}</span>
    <h3>Дані</h3><p>${src}</p>`;
  el.hidden = false;
  $("#card-x").onclick = () => { S.selected = null; renderCard(); renderList(); tick(); };
}

// ---------- моє місце ----------
function locateMyRegion() {
  S.myRegion = null;
  if (!S.me || !S.raions) return;
  const r = S.raions.features.find((f) => inGeom(S.me.lon, S.me.lat, f.geometry));
  const o = S.oblasts.features.find((f) => inGeom(S.me.lon, S.me.lat, f.geometry));
  S.myRegion = { raion: r?.properties.key, oblast: o?.properties.key };
}

function setMe(p) {
  S.me = p ? { lat: +p.lat, lon: +p.lon, name: p.name || "Моє місце" } : null;
  store.set("me", S.me);
  S.inside.clear();
  locateMyRegion(); drawMe(); renderMyPlace();
  if (S.me && "Notification" in window && Notification.permission === "default") Notification.requestPermission();
}

function drawMe() {
  const f = [];
  if (S.me) {
    f.push({ type: "Feature", properties: { role: "center", name: S.me.name }, geometry: { type: "Point", coordinates: [S.me.lon, S.me.lat] } });
    f.push({ type: "Feature", properties: { role: "ring" }, geometry: { type: "LineString", coordinates: circle(S.me.lat, S.me.lon, S.radius) } });
  }
  map.getSource("me")?.setData({ type: "FeatureCollection", features: f });
}

function myAlert() {
  if (!S.myRegion) return null;
  return (S.alerts.raions || []).find((a) => a.key === S.myRegion.raion) || (S.alerts.oblasts || []).find((a) => a.key === S.myRegion.oblast) || null;
}

function renderMyPlace() {
  const el = $("#myplace");
  if (!S.me) {
    el.innerHTML = `<div class="line">Додайте <b>моє місце</b>: правий клік (довге натискання) на карті, пошук або кнопка ⌖ — і агент рахуватиме відстань і час підльоту саме для вас.</div>`;
    return;
  }
  const t0 = now(), al = myAlert();
  const pts = S.tracks.filter((t) => !t.areaOnly).map((t) => { const p = t._p || predict(t, t0); return { t, d: distKm(S.me.lat, S.me.lon, p.lat, p.lon), b: bearing(S.me.lat, S.me.lon, p.lat, p.lon) }; }).sort((a, b) => a.d - b.d);
  const inR = pts.filter((x) => x.d <= S.radius).length, near = pts[0];
  el.innerHTML = `<div class="t"><span>📍 ${esc(S.me.name)}</span><button id="me-clear" aria-label="Прибрати моє місце">прибрати</button></div>
    <div class="line ${al ? "danger" : ""}">Тривога: <b>${al ? `так — ${esc(al.name)}${al.level === "yellow" ? " (жовта)" : ""}` : "немає"}</b></div>
    <div class="line ${inR ? "danger" : ""}">У радіусі ${S.radius} км: <b>${inR ? `${inR} ${plural(inR, ["ціль", "цілі", "цілей"])}` : "чисто"}</b></div>
    <div class="line">Найближча: <b>${near ? `${esc(near.t.title || TYPE_TITLE[near.t.type])}, ${Math.round(near.d)} км на ${compass(near.b)}` : "—"}</b></div>
    <label>Радіус <input id="radius" type="range" min="5" max="60" step="5" value="${S.radius}"> <span>${S.radius} км</span></label>`;
  $("#me-clear").onclick = () => setMe(null);
  $("#radius").oninput = (e) => { S.radius = +e.target.value; store.set("radius", S.radius); e.target.nextElementSibling.textContent = S.radius + " км"; drawMe(); };
  $("#radius").onchange = () => renderMyPlace();
}

// сповіщення: ціль увійшла в мій радіус
function checkMyRadius() {
  if (!S.me) return;
  const t0 = now();
  for (const t of S.tracks) {
    if (t.areaOnly || isStale(t)) continue;
    const p = t._p || predict(t, t0);
    const inside = distKm(S.me.lat, S.me.lon, p.lat, p.lon) <= S.radius;
    if (inside && !S.inside.has(t.id)) {
      S.inside.add(t.id);
      const title = `${t.title || TYPE_TITLE[t.type]} у радіусі ${S.radius} км`;
      const body = `${S.me.name}: ${t.locality || t.region || ""}. Орієнтуйтеся на офіційну тривогу.`;
      if ("Notification" in window && Notification.permission === "granted") { try { new Notification(title, { body, tag: t.id }); } catch { /* */ } }
    } else if (!inside) S.inside.delete(t.id);
  }
}

// правий клік / довге натискання → «Зробити моїм місцем»
let ctxAt = null;
map.on("contextmenu", (e) => {
  ctxAt = e.lngLat;
  const m = $("#ctx-menu"); m.style.left = e.point.x + "px"; m.style.top = e.point.y + "px"; m.hidden = false;
});
map.on("click", () => { $("#ctx-menu").hidden = true; });
$("#ctx-set").onclick = async () => {
  $("#ctx-menu").hidden = true;
  if (!ctxAt) return;
  let name = "Моє місце";
  try {
    const w = await (await fetch(`/api/where?lat=${ctxAt.lat}&lon=${ctxAt.lng}`)).json();
    if (w.name) name = `біля н. п. ${w.name}`;
  } catch { /* назва необовʼязкова */ }
  setMe({ lat: ctxAt.lat, lon: ctxAt.lng, name });
};

$("#btn-locate").onclick = () => {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition((pos) => {
    setMe({ lat: pos.coords.latitude, lon: pos.coords.longitude, name: "Я (GPS)" });
    map.flyTo({ center: [pos.coords.longitude, pos.coords.latitude], zoom: 8.5 });
  }, () => alert("Не вдалося отримати геолокацію. Додайте місце правим кліком або через пошук."), { timeout: 10000 });
};
$("#btn-fit").onclick = () => map.fitBounds(UA_BOUNDS, { padding: 40 });
$("#btn-layers").onclick = () => {
  S.basemap = S.basemap === "sat" ? "ink" : "sat"; store.set("basemap", S.basemap);
  const sat = S.basemap === "sat";
  for (const id of ["satellite", "water-plate", "roads-major"]) map.setLayoutProperty(id, "visibility", sat ? "visible" : "none");
  for (const id of ["ink-wood", "ink-landuse", "ink-water", "ink-roads"]) map.setLayoutProperty(id, "visibility", sat ? "none" : "visible");
  $("#btn-layers").classList.toggle("on", !sat);
};

// ---------- пошук ----------
let searchTimer = null, results = [];
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(async () => {
    try {
      results = q.length < 2 ? [] : await (await fetch(`/api/places?q=${encodeURIComponent(q)}`)).json();
    } catch { results = []; }   // сервер перезапускається — просто без підказок
    const ul = $("#search-results");
    ul.innerHTML = results.map((p, i) => `<li role="option" data-i="${i}" aria-selected="${i === 0}"><span>${esc(p.name)}</span><small>${esc(p.oblast)}</small></li>`).join("");
    ul.hidden = !results.length;
  }, 150);
});
$("#search").addEventListener("keydown", (e) => { if (e.key === "Enter" && results[0]) pickPlace(results[0]); if (e.key === "Escape") $("#search-results").hidden = true; });
$("#search-results").addEventListener("click", (e) => { const li = e.target.closest("li"); if (li) pickPlace(results[+li.dataset.i]); });

function pickPlace(p) {
  $("#search-results").hidden = true;
  $("#search").value = p.name;
  map.flyTo({ center: [p.lon, p.lat], zoom: 9 });
  const div = document.createElement("div");
  div.innerHTML = `<b>${esc(p.name)}</b><br><small>${esc(p.oblast)}</small><br><button class="chip" style="margin-top:6px">📍 Зробити моїм місцем</button>`;
  const pop = new maplibregl.Popup({ closeButton: true }).setLngLat([p.lon, p.lat]).setDOMContent(div).addTo(map);
  div.querySelector("button").onclick = () => { setMe({ lat: p.lat, lon: p.lon, name: p.name }); pop.remove(); };
}

// ---------- панель: згортання і вкладки ----------
$("#collapse").onclick = () => {
  const c = $("#panel").classList.toggle("collapsed");
  $("#collapse").setAttribute("aria-expanded", String(!c)); $("#collapse").textContent = c ? "›" : "‹";
};
for (const [tab, view] of [["#tab-list", "#view-list"], ["#tab-chat", "#view-chat"]]) {
  $(tab).onclick = () => {
    for (const [t2, v2] of [["#tab-list", "#view-list"], ["#tab-chat", "#view-chat"]]) {
      $(t2).setAttribute("aria-selected", String(t2 === tab)); $(v2).hidden = v2 !== view;
    }
    store.set("tab", tab);
    $("#panel").classList.toggle("chatting", tab === "#tab-chat");
    if (tab === "#tab-chat") $("#q").focus();
  };
}

// ---------- чат з агентом ----------
const SUGGEST = ["Де найближчий шахед?", "Чи летить щось на мене?", "Коли буде у мене?", "Яка обстановка?", "Чи є тривога в мене?", "Що ти вмієш?"];
$("#suggest").innerHTML = SUGGEST.map((s) => `<button type="button">${esc(s)}</button>`).join("");
$("#suggest").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) ask(b.textContent); });
$("#ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q) { $("#q").value = ""; ask(q); } });

function addMsg(cls, html) {
  const div = document.createElement("div"); div.className = "msg " + cls; div.innerHTML = html;
  $("#messages").appendChild(div); $("#messages").scrollTop = 1e9; return div;
}

async function ask(q) {
  addMsg("me", esc(q));
  const wait = addMsg("bot pending", "думаю…");
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q, place: S.me, places: S.me ? [S.me] : [], ctx: S.ctx }) });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    S.ctx = d.ctx;
    wait.className = "msg bot";
    wait.innerHTML = `${esc(d.answer)}<small>намір: ${esc(d.intent)} · ${d.confidence} · ${d.ms} мс</small>`;
    showAnswer(d);
  } catch (err) {
    wait.className = "msg bot"; wait.textContent = "Не вдалося отримати відповідь: " + err.message;
  }
}

function showAnswer(d) {
  S.focus = new Set(d.focus || []);
  const f = [];
  if (d.place && d.place.radius_km) f.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: circle(d.place.lat, d.place.lon, d.place.radius_km) } });
  map.getSource("answer").setData({ type: "FeatureCollection", features: f });
  const pts = S.tracks.filter((t) => S.focus.has(t.id)).map((t) => t._p || t);
  if (d.place) pts.push(d.place);
  if (pts.length === 1) map.flyTo({ center: [pts[0].lon, pts[0].lat], zoom: Math.max(map.getZoom(), 8) });
  else if (pts.length > 1) {
    const b = new maplibregl.LngLatBounds();
    for (const p of pts) b.extend([p.lon, p.lat]);
    map.fitBounds(b, { padding: { top: 80, bottom: 90, left: window.innerWidth > 720 ? 380 : 40, right: 60 }, maxZoom: 9.5 });
  }
  if (d.focus && d.focus.length === 1) S.selected = d.focus[0];
  tick(); renderCard(); renderList();
}

// ---------- старт ----------
map.on("load", async () => {
  await loadMapIcons(map);
  $("#btn-layers").classList.toggle("on", S.basemap !== "sat");
  drawMe();
  loadGeo().catch(() => setStatus("down", "не вдалося завантажити межі районів"));
  await poll();
  setInterval(poll, 3000);
  setInterval(tick, 1000);
  setInterval(() => { renderList(); renderMyPlace(); }, 15000);
  if (store.get("tab", "#tab-list") === "#tab-chat") $("#tab-chat").click();
  addMsg("bot", "Я відповідаю лише за живими даними NEPTUN, MAPA.UA та офіційними тривогами — нічого не вигадую. Спитайте, наприклад: «де найближчий шахед?»");
});
renderMyPlace();

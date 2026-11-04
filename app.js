/* Shared page logic for the SEACAR Event-Core overview pages.
   Set window.PAGE_MODE = "all" (index.html) or "seagrass" (seagrass.html)
   before this script loads. */
"use strict";

const H3 = window.h3 || window.H3;   // h3-js UMD global is lowercase "h3"
const FINE_ZOOM = 10;
const SEAG = (window.PAGE_MODE === "seagrass");
const COL = SEAG ? 1 : 0;            // index into TS[i][year] = [total, seagrass]

const DS = [], COARSE = {}, FINE = {}, TS = {}, APPIA = [];
const included = new Set();
const dsColor = [];
let map, chart, hexLayer, fineOn = false, redrawQueued = false;
let tsMode = "stack";
let coarseByHex = new Map(), seagCoarseByHex = new Map();let fineByHex = new Map(), seagFineByHex = new Map();
const polyCache = new Map();   // h3 index -> latlng ring (memoized)

const fmt = n => n === null || n === undefined ? "—" : n.toLocaleString("en-US");
const pal = i => `hsl(${Math.round((i * 137.508) % 360)} 62% 45%)`;

function lerpColor(a, b, t) {
  return "rgb(" + a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",") + ")";
}
function countColor(c) {
  const t = Math.min(1, Math.log(Math.max(1, c)) / Math.log(11000));
  return t < .5 ? lerpColor([254,237,222],[253,141,60], t*2)
                : lerpColor([253,141,60],[189,0,38], (t-.5)*2);
}

(function BOOT() {
  const meta = OVERVIEW_META, coarse = OVERVIEW_COARSE, fine = OVERVIEW_FINE;
  meta.datasets.forEach((d, i) => {
    DS[i] = d;
    dsColor[i] = pal(i);
    included.add(i);
  });
  COARSE.all = coarse.coarse || {};
  COARSE.seag = coarse.seagrassCoarse || {};
  FINE.all = fine.fine || {};
  FINE.seag = fine.seagrassFine || {};
  Object.assign(TS, coarse.ts || {});   // TS[i][year] = [total, seagrass]
  if (window.OVERVIEW_APPIAIDS) Object.assign(APPIA, OVERVIEW_APPIAIDS.aphiaIds);

  buildMaps();
  renderCards();
  renderAppiaTable();
  renderTable();
  renderLegendFilter();
  initMap();
  initChart();
  document.getElementById("ts-mode").addEventListener("change", e => {
    tsMode = e.target.value; chartUpdate();
  });
  window.addEventListener("resize", () => chart && chart.resize());
})();

function accum(mapRef, hexArr, dsIdx) {
  // hexArr: [h3Index, count][] -> fold into mapRef: {[hexId]: {count, parts:[[dsIdx,count]]}}
  if (!hexArr) return;
  for (const [h, c] of hexArr) {
    let e = mapRef.get(h);
    if (!e) mapRef.set(h, e = { count: 0, parts: [] });
    e.count += c;
    e.parts.push([dsIdx, c]);
  }
}
function buildMaps() {
  Object.entries(COARSE.all).forEach(([k, hexArr]) => accum(coarseByHex, hexArr, +k));
  Object.entries(COARSE.seag).forEach(([k, hexArr]) => accum(seagCoarseByHex, hexArr, +k));
  Object.entries(FINE.all).forEach(([k, hexArr]) => accum(fineByHex, hexArr, +k));
  Object.entries(FINE.seag).forEach(([k, hexArr]) => accum(seagFineByHex, hexArr, +k));
}

function renderCards() {
  let cards;
  if (SEAG) {
    const withSeag = DS.filter(d => d.seagrassEvents > 0);
    const sev = DS.reduce((s, d) => s + (d.seagrassEvents || 0), 0);
    const soc = DS.reduce((s, d) => s + (d.seagrassOccurrences || 0), 0);
    cards = [
      [withSeag.length, "Datasets with seagrass occurrences"],
      [sev, "Seagrass event records"],
      [soc, "Seagrass occurrence rows"],
      [APPIA.length, "Seagrass AphiaIDs"],
    ];
  } else {
    const ev  = DS.reduce((s, d) => s + d.events, 0);
    const oc  = DS.reduce((s, d) => s + d.occurrences, 0);
    const geo = DS.filter(d => d.minLat !== null).length;
    cards = [
      [DS.length, "Program datasets"],
      [ev, "Event records"],
      [oc, "Occurrence records"],
      [geo, "Datasets with event coordinates"],
    ];
  }
  const ys = DS.filter(d => d.events > 0).flatMap(d => [d.yearMin, d.yearMax]).filter(y => y);
  cards.push([fmt(Math.min(...ys)) + " – " + fmt(Math.max(...ys)), "Event-date span"]);
  document.getElementById("cards").innerHTML = cards
    .map(([n, l]) => `<div class="card"><div class="n">${fmt(n)}</div><div class="l">${l}</div></div>`).join("");
}

function renderAppiaTable() {
  const el = document.getElementById("appia-tbody");
  if (!el || !APPIA.length) return;
  el.innerHTML = APPIA.map(([id, name, src]) =>
    `<tr><td class="num"><a href="https://www.marinespecies.org/aphia.php?p=taxdetails&id=${id}" target="_blank" rel="noopener">${id}</a></td>
     <td>${name}</td><td>${src}</td></tr>`).join("");
}

function renderTable() {
  const head = SEAG
    ? "<th>ID</th><th>Title</th><th class=\"num\">Seagrass events</th><th class=\"num\">Seagrass occ. rows</th><th class=\"num\">Seagrass hexes</th><th>Years</th><th>Bounding box</th>"
    : "<th>ID</th><th>Title</th><th class=\"num\">Events</th><th class=\"num\">Occurrences</th><th class=\"num\">Hexes</th><th>Years</th><th>Bounding box</th>";
  document.querySelector("#ds-table thead").innerHTML = "<tr>" + head + "</tr>";
  document.querySelector("#ds-table tbody").innerHTML = DS.map((d, i) => {
    const cols = SEAG
      ? `<td class="num">${fmt(d.seagrassEvents)}</td>
         <td class="num">${d.seagrassOccurrences ? fmt(d.seagrassOccurrences) : "—"}</td>
         <td class="num">${(COARSE.seag[String(i)] || []).length ? fmt((COARSE.seag[String(i)] || []).length) : "—"}</td>`
      : `<td class="num">${fmt(d.events)}</td>
         <td class="num">${d.occurrences ? fmt(d.occurrences) : "—"}</td>
         <td class="num">${d.minLat === null
           ? '<span class="tag none">no coords</span>'
           : `<span class="tag ok">${fmt((COARSE.all[String(i)] || []).length)}</span>`}</td>`;
    return `<tr data-i="${i}" class="${SEAG && !d.seagrassEvents ? "dim" : ""}">
      <td style="white-space:nowrap"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${dsColor[i]};margin-right:5px"></span>${d.id}</td>
      <td>${d.title}</td>
      ${cols}
      <td style="white-space:nowrap">${d.yearMin ?? "?"} – ${d.yearMax ?? "?"}</td>
      <td style="font-size:12px;white-space:nowrap">${/W None/.test(d.bbox) ? "—" : d.bbox}</td>
    </tr>`;
  }).join("");
  document.querySelectorAll("#ds-table tr[data-i]").forEach(tr =>
    tr.addEventListener("click", () => {
      const i = +tr.dataset.i;
      included.clear(); included.add(i); renderLegendFilter(); hexesRedraw(); chartUpdate();
      document.querySelectorAll("tr.flash").forEach(el => el.classList.remove("flash"));
      tr.classList.add("flash");
      if (SEAG) {
        // fit the map to this dataset's seagrass hexagons
        const arr = (fineOn ? seagFineByHex : seagCoarseByHex);
        const pts = ((arr.get) ? [...arr.entries()] : [])
          .filter(([h, e]) => e.parts.some(([j]) => j === i))
          .map(([h]) => hexRing(h)[0]);
        if (pts.length) {
          const la = pts.map(p => p[0]), lo = pts.map(p => p[1]);
          map.fitBounds([[Math.min(...la), Math.min(...lo)], [Math.max(...la), Math.max(...lo)]],
            { padding: [30, 30] });
        }
      } else if (DS[i].minLat !== null) {
        const d = DS[i];
        map.fitBounds([[d.minLat, d.minLon], [d.maxLat, d.maxLon]], { padding: [30, 30] });
      }
    }));
}

function renderLegendFilter() {
  const html = DS.map((d, i) => `
    <label><input type="checkbox" data-dsi="${i}" ${included.has(i) ? "checked" : ""}>
      <i class="dot" style="background:${dsColor[i]}"></i><span class="t" title="${d.title}">${d.id} ${d.title.slice(0, 30)}</span></label>`).join("");
  const title = SEAG ? "Seagrass events / hexagon" : "Events / hexagon";
  document.getElementById("map-legend").innerHTML = `
    <b>${title}</b>
    <div class="lgrad"></div>
    <div style="display:flex;justify-content:space-between"><span>1</span><span>10&thinsp;000+</span></div>
    <div style="margin-top:6px"><b>Datasets</b></div>
    <div id="dsfilter-inner">${html}</div>
    <div style="margin-top:4px">
      <a href="#" id="all-ds" style="color:#0b3d62">show all</a> ·
      <a href="#" id="none-ds" style="color:#0b3d62">show none</a>
    </div>`;
  document.querySelectorAll("#dsfilter-inner input").forEach(cb =>
    cb.addEventListener("change", () => {
      const i = +cb.dataset.dsi;
      cb.checked ? included.add(i) : included.delete(i);
      hexesRedraw(); chartUpdate();
    }));
  document.getElementById("all-ds").onclick = e => { e.preventDefault(); fillAnd(1); };
  document.getElementById("none-ds").onclick = e => { e.preventDefault(); fillAnd(0); };
}
function fillAnd(mode) {
  included.clear();
  DS.forEach((_, i) => { if (mode) included.add(i); });
  renderLegendFilter(); hexesRedraw(); chartUpdate();
}

// ---------- map ----------
function hexRing(h) {
  let r = polyCache.get(h);
  if (!r) { r = H3.cellToBoundary(h).map(p => [p[0], p[1]]); polyCache.set(h, r); }
  return r;
}
function visibleH3(mapRef) {
  const b = map.getBounds().pad(0.25);
  const out = [];
  for (const [h, e] of mapRef) {
    const ring = hexRing(h);
    // culling by first vertex (~ half-hex offset, bounds are padded)
    const p = ring[0];
    if (p[0] > b.getSouth() && p[0] < b.getNorth() && p[1] > b.getWest() && p[1] < b.getEast())
      out.push([h, e]);
  }
  return out;
}
function styleFor(count) {
  const fill = countColor(count);
  return { color: "#7a7a7a", weight: .4, opacity: .55, fillColor: fill, fillOpacity: .78 };
}
function hexesRedraw() {
  if (!hexLayer) return;
  const useFine = map.getZoom() >= FINE_ZOOM;
  if (useFine !== fineOn) fineOn = useFine;
  const mapRef = SEAG ? (fineOn ? seagFineByHex : seagCoarseByHex)
                      : (fineOn ? fineByHex : coarseByHex);
  const seagMap  = SEAG ? null : (fineOn ? seagFineByHex : seagCoarseByHex);
  const maxCells = 6000;                       // render cap per view
  const items = visibleH3(mapRef);
  if (items.length > maxCells) {
    // keep the densest hexes when over cap
    items.sort((a, b) => b[1].count - a[1].count);
    items.length = maxCells;
  }
  hexLayer.clearLayers();
  for (const [h, e] of items) {
    const parts = e.parts.filter(([i]) => included.has(i));
    if (!parts.length) continue;
    const count = parts.reduce((s, [, c]) => s + c, 0);
    const extraParts = seagMap ? (seagMap.get(h) || {}).parts || [] : [];
    const label = SEAG ? "seagrass events" : "events";
    const lines = parts.map(([i, c]) => {
      const sev = extraParts.length ? (extraParts.find(([j]) => j === i) || [i, 0])[1] : null;
      const extra = sev === null ? "" : `, ${fmt(sev)} seagrass`;
      return `<span style="color:${dsColor[i]};font-size:15px">&#9632;</span> ${DS[i].id}: ${fmt(c)} ${label}${extra} · ${DS[i].title.slice(0, 40)}`;
    }).join("<br>");
    L.polygon(hexRing(h), styleFor(count))
      .bindPopup(`<b>${fmt(count)} ${label}</b><br><span style="font-size:11px;color:#656d76">H3 res ${fineOn ? 7 : 5} ${h}</span><hr style="margin:4px 0">${lines}`)
      .addTo(hexLayer);
  }
  const hidden = items.length >= maxCells ? ` (showing ${maxCells} densest of ${len(mapRef)} hexes this view)` : "";
  let nogTxt;
  if (SEAG) {
    const nog = DS.filter(d => d.minLat === null && d.seagrassEvents > 0).map(d => d.id);
    nogTxt = `not mappable (no event coordinates): ${nog.join(", ") || "none"}`;
  } else {
    const nog = DS.filter(d => d.minLat === null).map(d => d.id);
    nogTxt = `not mappable (no event coordinates): ${nog.join(", ") || "none"}`;
  }
  document.getElementById("map-note").innerHTML =
    `Aggregation level: <b>res ${fineOn ? 7 : 5}</b>${SEAG ? " · <b>seagrass (AphiaID-selected) view</b>" : ""}
     · rendered: ${fmt(hexLayer.getLayers().length)} hexagons${hidden} · ${nogTxt}`;
}
function len(m) { let n = 0; for (const _k of m.keys()) n++; return n; }
function queueRedraw() { if (!redrawQueued) { redrawQueued = true; requestAnimationFrame(() => { redrawQueued = false; hexesRedraw(); }); } }

function initMap() {
  map = L.map("map", { renderer: L.canvas({ padding: .3 }) }).setView([27.8, -82.8], 7);
  // allow deep-linking a view:  #z=ZOOM&lat=LAT&lon=LON[&sav=1]
  const hm = location.hash.match(/z=(\d+).*lat=([\d.\-]+).*lon=([\d.\-]+)/);
  if (hm) map.setView([+hm[2], +hm[3]], +hm[1]);
  map.on("moveend", () =>
    history.replaceState(null, "", `#z=${map.getZoom()}&lat=${map.getCenter().lat.toFixed(4)}&lon=${map.getCenter().lng.toFixed(4)}`));

  const basemaps = {
    "Light gray": L.tileLayer("https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
      attribution: "Tiles &copy; Esri — Esri, DeLorme, NAVTEQ", maxZoom: 16,
    }),
    "Ocean": L.tileLayer("https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}", {
      attribution: "Tiles &copy; Esri — GEBCO, NOAA, National Geographic, DeLorme", maxZoom: 13,
    }),
    "Imagery": L.tileLayer("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      attribution: "Tiles &copy; Esri — Maxar, Earthstar Geographics", maxZoom: 18,
    }),
    "OpenStreetMap": L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', maxZoom: 18,
    }),
  };
  basemaps["Light gray"].addTo(map);
  L.control.layers(basemaps, null, { position: "bottomleft", collapsed: true }).addTo(map);

  hexLayer = L.featureGroup().addTo(map);
  map.on("moveend zoomend", queueRedraw);
  hexesRedraw();
  setTimeout(hexesRedraw, 300); // re-render once tiles settle
}

// ---------- chart ----------
function initChart() {
  chart = echarts.init(document.getElementById("chartheight"));
  tsMode = document.getElementById("ts-mode").value;
  chartUpdate();
}
function chartUpdate() {
  if (!chart) return;
  const years = new Set();
  Object.values(TS).forEach(yrs => Object.keys(yrs).forEach(y => years.add(+y)));
  if (!years.size) return;
  const y0 = Math.min(...years), y1 = Math.max(...years);
  const allYears = [];
  for (let y = y0; y <= y1; y++) allYears.push(y);

  const seriesList = DS.map((d, i) => ({ d, i }))
    .filter(({ i }) => included.has(i) && TS[i])
    .filter(({ i }) => Object.values(TS[i]).some(rec => rec[COL] > 0));
  const col = rec => rec[COL];

  let series, legend, yLog = tsMode === "lines";
  if (tsMode === "stack") {
    series = seriesList.map(({ d, i }) => ({
      name: d.id + " — " + d.title,
      type: "bar", stack: "events", emphasis: { focus: "series" },
      itemStyle: { color: dsColor[i] },
      data: allYears.map(y => col(TS[i][y] || [0, 0])),
    }));
    legend = { type: "scroll", right: 0, top: 0, itemWidth: 13, textStyle: { fontSize: 11 },
      formatter: n => n.length > 34 ? n.slice(0, 34) + "…" : n };
  } else {
    series = seriesList.map(({ d, i }) => ({
      name: d.id,
      type: "line", symbol: "circle", symbolSize: 3, lineStyle: { width: 1.4 },
      itemStyle: { color: dsColor[i] },
      data: allYears.map(y => { const rec = TS[i][y]; return rec && col(rec) > 0 ? col(rec) : null; }),
    }));
    legend = { type: "scroll", right: 0, top: 0, itemWidth: 13, textStyle: { fontSize: 11 } };
  }
  const unit = SEAG ? "seagrass events / year" : "events / year";
  const startPct = SEAG ? 0 : Math.max(0, (1975 - y0) / (y1 - y0 + 1) * 100);
  chart.setOption({
    backgroundColor: "#fff",
    tooltip: { trigger: "axis", axisPointer: { type: tsMode === "stack" ? "shadow" : "line" } },
    legend,
    grid: { left: 70, right: legend.type === "scroll" ? 130 : 20, top: 46, bottom: 55 },
    xAxis: { type: "category", data: allYears, name: "", axisLabel: { fontSize: 10, rotate: 0, interval: Math.floor((allYears.length / 12)) } },
    yAxis: { type: yLog ? "log" : "value", name: unit, axisLabel: { fontSize: 10 } },
    dataZoom: (() => {
      return [{ type: "inside", xAxisIndex: 0, start: startPct, end: 100 },
              { type: "slider", height: 18, bottom: 8, start: startPct, end: 100 }];
    })(),
    series,
  }, true);
}

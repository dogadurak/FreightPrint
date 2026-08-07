// Slots 1-3 of the dataviz reference palette, checked against this surface with the
// skill's validator: lightness band, chroma floor, CVD separation and the normal-vision
// floor all pass on both the adjacent and all-pairs lists.
const MODE_COLOURS = { road: "#eb6834", sea: "#2a78d6", rail: "#1baf7a" };
/** Read the mode colour from the stylesheet so the map follows the active theme. */
const modeColour = (mode) =>
  getComputedStyle(document.documentElement).getPropertyValue(`--${mode}`).trim()
  || MODE_COLOURS[mode] || "#6e7783";
const MODE_LABELS = { road: "karayolu", sea: "deniz", rail: "demiryolu" };
const MODE_ORDER = ["road", "sea", "rail"];

const SET_LABELS = {
  reference: "Müşteri raporu",
  glec: "GLEC · refakatsiz",
  glec_accompanied: "GLEC · refakatli",
  glec_freight_average: "GLEC · filo ort.",
};

const $ = (id) => document.getElementById(id);
const form = $("shipment-form");
const dashboard = $("dashboard");
const emptyState = $("empty-state");
const statusLine = $("status");
const submitButton = $("submit");
const originInput = $("origin");
const destinationInput = $("destination");
const mapElement = $("map");

const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const nf3 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 });
const signed = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${nf.format(Math.abs(v))}`;

let factorSets = [];
let payload = null;          // last /api/routes response
let scenarioKey = null;      // "factor_set|scope"
let selectedIndex = 0;
let map;
let drawnLayers = [];
let endpointMarkers = {};
let picking = null;

const keyOf = (s) => `${s.factor_set}|${s.scope}`;
const currentScenario = () => payload?.scenarios.find((s) => keyOf(s) === scenarioKey) ?? null;

/**
 * Re-price an alternative's legs under a scenario.
 *
 * `alternatives` carries geometry and legs priced under the primary scenario only, so a
 * switched scenario has to redistribute. Scaling every leg by the change in the total is
 * wrong — switching the ro-ro basis moves the sea factor and leaves road and rail alone,
 * yet uniform scaling inflates all three. Within one mode every leg shares a factor, so
 * splitting that mode's scenario total by distance is exact, and the factor it implies
 * can be recovered the same way.
 */
function legsUnder(scenario, alternative, total) {
  const kmByMode = {};
  alternative.legs.forEach((leg) => {
    kmByMode[leg.mode] = (kmByMode[leg.mode] ?? 0) + leg.distance_km;
  });
  return alternative.legs.map((leg) => {
    const modeCo2 = total.co2_by_mode[leg.mode] ?? 0;
    const modeKm = kmByMode[leg.mode] || 1;
    return {
      ...leg,
      co2_kg: modeCo2 * (leg.distance_km / modeKm),
      factor_value: payload.tonnage ? modeCo2 / (modeKm * payload.tonnage) : leg.factor_value,
    };
  });
}

function parsePoint(value) {
  const [lon, lat] = value.split(",").map((part) => Number(part.trim()));
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  if (Math.abs(lon) > 180 || Math.abs(lat) > 90) return null;
  return { lon, lat };
}
const formatPoint = (lngLat) => `${lngLat.lng.toFixed(4)}, ${lngLat.lat.toFixed(4)}`;

/* ── map ─────────────────────────────────────────────────────────────── */

/** The map is a nice-to-have. Losing it must not take the dashboard down with it. */
function initMap() {
  if (typeof maplibregl === "undefined") {
    mapElement.innerHTML =
      '<p class="map-unavailable">Harita kütüphanesi yüklenemedi (çevrimdışı olabilirsiniz). '
      + "Hesaplama, göstergeler ve rapor indirme çalışmaya devam eder.</p>";
    return;
  }
  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      // Symbol layers need a glyph source; the server below serves Noto, not Open Sans.
      glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "© OpenStreetMap katkıda bulunanlar",
        },
      },
      layers: [{ id: "osm", type: "raster", source: "osm" }],
    },
    center: [18, 45],
    zoom: 3.4,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  map.on("load", () => { loadTerminals(); placeEndpointMarkers(); paintBasemap(currentTheme()); });
  map.on("click", onMapClick);
}

function endpointMarker(kind, lngLat) {
  const element = document.createElement("div");
  element.style.cssText =
    "width:15px;height:15px;border-radius:50%;box-shadow:0 0 0 2px #fff,0 1px 3px rgba(0,0,0,.4);"
    + `background:${kind === "origin" ? "#10141a" : "#b3261e"}`;
  const marker = new maplibregl.Marker({ element, draggable: true })
    .setLngLat(lngLat)
    .setPopup(new maplibregl.Popup({ offset: 14, closeButton: false })
      .setText(kind === "origin" ? "Kalkış — sürükleyin" : "Varış — sürükleyin"))
    .addTo(map);
  marker.on("dragend", () => {
    (kind === "origin" ? originInput : destinationInput).value = formatPoint(marker.getLngLat());
  });
  return marker;
}

function placeEndpointMarkers() {
  if (!map) return;
  for (const [kind, input] of [["origin", originInput], ["destination", destinationInput]]) {
    const point = parsePoint(input.value);
    if (!point) continue;
    if (endpointMarkers[kind]) endpointMarkers[kind].setLngLat([point.lon, point.lat]);
    else endpointMarkers[kind] = endpointMarker(kind, [point.lon, point.lat]);
  }
}

function setPicking(kind) {
  picking = picking === kind ? null : kind;
  $("pick-origin").setAttribute("aria-pressed", String(picking === "origin"));
  $("pick-destination").setAttribute("aria-pressed", String(picking === "destination"));
  mapElement.classList.toggle("picking", picking !== null);
}

function onMapClick(event) {
  if (!picking) return;
  (picking === "origin" ? originInput : destinationInput).value = formatPoint(event.lngLat);
  placeEndpointMarkers();
  setPicking(null);
}

async function loadTerminals() {
  const terminals = await fetch("/api/terminals").then((r) => r.json());
  map.addSource("terminals", {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: terminals.filter((t) => t.is_connected).map((t) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [t.lon, t.lat] },
        properties: { name: t.name, type: t.type, country: t.country },
      })),
    },
  });
  // A 2px surface ring keeps the dot legible where a route line crosses it.
  map.addLayer({
    id: "terminals", type: "circle", source: "terminals",
    paint: {
      "circle-radius": 4.5, "circle-color": "#ffffff",
      "circle-stroke-color": "#414a57", "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "terminal-labels", type: "symbol", source: "terminals", minzoom: 4.6,
    layout: {
      "text-field": ["get", "name"], "text-size": 11,
      "text-offset": [0, 1.1], "text-anchor": "top", "text-font": ["Noto Sans Regular"],
    },
    paint: { "text-color": "#414a57", "text-halo-color": "#ffffff", "text-halo-width": 1.5 },
  });

  const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
  map.on("mouseenter", "terminals", (event) => {
    map.getCanvas().style.cursor = "pointer";
    const { name, type, country } = event.features[0].properties;
    hover.setLngLat(event.features[0].geometry.coordinates)
      .setHTML(`<strong>${name}</strong>${type} · ${country}`).addTo(map);
  });
  map.on("mouseleave", "terminals", () => {
    map.getCanvas().style.cursor = picking ? "crosshair" : "";
    hover.remove();
  });
  paintBasemap(currentTheme());
}

function clearRoute() {
  if (!map) return;
  drawnLayers.forEach((id) => {
    [`${id}-hit`, id].forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
    if (map.getSource(id)) map.removeSource(id);
  });
  drawnLayers = [];
}

function terminalCoordinate(name) {
  const source = map && map.getSource("terminals");
  if (!source) return null;
  const match = source._data.features.find((f) => f.properties.name === name);
  return match ? match.geometry.coordinates : null;
}

/** Draw one alternative. Legs without geometry are dashed: schematic, not surveyed. */
function drawAlternative(alternative, scenario, total) {
  if (!map || !alternative) return;
  clearRoute();
  const bounds = new maplibregl.LngLatBounds();
  // Popups quote the scenario on screen, not the one the geometry was priced under.
  const priced = legsUnder(scenario, alternative, total);

  priced.forEach((leg, index) => {
    let coordinates = leg.geometry;
    const schematic = !coordinates.length;
    if (schematic) {
      // A ferry inside a road leg has no endpoints of its own; the road leg it
      // belongs to is already drawn, so there is nothing separate to show.
      const from = terminalCoordinate(leg.from_name);
      const to = terminalCoordinate(leg.to_name);
      if (!from || !to) return;
      coordinates = [from, to];
    }
    coordinates.forEach((point) => bounds.extend(point));

    const id = `leg-${index}`;
    map.addSource(id, {
      type: "geojson",
      data: {
        type: "Feature",
        geometry: { type: "LineString", coordinates },
        properties: {
          label: `${leg.from_name} → ${leg.to_name}`,
          mode: MODE_LABELS[leg.mode] ?? leg.mode,
          km: nf.format(leg.distance_km),
          co2: nf.format(leg.co2_kg),
          factor: `${nf3.format(leg.factor_value)} kg CO2/ton-km`,
          schematic: schematic ? "1" : "",
        },
      },
    });
    map.addLayer({
      id, type: "line", source: id,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": modeColour(leg.mode),
        "line-width": 3,
        "line-dasharray": schematic ? [2, 1.6] : [1],
      },
    });
    // An invisible fat line under the thin one: the hit target is bigger than the mark.
    map.addLayer({
      id: `${id}-hit`, type: "line", source: id,
      paint: { "line-color": "#000", "line-opacity": 0, "line-width": 18 },
    });
    drawnLayers.push(id);
  });

  attachLegHover();
  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 50, duration: 600 });
}

function attachLegHover() {
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
  drawnLayers.forEach((id) => {
    const hit = `${id}-hit`;
    map.on("mousemove", hit, (event) => {
      map.getCanvas().style.cursor = "pointer";
      const p = event.features[0].properties;
      popup.setLngLat(event.lngLat).setHTML(
        `<strong>${p.label}</strong>${p.mode} · ${p.km} km · ${p.co2} kg CO2<br>`
        + `<span style="color:#6e7783">faktör ${p.factor}${p.schematic ? " · şematik" : ""}</span>`,
      ).addTo(map);
    });
    map.on("mouseleave", hit, () => {
      map.getCanvas().style.cursor = picking ? "crosshair" : "";
      popup.remove();
    });
  });
}

/* ── scenario controls ───────────────────────────────────────────────── */

function renderScenarioBar() {
  const sets = [...new Set(payload.scenarios.map((s) => s.factor_set))];
  const current = currentScenario();

  $("scenario-set").innerHTML = sets.map((name) => {
    const usable = payload.scenarios.some((s) => s.factor_set === name && !s.error);
    return `<button type="button" class="seg-btn" role="radio" data-set="${name}"
      aria-checked="${name === current.factor_set}" ${usable ? "" : "disabled"}
      title="${(factorSets.find((f) => f.name === name)?.description) ?? ""}"
      >${SET_LABELS[name] ?? name}</button>`;
  }).join("");

  const scopes = payload.scenarios
    .filter((s) => s.factor_set === current.factor_set)
    .map((s) => s.scope);
  $("scenario-scope").innerHTML = ["TTW", "WTW"].map((scope) => {
    const found = payload.scenarios.find(
      (s) => s.factor_set === current.factor_set && s.scope === scope,
    );
    return `<button type="button" class="seg-btn" role="radio" data-scope="${scope}"
      aria-checked="${scope === current.scope}" ${found && !found.error ? "" : "disabled"}
      >${scope}</button>`;
  }).join("");

  const seaFactor = factorSets.find((f) => f.name === current.factor_set)
    ?.sea_factor_by_scope?.[current.scope];
  $("scenario-note").textContent =
    (factorSets.find((f) => f.name === current.factor_set)?.description ?? "")
    + (seaFactor ? ` · deniz faktörü ${seaFactor} kg CO2/ton-km` : "")
    + (scopes.includes("WTW") ? "" : " · bu set WTW vermiyor");

  $("scenario-set").querySelectorAll("[data-set]").forEach((b) =>
    b.addEventListener("click", () => selectScenario(b.dataset.set, current.scope)));
  $("scenario-scope").querySelectorAll("[data-scope]").forEach((b) =>
    b.addEventListener("click", () => selectScenario(current.factor_set, b.dataset.scope)));
}

function selectScenario(factorSet, scope) {
  const exact = payload.scenarios.find(
    (s) => s.factor_set === factorSet && s.scope === scope && !s.error,
  );
  const fallback = payload.scenarios.find((s) => s.factor_set === factorSet && !s.error);
  const chosen = exact ?? fallback;
  if (!chosen) return;
  scenarioKey = keyOf(chosen);
  applyScenario();
}

/* ── dashboard ───────────────────────────────────────────────────────── */

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Count an element from its last value to a new one.
 *
 * Scenario switches are the product's argument: watching a figure travel from 1,260 to
 * 6,120 as the ro-ro basis changes says more than replacing the text ever could. The
 * element remembers its own value so the tween survives re-entry.
 */
function tween(element, to, format) {
  const from = Number(element.dataset.value);
  element.dataset.value = to;
  if (!Number.isFinite(from) || from === to || reducedMotion()) {
    element.textContent = format(to);
    return;
  }
  const started = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - started) / 420);
    const eased = 1 - (1 - t) ** 3;
    element.textContent = format(from + (to - from) * eased);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function flash(element) {
  if (reducedMotion()) return;
  element.classList.remove("flash");
  void element.offsetWidth;
  element.classList.add("flash");
}

/** Build the tiles once. Re-creating them on every switch would restart the animations. */
function buildKpis() {
  $("kpi-row").innerHTML = [
    ["route", "Seçilen rota", "kg CO2"],
    ["delta", "Tam karayoluna fark", "kg CO2"],
    ["range", "Belirsizlik aralığı", ""],
    ["sens", "Ro-ro esasına duyarlılık", ""],
  ].map(([key, label, unit]) => `<article class="kpi" data-kpi="${key}">
      <p class="kpi-label">${label}</p>
      <p class="kpi-value"><span data-num></span>${unit ? `<span class="unit">${unit}</span>` : ""}</p>
      <p class="kpi-sub"></p>
    </article>`).join("");
}

function updateKpis(scenario) {
  const totals = scenario.totals;
  const chosen = totals[selectedIndex] ?? totals[0];
  const baseline = totals.find((t) => t.is_all_road);
  const delta = chosen.saving_co2_kg;

  // Only the GLEC sets count here: the customer's own set is a different methodology,
  // not a basis choice, and folding it in stretched the band into meaninglessness.
  const acrossBases = payload.scenarios
    .filter((s) => !s.error && s.scope === scenario.scope && s.factor_set.startsWith("glec"))
    .map((s) => s.totals.find((t) => t.label === chosen.label))
    .filter(Boolean)
    .map((t) => t.total_co2_kg);
  const low = Math.min(...acrossBases);
  const high = Math.max(...acrossBases);
  const ratio = acrossBases.length > 1 && low ? high / low : null;

  const range = chosen.emission_range;
  // The band's width relative to the estimate. A bar that always filled its track
  // looked like a measure while carrying nothing.
  const bandWidth = range && chosen.total_co2_kg
    ? ((range.high_co2_kg - range.low_co2_kg) / 2 / chosen.total_co2_kg) * 100
    : null;

  const tile = (key) => $("kpi-row").querySelector(`[data-kpi="${key}"]`);
  const set = (key, value, format, sub, tone) => {
    const element = tile(key);
    tween(element.querySelector("[data-num]"), value, format);
    element.querySelector(".kpi-sub").textContent = sub;
    element.classList.toggle("good", tone === "good");
    element.classList.toggle("bad", tone === "bad");
    flash(element.querySelector(".kpi-value"));
  };

  set("route", chosen.total_co2_kg, (v) => nf.format(v), chosen.label);
  set("delta", delta === null ? 0 : -delta, (v) => signed(v),
    delta === null ? "temel yok"
      : delta > 0 ? `%${nf.format((delta / baseline.total_co2_kg) * 100)} daha az`
      : delta < 0 ? `%${nf.format((-delta / baseline.total_co2_kg) * 100)} daha fazla`
      : "karşılaştırma temeli",
    delta === null || delta === 0 ? "" : delta > 0 ? "good" : "bad");
  set("range", range ? range.low_co2_kg : 0,
    (v) => (range ? `${nf.format(v)} – ${nf.format(range.high_co2_kg)} kg` : "—"),
    range ? `±%${bandWidth.toLocaleString("tr-TR", { maximumFractionDigits: 1 })} · %${
      Math.round(range.confidence * 100)} güven · Monte Carlo` : "hesaplanamadı");
  set("sens", ratio ?? 0,
    (v) => (ratio ? `×${v.toLocaleString("tr-TR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}` : "—"),
    ratio === null ? "tek esas" : `${nf.format(low)}–${nf.format(high)} kg · ${acrossBases.length} GLEC esası`);
}

function buildComparison(scenario) {
  $("comparison-chart").innerHTML = `<div class="bars">${
    scenario.totals.map((total, index) => `<button type="button" class="bar-row" data-index="${index}">
      <span class="bar-name">${total.label}${
        total.is_all_road ? '<span class="baseline-tag">temel</span>' : ""}</span>
      <span class="bar-track"><span class="bar-stack">${
        MODE_ORDER.map((m) => `<span class="${m}" data-mode="${m}" style="flex:0 0 0%"></span>`).join("")
      }</span></span>
      <span class="bar-value"><span data-num></span></span>
    </button>`).join("")}</div>
    <div class="legend-row">
      ${MODE_ORDER.map((m) =>
        `<span class="key"><span class="swatch ${m}"></span>${MODE_LABELS[m]}</span>`).join("")}
      <span class="key" style="color:var(--ink-muted)">değerler kg CO2</span>
    </div>`;

  const rows = [...$("comparison-chart").querySelectorAll(".bar-row")];
  rows.forEach((element, index) => {
    element.addEventListener("click", () => { selectedIndex = index; applyScenario(); });
    element.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      rows[(index + step + rows.length) % rows.length].focus();
    });
  });
}

function updateComparison(scenario) {
  const totals = scenario.totals;
  const max = Math.max(...totals.map((t) => t.total_co2_kg), 1);

  $("comparison-chart").querySelectorAll(".bar-row").forEach((element, index) => {
    const total = totals[index];
    if (!total) return;
    element.setAttribute("aria-current", String(index === selectedIndex));
    element.setAttribute("aria-label",
      `${total.label}: ${nf.format(total.total_co2_kg)} kg CO2`);
    element.querySelector(".bar-stack").style.width = `${(total.total_co2_kg / max) * 100}%`;
    MODE_ORDER.forEach((mode) => {
      const share = (total.co2_by_mode[mode] ?? 0) / total.total_co2_kg;
      const segment = element.querySelector(`[data-mode="${mode}"]`);
      segment.style.flexBasis = `${share * 100}%`;
      // A zero-width segment still shows its 2px minimum and its gap, so hide it.
      segment.style.display = share ? "" : "none";
      segment.title = `${MODE_LABELS[mode]}: ${nf.format(total.co2_by_mode[mode] ?? 0)} kg CO2`;
    });
    tween(element.querySelector("[data-num]"), total.total_co2_kg, (v) => nf.format(v));
  });
}

function buildSensitivity() {
  const rows = payload.scenarios.filter((s) => !s.error);
  if (rows.length < 2) { $("sensitivity").innerHTML = ""; return; }
  $("sensitivity").innerHTML = rows.map((s) => `<div class="sens-row" data-key="${keyOf(s)}">
      <span class="sens-name">${SET_LABELS[s.factor_set] ?? s.factor_set} · ${s.scope}</span>
      <span class="sens-track"><span class="axis"></span>
        <span class="baseline"></span><span class="dot"></span></span>
      <span class="sens-value"></span>
    </div>`).join("")
    + `<div class="sens-scale"><span data-scale="min"></span><span data-scale="max"></span></div>
       <div class="legend-row">
         <span class="key"><span style="width:.6rem;height:.6rem;border-radius:50%;background:var(--good);display:inline-block"></span>karayolundan iyi</span>
         <span class="key"><span style="width:.6rem;height:.6rem;border-radius:50%;background:var(--bad);display:inline-block"></span>karayolundan kötü</span>
         <span class="key"><span style="width:2px;height:.8rem;background:var(--ink-muted);display:inline-block"></span>tam karayolu</span>
       </div>`;
}

function updateSensitivity(scenario) {
  const container = $("sensitivity");
  if (!container.children.length) return;
  const chosen = scenario.totals[selectedIndex] ?? scenario.totals[0];

  const rows = [...container.querySelectorAll(".sens-row")].map((element) => {
    const s = payload.scenarios.find((x) => keyOf(x) === element.dataset.key);
    return {
      element,
      scenario: s,
      total: s.totals.find((t) => t.label === chosen.label),
      baseline: s.totals.find((t) => t.is_all_road),
    };
  });

  const values = rows.flatMap((r) => [r.total?.total_co2_kg, r.baseline?.total_co2_kg]).filter(Boolean);
  const min = Math.min(...values) * 0.92;
  const max = Math.max(...values) * 1.04;
  const at = (v) => ((v - min) / (max - min)) * 100;

  rows.forEach(({ element, scenario: s, total, baseline }) => {
    element.hidden = !total;
    if (!total) return;
    element.classList.toggle("current", keyOf(s) === scenarioKey);
    const worse = total.saving_co2_kg !== null && total.saving_co2_kg < 0;
    element.querySelector(".dot").style.left = `${at(total.total_co2_kg)}%`;
    element.querySelector(".dot").style.background = worse ? "var(--bad)" : "var(--good)";
    element.querySelector(".dot").title =
      `${total.label}: ${nf.format(total.total_co2_kg)} kg CO2`;
    const line = element.querySelector(".baseline");
    line.hidden = !baseline;
    if (baseline) {
      line.style.left = `${at(baseline.total_co2_kg)}%`;
      line.title = `tam karayolu ${nf.format(baseline.total_co2_kg)} kg`;
    }
    element.querySelector(".sens-value").textContent = `${nf.format(total.total_co2_kg)} kg`;
  });

  container.querySelector('[data-scale="min"]').textContent = nf.format(min);
  container.querySelector('[data-scale="max"]').textContent = `${nf.format(max)} kg CO2`;
}

function renderLegDetail(scenario) {
  const totals = scenario.totals;
  const chosen = totals[selectedIndex] ?? totals[0];
  const alternative = payload.alternatives.find((a) => a.label === chosen.label);
  $("leg-route-name").textContent = chosen.label;

  if (!alternative) { $("leg-detail").innerHTML = ""; return; }

  $("leg-detail").innerHTML = `<table>
    <thead><tr><th>Bacak</th><th>km</th><th>kg CO2</th><th>faktör</th></tr></thead>
    <tbody>${legsUnder(scenario, alternative, chosen).map((leg) => `<tr>
      <td><span class="leg-mark ${leg.mode}"></span>${leg.from_name} → ${leg.to_name}</td>
      <td class="num">${nf.format(leg.distance_km)}</td>
      <td class="num">${nf.format(leg.co2_kg)}</td>
      <td class="num">${nf3.format(leg.factor_value)}</td>
    </tr>`).join("")}</tbody>
    <tfoot><tr>
      <td>Toplam</td>
      <td class="num">${nf.format(alternative.total_distance_km)}</td>
      <td class="num">${nf.format(chosen.total_co2_kg)}</td>
      <td></td>
    </tr></tfoot>
  </table>`;

  $("provenance").textContent =
    `Faktör seti ${scenario.factor_set} · kapsam ${scenario.scope} · `
    + (scenario.sources.join("; ") || "doğrulanmamış");
}

function renderNotices(scenario) {
  const messages = [...scenario.warnings];
  if (!scenario.is_verified) {
    messages.unshift("Bu faktör seti doğrulanmamış değer içeriyor — rapora girmemeli.");
  }
  $("notice-slot").innerHTML = messages.length
    ? `<div class="notice"><strong>Uyarılar</strong><ul>${
        messages.map((m) => `<li>${m}</li>`).join("")}</ul></div>`
    : "";
}

/** Build the parts whose shape depends on the routes, not on the scenario. */
function buildDashboard() {
  buildKpis();
  buildComparison(currentScenario());
  buildSensitivity();
  $("map-legend").innerHTML = MODE_ORDER
    .map((m) => `<span class="key"><span class="swatch ${m}"></span>${MODE_LABELS[m]}</span>`)
    .join("") + '<span class="key"><span class="dashed-key"></span>şematik</span>';
}

/** Update values in place so the bars and dots animate between scenarios. */
function applyScenario() {
  const scenario = currentScenario();
  if (!scenario) return;
  if (selectedIndex >= scenario.totals.length) selectedIndex = 0;

  renderScenarioBar();
  updateKpis(scenario);
  renderNotices(scenario);
  updateComparison(scenario);
  updateSensitivity(scenario);
  renderLegDetail(scenario);

  const chosen = scenario.totals[selectedIndex];
  drawAlternative(payload.alternatives.find((a) => a.label === chosen.label), scenario, chosen);
}

/* ── requests ────────────────────────────────────────────────────────── */

async function loadFactorSets() {
  const all = await fetch("/api/factor-sets").then((r) => r.json());
  // A set without a factor for every mode can only ever answer with an error, so it is
  // never offered as a scenario.
  factorSets = all.filter((set) => Object.keys(set.sea_factor_by_scope).length > 0);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const origin = parsePoint(data.get("origin"));
  const destination = parsePoint(data.get("destination"));
  if (!origin || !destination) {
    statusLine.textContent = "Kalkış ve varış 'boylam, enlem' biçiminde olmalı.";
    return;
  }

  // Every scenario the bar offers is priced in this one request; switching afterwards
  // costs nothing, while re-routing would cost seconds and seven OSRM calls.
  const scenarios = factorSets.flatMap((set) =>
    set.scopes.map((scope) => ({ factor_set: set.name, scope })));

  const body = {
    origin, destination,
    origin_name: data.get("origin_name") || "kalkış",
    destination_name: data.get("destination_name") || "varış",
    tonnage: Number(data.get("tonnage")),
    factor_set: "glec",
    scope: "TTW",
    scenarios,
  };
  if (data.get("load_factor")) body.load_factor = Number(data.get("load_factor"));
  if (data.get("empty_return_share")) body.empty_return_share = Number(data.get("empty_return_share"));

  submitButton.disabled = true;
  statusLine.textContent = "Rotalanıyor — soğuk istek birkaç saniye sürebilir…";
  showSkeleton();
  try {
    const response = await fetch("/api/routes", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) {
      emptyState.innerHTML = `<div class="error">${result.detail ?? "İstek başarısız."}</div>`;
      emptyState.hidden = false; dashboard.hidden = true;
      return;
    }
    payload = result;
    const first = result.scenarios.find((s) => !s.error) ?? result.scenarios[0];
    scenarioKey = keyOf(first);
    // Land on a multimodal option rather than the baseline: the baseline compares to
    // itself, and its emissions do not move with the sea factor, so both the delta and
    // the sensitivity panel would open flat and say nothing.
    const firstMultimodal = first.totals.findIndex((t) => !t.is_all_road);
    selectedIndex = firstMultimodal === -1 ? 0 : firstMultimodal;
    $("shipment-summary").textContent =
      `${data.get("origin_name")} → ${data.get("destination_name")} · ${data.get("tonnage")} ton`;
    emptyState.hidden = true; dashboard.hidden = false;
    if (map) map.resize();
    buildDashboard();
    applyScenario();
  } catch (error) {
    emptyState.innerHTML = `<div class="error">Sunucuya ulaşılamadı: ${error.message}</div>`;
    emptyState.hidden = false; dashboard.hidden = true;
  } finally {
    submitButton.disabled = false;
    statusLine.textContent = "";
  }
});

const reportForm = $("report-form");
const reportStatus = $("report-status");
const reportSubmit = $("report-submit");

reportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = new FormData(reportForm);
  const scenario = currentScenario();
  // The report is priced with the scenario on screen, so one dashboard cannot hand
  // back two different answers for the same shipment.
  body.set("scope", scenario?.scope ?? "TTW");
  body.set("factor_set", scenario?.factor_set ?? "glec");
  const loadFactor = new FormData(form).get("load_factor");
  const emptyReturn = new FormData(form).get("empty_return_share");
  if (loadFactor) body.set("load_factor", loadFactor);
  if (emptyReturn) body.set("empty_return_share", emptyReturn);

  reportSubmit.disabled = true;
  reportStatus.textContent = "Rapor hazırlanıyor — her sevkiyat ayrı rotalanıyor…";
  try {
    const response = await fetch("/api/report", { method: "POST", body });
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      reportStatus.textContent = problem.detail ?? `İstek başarısız (${response.status}).`;
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "freightprint-rapor.csv"; link.click();
    URL.revokeObjectURL(url);
    reportStatus.textContent = "Rapor indirildi.";
  } catch (error) {
    reportStatus.textContent = `Sunucuya ulaşılamadı: ${error.message}`;
  } finally {
    reportSubmit.disabled = false;
  }
});

$("pick-origin").addEventListener("click", () => setPicking("origin"));
$("pick-destination").addEventListener("click", () => setPicking("destination"));
[originInput, destinationInput].forEach((input) =>
  input.addEventListener("change", placeEndpointMarkers));

/* ── theme ───────────────────────────────────────────────────────────── */

const themeToggle = $("theme-toggle");
const osPrefersDark = window.matchMedia("(prefers-color-scheme: dark)");

function currentTheme() {
  return document.documentElement.dataset.theme
    ?? (osPrefersDark.matches ? "dark" : "light");
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("freightprint-theme", theme);
  themeToggle.textContent = theme === "dark" ? "☀︎ Açık tema" : "☾ Koyu tema";
  paintBasemap(theme);
  // MapLibre keeps its own canvas, so the route has to be repainted in the new tokens.
  if (payload) requestAnimationFrame(applyScenario);
}

/** Dim the basemap only. A CSS filter on the canvas would drag the route lines down
 *  with it; the raster layer's own paint properties leave the vector marks alone. */
function paintBasemap(theme) {
  if (!map || !map.getLayer("osm")) return;
  const dark = theme === "dark";
  map.setPaintProperty("osm", "raster-brightness-max", dark ? 0.5 : 1);
  map.setPaintProperty("osm", "raster-saturation", dark ? -0.35 : 0);
  map.setPaintProperty("osm", "raster-contrast", dark ? -0.12 : 0);
  if (map.getLayer("terminal-labels")) {
    map.setPaintProperty("terminal-labels", "text-color", dark ? "#b9c0ca" : "#414a57");
    map.setPaintProperty("terminal-labels", "text-halo-color", dark ? "#14171c" : "#ffffff");
  }
  if (map.getLayer("terminals")) {
    map.setPaintProperty("terminals", "circle-color", dark ? "#14171c" : "#ffffff");
    map.setPaintProperty("terminals", "circle-stroke-color", dark ? "#b9c0ca" : "#414a57");
  }
}

const savedTheme = localStorage.getItem("freightprint-theme");
if (savedTheme) applyTheme(savedTheme);
else themeToggle.textContent = osPrefersDark.matches ? "☀︎ Açık tema" : "☾ Koyu tema";
themeToggle.addEventListener("click", () =>
  applyTheme(currentTheme() === "dark" ? "light" : "dark"));

/* ── loading ─────────────────────────────────────────────────────────── */

/** A shaped placeholder beats a spinner: a cold route takes about six seconds. */
function showSkeleton() {
  emptyState.hidden = false;
  dashboard.hidden = true;
  emptyState.innerHTML = `<div class="skeleton" aria-busy="true" aria-live="polite">
      <div class="sk short" style="height:56px"></div>
      <div class="sk-row">${'<div class="sk tile"></div>'.repeat(4)}</div>
      <div class="sk wide"></div>
      <div class="sk short"></div>
    </div>`;
}

initMap();
loadFactorSets();

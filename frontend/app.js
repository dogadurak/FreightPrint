// Slots 1-3 of the dataviz reference palette, checked against this surface with the
// skill's validator: lightness band, chroma floor, CVD separation and the normal-vision
// floor all pass on both the adjacent and all-pairs lists.
const MODE_COLOURS = { road: "#eb6834", sea: "#2a78d6", rail: "#1baf7a" };
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
  map.on("load", () => { loadTerminals(); placeEndpointMarkers(); });
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
function drawAlternative(alternative, totals) {
  if (!map || !alternative) return;
  clearRoute();
  const bounds = new maplibregl.LngLatBounds();
  const co2ByLeg = alternative.legs.map((leg) => leg.co2_kg);

  alternative.legs.forEach((leg, index) => {
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
          co2: nf.format(co2ByLeg[index]),
          factor: `${nf3.format(leg.factor_value)} kg CO2/ton-km`,
          schematic: schematic ? "1" : "",
        },
      },
    });
    map.addLayer({
      id, type: "line", source: id,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": MODE_COLOURS[leg.mode] ?? "#6e7783",
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
  renderDashboard();
}

/* ── dashboard ───────────────────────────────────────────────────────── */

function totalsFor(scenario) {
  return scenario.totals;
}

function renderKpis(scenario) {
  const totals = totalsFor(scenario);
  const chosen = totals[selectedIndex] ?? totals[0];
  const baseline = totals.find((t) => t.is_all_road);
  const delta = chosen.saving_co2_kg;

  // How far the same route moves across the ro-ro accounting bases. Only the GLEC sets
  // count: the customer's own set is a different methodology, not a basis choice, and
  // folding it in would inflate the band into meaninglessness.
  const acrossBases = payload.scenarios
    .filter((s) => !s.error && s.scope === scenario.scope && s.factor_set.startsWith("glec"))
    .map((s) => s.totals.find((t) => t.label === chosen.label))
    .filter(Boolean)
    .map((t) => t.total_co2_kg);
  const low = Math.min(...acrossBases);
  const high = Math.max(...acrossBases);
  const ratio = acrossBases.length > 1 && low ? high / low : null;

  const range = chosen.emission_range;
  const bandFill = range && range.high_co2_kg > range.low_co2_kg
    ? `<div class="kpi-band"><i style="left:0;right:0"></i></div>` : "";

  const tiles = [
    `<article class="kpi">
      <p class="kpi-label">Seçilen rota</p>
      <p class="kpi-value">${nf.format(chosen.total_co2_kg)}<span class="unit">kg CO2</span></p>
      <p class="kpi-sub">${chosen.label}</p>
    </article>`,

    `<article class="kpi ${delta === null ? "" : delta > 0 ? "good" : delta < 0 ? "bad" : ""}">
      <p class="kpi-label">Tam karayoluna fark</p>
      <p class="kpi-value">${delta === null ? "—" : signed(-delta)}<span class="unit">kg CO2</span></p>
      <p class="kpi-sub">${
        delta === null ? "temel yok"
        : delta > 0 ? `%${nf.format((delta / baseline.total_co2_kg) * 100)} daha az`
        : delta < 0 ? `%${nf.format((-delta / baseline.total_co2_kg) * 100)} daha fazla`
        : "karşılaştırma temeli"}</p>
    </article>`,

    `<article class="kpi">
      <p class="kpi-label">Belirsizlik aralığı</p>
      <p class="kpi-value">${range ? nf.format(range.low_co2_kg) : "—"}<span class="unit">–
        ${range ? nf.format(range.high_co2_kg) : ""} kg</span></p>
      <p class="kpi-sub">${range ? `%${Math.round(range.confidence * 100)} güven · Monte Carlo` : "hesaplanamadı"}</p>
      ${bandFill}
    </article>`,

    `<article class="kpi">
      <p class="kpi-label">Ro-ro esasına duyarlılık</p>
      <p class="kpi-value">${
        ratio === null ? "—"
        : `×${ratio.toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`}</p>
      <p class="kpi-sub">${
        ratio === null ? "tek esas"
        : `${nf.format(low)}–${nf.format(high)} kg · ${acrossBases.length} GLEC esası`}</p>
    </article>`,
  ];
  $("kpi-row").innerHTML = tiles.join("");
}

function renderComparison(scenario) {
  const totals = totalsFor(scenario);
  const max = Math.max(...totals.map((t) => t.total_co2_kg), 1);

  const rows = totals.map((total, index) => {
    const segments = MODE_ORDER.filter((m) => total.co2_by_mode[m])
      .map((m) => `<span class="${m}" style="flex:0 0 ${
        (total.co2_by_mode[m] / total.total_co2_kg) * 100
      }%" title="${MODE_LABELS[m]}: ${nf.format(total.co2_by_mode[m])} kg"></span>`)
      .join("");
    const share = (total.total_co2_kg / max) * 100;
    return `<button type="button" class="bar-row" data-index="${index}"
        aria-current="${index === selectedIndex}"
        aria-label="${total.label}: ${nf.format(total.total_co2_kg)} kg CO2">
        <span class="bar-name">${total.label}${
          total.is_all_road ? '<span class="baseline-tag">temel</span>' : ""}</span>
        <span class="bar-track"><span class="bar-stack" style="width:${share}%">${segments}</span></span>
        <span class="bar-value">${nf.format(total.total_co2_kg)}</span>
      </button>`;
  }).join("");

  $("comparison-chart").innerHTML = `<div class="bars">${rows}</div>
    <div class="legend-row">
      ${MODE_ORDER.map((m) =>
        `<span class="key"><span class="swatch ${m}"></span>${MODE_LABELS[m]}</span>`).join("")}
      <span class="key" style="color:var(--ink-muted)">değerler kg CO2</span>
    </div>`;

  $("comparison-chart").querySelectorAll(".bar-row").forEach((element) =>
    element.addEventListener("click", () => {
      selectedIndex = Number(element.dataset.index);
      renderDashboard();
    }));
}

function renderSensitivity(scenario) {
  const chosen = totalsFor(scenario)[selectedIndex] ?? totalsFor(scenario)[0];
  const rows = payload.scenarios
    .filter((s) => !s.error && s.totals.some((t) => t.label === chosen.label))
    .map((s) => ({
      scenario: s,
      total: s.totals.find((t) => t.label === chosen.label),
      baseline: s.totals.find((t) => t.is_all_road),
    }));
  if (rows.length < 2) { $("sensitivity").innerHTML = ""; return; }

  const values = rows.flatMap((r) => [r.total.total_co2_kg, r.baseline?.total_co2_kg]).filter(Boolean);
  const min = Math.min(...values) * 0.92;
  const max = Math.max(...values) * 1.04;
  const at = (v) => ((v - min) / (max - min)) * 100;

  $("sensitivity").innerHTML = rows.map((r) => {
    const isCurrent = keyOf(r.scenario) === scenarioKey;
    const worse = r.total.saving_co2_kg !== null && r.total.saving_co2_kg < 0;
    return `<div class="sens-row${isCurrent ? " current" : ""}">
      <span class="sens-name">${SET_LABELS[r.scenario.factor_set] ?? r.scenario.factor_set}
        · ${r.scenario.scope}</span>
      <span class="sens-track">
        <span class="axis"></span>
        ${r.baseline ? `<span class="baseline" style="left:${at(r.baseline.total_co2_kg)}%"
          title="tam karayolu ${nf.format(r.baseline.total_co2_kg)} kg"></span>` : ""}
        <span class="dot" style="left:${at(r.total.total_co2_kg)}%;background:${
          worse ? "var(--bad)" : "var(--good)"}"></span>
      </span>
      <span class="sens-value">${nf.format(r.total.total_co2_kg)} kg</span>
    </div>`;
  }).join("")
    + `<div class="sens-scale"><span>${nf.format(min)}</span><span>${nf.format(max)} kg CO2</span></div>
       <div class="legend-row">
         <span class="key"><span class="dot-key" style="width:.6rem;height:.6rem;border-radius:50%;background:var(--good);display:inline-block"></span>karayolundan iyi</span>
         <span class="key"><span class="dot-key" style="width:.6rem;height:.6rem;border-radius:50%;background:var(--bad);display:inline-block"></span>karayolundan kötü</span>
         <span class="key"><span style="width:2px;height:.8rem;background:var(--ink-muted);display:inline-block"></span>tam karayolu</span>
       </div>`;
}

function renderLegDetail(scenario) {
  const totals = totalsFor(scenario);
  const chosen = totals[selectedIndex] ?? totals[0];
  const alternative = payload.alternatives.find((a) => a.label === chosen.label);
  $("leg-route-name").textContent = chosen.label;

  if (!alternative) { $("leg-detail").innerHTML = ""; return; }
  // Leg geometry is priced under the primary scenario; scale each leg's share so the
  // detail always adds up to the total shown above it.
  const ratio = alternative.total_co2_kg ? chosen.total_co2_kg / alternative.total_co2_kg : 1;

  $("leg-detail").innerHTML = `<table>
    <thead><tr><th>Bacak</th><th>km</th><th>kg CO2</th><th>faktör</th></tr></thead>
    <tbody>${alternative.legs.map((leg) => `<tr>
      <td><span class="leg-mark ${leg.mode}"></span>${leg.from_name} → ${leg.to_name}</td>
      <td class="num">${nf.format(leg.distance_km)}</td>
      <td class="num">${nf.format(leg.co2_kg * ratio)}</td>
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

function renderDashboard() {
  const scenario = currentScenario();
  if (!scenario) return;
  const totals = totalsFor(scenario);
  if (selectedIndex >= totals.length) selectedIndex = 0;

  renderScenarioBar();
  renderKpis(scenario);
  renderNotices(scenario);
  renderComparison(scenario);
  renderSensitivity(scenario);
  renderLegDetail(scenario);

  $("map-legend").innerHTML = MODE_ORDER
    .map((m) => `<span class="key"><span class="swatch ${m}"></span>${MODE_LABELS[m]}</span>`)
    .join("") + '<span class="key"><span class="dashed-key"></span>şematik</span>';

  const alternative = payload.alternatives.find((a) => a.label === totals[selectedIndex].label);
  drawAlternative(alternative, totals);
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
    renderDashboard();
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

initMap();
loadFactorSets();

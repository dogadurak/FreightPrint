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

/** Read any palette token from the stylesheet, so the map follows the active theme. */
const token = (name, fallback = "#6e7783") =>
  getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim() || fallback;

// Six categorical slots, assigned in fixed order and never cycled: a seventh terminal
// becomes "diger" rather than reusing slot one, because a repeated colour reads as the
// same terminal. Validated in both themes with the dataviz checks.
const CATCHMENT_SLOTS = 6;
let catchmentData = null;
let catchmentVisible = false;
// id -> display name, so the catchment legend and tooltip can name a terminal the
// endpoint reports only by id.
const terminalNames = new Map();

// `reference` is the dataset's own basis, kept as a comparison point. Named for what it
// is rather than whose it is: the brief scopes this tool as a calculation engine, not an
// audit of anyone's report.
const SET_LABELS = {
  reference: "Karşılaştırma esası",
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
const nf1 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });
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
class TerrainControl {
  onAdd(map) {
    this._map = map;
    this._container = document.createElement('div');
    this._container.className = 'maplibregl-ctrl maplibregl-ctrl-group';
    const button = document.createElement('button');
    button.type = 'button';
    button.title = '3D/2D Görünüm';
    button.innerHTML = '3D';
    button.style.fontWeight = 'bold';
    button.style.fontFamily = 'inherit';
    this._is3D = false;
    
    button.onclick = () => {
      this._is3D = !this._is3D;
      button.innerHTML = this._is3D ? '2D' : '3D';
      if (this._is3D) {
        if (!map.getSource('terrain-source')) {
          map.addSource('terrain-source', {
            type: 'raster-dem',
            tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
            encoding: 'terrarium',
            maxzoom: 14,
          });
        }
        map.setTerrain({ source: 'terrain-source', exaggeration: 1.5 });
        map.setPitch(60);
      } else {
        map.setTerrain(null);
        map.setPitch(0);
      }
    };
    this._container.appendChild(button);
    return this._container;
  }

  onRemove() {
    this._container.parentNode.removeChild(this._container);
    this._map = undefined;
  }
}

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
          tiles: ["https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "© OpenStreetMap, © CARTO",
        },
      },
      layers: [{ id: "osm", type: "raster", source: "osm" }],
    },
    center: [18, 45],
    zoom: 3.4,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new TerrainControl(), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  map.on("load", async () => {
    await loadRiskZones();
    loadTerminals();
    placeEndpointMarkers();
    paintBasemap(currentTheme());
  });
  map.on("click", onMapClick);
}

/** Origin hollow, destination filled — the same pair of shapes the form's dots use, so
 *  the sidebar and the map say the endpoints are the same thing.
 *
 *  The destination used to be `#b3261e`, which on this map already means "risk zone
 *  crossed": one colour cannot carry a status and an identity at once. Distinguishing
 *  them by fill rather than by hue frees the red and survives colour-blind reading.
 */
function endpointMarker(kind, lngLat) {
  const element = document.createElement("div");
  const isOrigin = kind === "origin";
  element.style.cssText =
    "width:15px;height:15px;border-radius:50%;box-sizing:border-box;"
    + "box-shadow:0 0 0 2px #fff,0 1px 3px rgba(0,0,0,.4);"
    + (isOrigin ? "background:#fff;border:3.5px solid #10141a" : "background:#10141a");
  const marker = new maplibregl.Marker({ element, draggable: true })
    .setLngLat(lngLat)
    .setPopup(new maplibregl.Popup({ offset: 14, closeButton: false })
      .setText(isOrigin ? "Kalkış — sürükleyin" : "Varış — sürükleyin"))
    .addTo(map);
  marker.on("dragend", () => {
    (isOrigin ? originInput : destinationInput).value = formatPoint(marker.getLngLat());
    validatePoints();
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

/** Bring the map on screen so an endpoint can actually be picked on it.
 *
 *  The map lives inside the results dashboard, which stays hidden until the first
 *  calculation — so before then "haritadan seç" armed a map nobody could see and the
 *  button appeared to do nothing but change colour. Choosing where the freight starts
 *  is the step *before* calculating, so the map has to be available first.
 *
 *  Only the map is revealed: the rest of the dashboard has no numbers to show yet, and
 *  the two `<main>` panels are siblings in a two-column grid, so both being visible at
 *  once would drop the empty state into the sidebar's column.
 */
function revealMapForPicking() {
  if (!dashboard.hidden) return;
  emptyState.hidden = true;
  dashboard.hidden = false;
  dashboard.classList.add("map-only");
  if (map) map.resize();
  dashboard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setPicking(kind) {
  const hint = $("map-pick-hint");

  // Without MapLibre there is nothing to click, and arming a mode that cannot end
  // would leave the button lit for good.
  if (kind && !map) {
    statusLine.textContent =
      "Harita yüklenemedi; koordinatı elle yazabilir veya ok ile terminal seçebilirsiniz.";
    return;
  }

  picking = picking === kind ? null : kind;
  $("pick-origin").setAttribute("aria-pressed", String(picking === "origin"));
  $("pick-destination").setAttribute("aria-pressed", String(picking === "destination"));
  mapElement.classList.toggle("picking", picking !== null);

  if (picking) revealMapForPicking();
  hint.hidden = picking === null;
  hint.textContent = picking
    ? `${picking === "origin" ? "Kalkış" : "Varış"} için haritaya tıklayın — vazgeçmek için Esc.`
    : "";
}

function onMapClick(event) {
  if (!picking) return;
  const kind = picking;
  (kind === "origin" ? originInput : destinationInput).value = formatPoint(event.lngLat);
  placeEndpointMarkers();
  validatePoints();
  setPicking(null);
  // Confirm where it landed. Silence after a click reads as a click that missed.
  const hint = $("map-pick-hint");
  hint.hidden = false;
  hint.textContent =
    `${kind === "origin" ? "Kalkış" : "Varış"} güncellendi: ${formatPoint(event.lngLat)}`;
}

/* ── shipment form ───────────────────────────────────────────────────── */

/** Say a coordinate is unusable while it is being typed, not on submit.
 *
 *  `parsePoint` already refuses anything outside the globe, but it did so silently
 *  until the request went out, so a transposed lat/lon looked accepted right up to
 *  the point it failed. Returns whether the form can be sent.
 */
function validatePoints() {
  const problems = [];
  for (const [label, input] of [["Kalkış", originInput], ["Varış", destinationInput]]) {
    const ok = parsePoint(input.value) !== null;
    input.classList.toggle("is-invalid", !ok);
    if (!ok) problems.push(label);
  }
  const error = $("point-error");
  error.hidden = problems.length === 0;
  error.textContent = problems.length
    ? `${problems.join(" ve ")} için "boylam, enlem" bekleniyor — boylam ±180, enlem ±90.`
    : "";
  return problems.length === 0;
}

/** Send the freight the other way, names and coordinates together.
 *
 *  Swapping the coordinates alone would leave Gebze labelling Düsseldorf's point, and
 *  the label is what every chart, the report and the map popup then carry.
 */
function swapEndpoints() {
  const fields = [
    [originInput, destinationInput],
    [form.elements.origin_name, form.elements.destination_name],
  ];
  for (const [from, to] of fields) [from.value, to.value] = [to.value, from.value];
  placeEndpointMarkers();
  validatePoints();
}

/* ── terminal picker ─────────────────────────────────────────────────── */

const TERMINAL_KINDS = {
  roro_port: "ro-ro limanı",
  port: "liman",
  rail_terminal: "demiryolu terminali",
  roro_rail_hub: "ro-ro + demiryolu",
};

/** Fetched once and shared by both endpoints, and deliberately not through the map's
 *  loader: `loadTerminals` runs inside `map.on("load")`, so a browser that could not
 *  load MapLibre would leave the picker permanently empty. The picker is exactly what
 *  such a browser needs most, since clicking the map is not available to it. */
let terminalsPromise = null;
const knownTerminals = () => (
  terminalsPromise ??= fetch("/api/terminals").then((r) => (r.ok ? r.json() : []))
);

/** Fill an endpoint from a terminal: name and coordinates together.
 *
 *  Setting only the name would leave the label describing one place and the coordinate
 *  another, and the label is what the report, the charts and the map popups then carry.
 */
function useTerminal(terminal, isOrigin) {
  (isOrigin ? originInput : destinationInput).value = `${terminal.lon}, ${terminal.lat}`;
  form.elements[isOrigin ? "origin_name" : "destination_name"].value = terminal.name;
  placeEndpointMarkers();
  validatePoints();
  if (map) map.flyTo({ center: [terminal.lon, terminal.lat], zoom: 6, duration: 800 });
}

function terminalRow(terminal, isOrigin, close) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "terminal-row";

  const name = document.createElement("span");
  name.className = "terminal-name";
  name.textContent = terminal.name;

  const kind = document.createElement("span");
  kind.className = "terminal-kind";
  kind.textContent = TERMINAL_KINDS[terminal.type] ?? terminal.type.replace(/_/g, " ");

  row.append(name, kind);
  // A terminal the network cannot route through is still a perfectly good door to
  // collect from; saying so beats hiding it and beats offering it as if it were a hub.
  if (!terminal.is_connected) {
    row.classList.add("is-unconnected");
    row.title = "Ağa bağlı değil — uç nokta olur, aktarma merkezi olmaz";
  }
  row.addEventListener("click", () => { useTerminal(terminal, isOrigin); close(); });
  return row;
}

function setUpTerminalPicker(kind) {
  const isOrigin = kind === "origin";
  const caret = $(`terminals-${kind}`);
  const list = $(`terminal-list-${kind}`);

  const close = () => {
    list.hidden = true;
    caret.setAttribute("aria-expanded", "false");
  };

  caret.addEventListener("click", async () => {
    if (!list.hidden) { close(); return; }
    // Only one picker open at a time; two lists over one narrow sidebar overlap.
    document.querySelectorAll(".terminal-list").forEach((other) => { other.hidden = true; });
    document.querySelectorAll(".terminal-caret")
      .forEach((other) => other.setAttribute("aria-expanded", "false"));

    list.hidden = false;
    caret.setAttribute("aria-expanded", "true");
    list.textContent = "Yükleniyor…";
    try {
      const terminals = await knownTerminals();
      if (!terminals.length) { list.textContent = "Terminal listesi alınamadı."; return; }
      // Grouped by country, because a picker sorted only by name asks the reader to
      // know which country every port is in before they can find one.
      const byCountry = new Map();
      for (const terminal of terminals) {
        if (!byCountry.has(terminal.country)) byCountry.set(terminal.country, []);
        byCountry.get(terminal.country).push(terminal);
      }
      const groups = [...byCountry.entries()].sort((a, b) => a[0].localeCompare(b[0]));
      list.replaceChildren(...groups.flatMap(([country, members]) => {
        const heading = document.createElement("p");
        heading.className = "terminal-country";
        heading.textContent = country;
        return [
          heading,
          ...members
            .sort((a, b) => a.name.localeCompare(b.name, "tr"))
            .map((terminal) => terminalRow(terminal, isOrigin, close)),
        ];
      }));
    } catch {
      list.textContent = "Terminal listesi alınamadı.";
    }
  });

  // Clicking anywhere else, or pressing Escape, puts it away.
  document.addEventListener("click", (event) => {
    if (!list.hidden && !list.contains(event.target) && event.target !== caret) close();
  });
  list.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
}

/** Count the assumptions in use, so collapsing the section cannot hide one.
 *
 *  A load factor left set from an earlier run changes every figure on the dashboard.
 *  Tucking it behind a closed `<details>` without a mark would be the interface quietly
 *  keeping a number the user cannot see.
 */
function markAdvancedInUse() {
  const inUse = ["load_factor", "empty_return_share"]
    .filter((name) => form.elements[name].value.trim() !== "").length
    + (form.elements.road_fuel_type.value ? 1 : 0);
  const tag = $("advanced-tag");
  tag.hidden = inUse === 0;
  tag.textContent = `${inUse} değişti`;
}

/** Draw the listed areas under everything else, so a route line stays readable over
 *  them. Crossed zones are picked out; the rest stay quiet. */
async function loadRiskZones() {
  const zones = await fetch("/api/risk-zones").then((r) => r.json());
  // feature-state needs a stable id per feature; the array index is one.
  zones.features.forEach((feature, index) => { feature.id = index; });
  map.addSource("risk-zones", { type: "geojson", data: zones });
  map.addLayer({
    id: "risk-zone-fill", type: "fill", source: "risk-zones",
    paint: {
      "fill-color": ["case", ["boolean", ["feature-state", "crossed"], false],
        "#b3261e", "#8b93a0"],
      "fill-opacity": ["case", ["boolean", ["feature-state", "crossed"], false], 0.22, 0.09],
    },
  });
  map.addLayer({
    id: "risk-zone-line", type: "line", source: "risk-zones",
    paint: {
      "line-color": ["case", ["boolean", ["feature-state", "crossed"], false],
        "#b3261e", "#8b93a0"],
      "line-width": ["case", ["boolean", ["feature-state", "crossed"], false], 1.6, 0.8],
      "line-dasharray": [3, 2],
    },
  });

  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 6 });
  map.on("mousemove", "risk-zone-fill", (event) => {
    const p = event.features[0].properties;
    popup.setLngLat(event.lngLat).setHTML(
      `<strong>${p.name}</strong><span style="color:#6e7783">${p.source}</span>`,
    ).addTo(map);
  });
  map.on("mouseleave", "risk-zone-fill", () => popup.remove());
}

/** Mark which zones the drawn route enters. */
function highlightCrossedZones(zoneIds) {
  if (!map || !map.getSource("risk-zones")) return;
  const data = map.getSource("risk-zones")._data;
  data.features.forEach((feature, index) => {
    map.setFeatureState(
      { source: "risk-zones", id: index },
      { crossed: zoneIds.includes(feature.properties.id) },
    );
  });
}

/** Which terminal serves where, by driving time rather than by a circle on a map.
 *
 *  Drawn as squares at the spacing that was actually measured, deliberately not as
 *  smoothed polygons: the boundary between two samples was never computed, and a clean
 *  outline would claim a precision nobody paid for. Opacity carries the drive time, so
 *  the edge of a catchment fades rather than ending in a line.
 */
async function toggleCatchment() {
  const button = $("catchment-toggle");
  catchmentVisible = !catchmentVisible;
  button.setAttribute("aria-pressed", String(catchmentVisible));

  if (!catchmentVisible) {
    if (map.getLayer("catchment")) map.setLayoutProperty("catchment", "visibility", "none");
    $("catchment-legend").hidden = true;
    return;
  }

  if (!catchmentData) {
    button.disabled = true;
    // Measured at ~26s cold against the public OSRM demo server and instant afterwards,
    // so the wait is stated rather than left to look like a hang.
    button.textContent = "Hesaplanıyor — ilk sefer ~30 sn…";
    try {
      const response = await fetch("/api/catchment?spacing_deg=1&max_duration_h=10");
      if (!response.ok) throw new Error((await response.json()).detail || response.statusText);
      catchmentData = await response.json();
    } catch (error) {
      button.textContent = "Terminal etki alanı";
      button.disabled = false;
      catchmentVisible = false;
      button.setAttribute("aria-pressed", "false");
      $("catchment-legend").hidden = false;
      $("catchment-legend").innerHTML =
        `<p class="hint">Etki alanı hesaplanamadı: ${error.message}</p>`;
      return;
    }
    button.textContent = "Terminal etki alanı";
    button.disabled = false;
    drawCatchment();
  }

  if (map.getLayer("catchment")) map.setLayoutProperty("catchment", "visibility", "visible");
  $("catchment-legend").hidden = false;
}

function drawCatchment() {
  const half = catchmentData.spacing_deg / 2;
  // Rank terminals by how much they serve, so the busiest get the first slots and the
  // colour a reader sees most is the one they learn first.
  const ranked = Object.entries(catchmentData.cells_by_terminal)
    .sort((a, b) => b[1] - a[1])
    .map(([id]) => id);
  const slotOf = new Map(ranked.slice(0, CATCHMENT_SLOTS).map((id, i) => [id, i + 1]));
  const colourFor = (id) =>
    slotOf.has(id) ? token(`cat-${slotOf.get(id)}`) : token("cat-other");

  const features = catchmentData.cells.map((cell) => ({
    type: "Feature",
    geometry: {
      type: "Polygon",
      coordinates: [[
        [cell.lon - half, cell.lat - half], [cell.lon + half, cell.lat - half],
        [cell.lon + half, cell.lat + half], [cell.lon - half, cell.lat + half],
        [cell.lon - half, cell.lat - half],
      ]],
    },
    properties: {
      terminal: cell.terminal_id,
      hours: cell.duration_h,
      colour: colourFor(cell.terminal_id),
    },
  }));

  map.addSource("catchment", {
    type: "geojson", data: { type: "FeatureCollection", features },
  });
  // Under the risk zones and routes: this is context, not the answer.
  map.addLayer({
    id: "catchment", type: "fill", source: "catchment",
    paint: {
      "fill-color": ["get", "colour"],
      // Near the terminal it is solid; at the time limit it has almost faded out.
      "fill-opacity": [
        "interpolate", ["linear"], ["get", "hours"],
        0, 0.55,
        catchmentData.max_duration_h, 0.12,
      ],
    },
  }, "risk-zone-fill");

  const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 6 });
  map.on("mousemove", "catchment", (event) => {
    const { terminal, hours } = event.features[0].properties;
    const name = terminalNames.get(terminal) || terminal;
    hover.setLngLat(event.lngLat)
      .setHTML(`<strong>${name}</strong>${nf1.format(hours)} saat sürüş`)
      .addTo(map);
  });
  map.on("mouseleave", "catchment", () => hover.remove());

  const named = ranked.slice(0, CATCHMENT_SLOTS);
  const folded = ranked.slice(CATCHMENT_SLOTS);
  const rows = named.map((id) => {
    const name = terminalNames.get(id) || id;
    return `<span class="key"><span class="swatch"
      style="background:var(--cat-${slotOf.get(id)})"></span>${name} <span
      class="card-note">${catchmentData.cells_by_terminal[id]}</span></span>`;
  });
  if (folded.length) {
    const cells = folded.reduce((sum, id) => sum + catchmentData.cells_by_terminal[id], 0);
    rows.push(`<span class="key"><span class="swatch"
      style="background:var(--cat-other)"></span>diğer ${folded.length} terminal <span
      class="card-note">${cells}</span></span>`);
  }

  // Say why the rest share one colour rather than leaving grey looking like a gap:
  // reusing a hue would read as one catchment split across the map.
  const foldNote = folded.length
    ? `<p class="hint">En çok hizmet veren ${CATCHMENT_SLOTS} terminal ayrı renkte; kalan ${
        folded.length} tanesi tek renkte toplandı — rengi tekrar kullanmak iki ayrı
        terminali aynı etki alanı gibi gösterirdi. Hangi terminal olduğunu görmek için
        hücrenin üzerine gelin: ${folded.map((id) => terminalNames.get(id) || id).join(", ")}.</p>`
    : "";

  $("catchment-legend").innerHTML = `<div class="legend-row">${rows.join("")}</div>
    ${foldNote}
    ${catchmentData.notes.map((n) => `<p class="hint">${n}</p>`).join("")}`;
}

async function loadTerminals() {
  const terminals = await fetch("/api/terminals").then((r) => r.json());
  terminals.forEach((t) => terminalNames.set(t.id, t.name));
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

/** Draw all alternatives, highlighting the selected one. */
function drawAlternatives(alternatives, scenario) {
  if (!map || !alternatives) return;
  clearRoute();
  const bounds = new maplibregl.LngLatBounds();
  
  const orderedIndices = [];
  for (let i = 0; i < scenario.totals.length; i++) {
    if (i !== selectedIndex) orderedIndices.push(i);
  }
  if (selectedIndex < scenario.totals.length) orderedIndices.push(selectedIndex);
  
  orderedIndices.forEach((idx) => {
    const total = scenario.totals[idx];
    const alternative = alternatives.find((a) => a.label === total.label);
    if (!alternative) return;
    
    const priced = legsUnder(scenario, alternative, total);
    const isSelected = idx === selectedIndex;

    priced.forEach((leg, index) => {
      let coordinates = leg.geometry;
      const schematic = !coordinates.length || leg.track_is_indicative;
      if (schematic) {
        const from = terminalCoordinate(leg.from_name);
        const to = terminalCoordinate(leg.to_name);
        if (!from || !to) return;
        coordinates = [from, to];
      }
      coordinates.forEach((point) => bounds.extend(point));

      const id = `leg-${idx}-${index}`;
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
            schematic: schematic
              ? (leg.track_is_indicative ? "göstergesel iz" : "şematik çizim")
              : "",
            gain: leg.elevation_gain_m || 0,
            loss: leg.elevation_loss_m || 0,
            terrain_factor: leg.terrain_factor || 1.0
          },
        },
      });
      const isSteep = isSelected && leg.terrain_factor > 1.05;

      map.addLayer({
        id, type: "line", source: id,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": isSelected ? (isSteep ? "#ef4444" : modeColour(leg.mode)) : "#9ca3af",
          "line-width": isSelected ? (isSteep ? 4 : 3) : 2,
          "line-dasharray": schematic ? [2, 1.6] : [1],
          "line-opacity": isSelected ? 1 : 0.6
        },
      });
      map.addLayer({
        id: `${id}-hit`, type: "line", source: id,
        paint: { "line-color": "#000", "line-opacity": 0, "line-width": isSelected ? 18 : 8 },
      });
      drawnLayers.push(id);
    });
  });

  attachLegHover();
  const chosenAlternative = alternatives.find(a => a.label === scenario.totals[selectedIndex].label);
  highlightCrossedZones((chosenAlternative?.risk?.zones ?? []).map((z) => z.id));
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
        + (p.gain > 0 ? `⛰️ Tırmanış: +${Math.round(p.gain)}m · İniş: -${Math.round(p.loss)}m<br>` : '')
        + `<span style="color:#6e7783">faktör ${p.factor}${p.terrain_factor && p.terrain_factor !== 1 ? ` (Topografya x${p.terrain_factor.toFixed(2)})` : ''}${p.schematic ? " · şematik" : ""}</span>`,
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
    ["saving_eur", "Maliyet Farkı", "€"],
    ["range", "Belirsizlik aralığı", ""],
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
  const costDelta = baseline && chosen.total_cost_eur && baseline.total_cost_eur ? chosen.total_cost_eur - baseline.total_cost_eur : null;

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
    if (!element) return;
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
    
  set("saving_eur", costDelta === null ? 0 : costDelta,
    (v) => (costDelta === null ? "—" : (v > 0 ? "+" : "") + nf.format(v)),
    costDelta === null ? "temel yok" : "karayoluna kıyasla (Yakıt + ETS)",
    costDelta === null || costDelta === 0 ? "" : costDelta < 0 ? "good" : "bad");
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
    scenario.totals.map((total, index) => {
      const tagsHtml = (total.tradeoff_tags || []).map(t => {
        if (t === 'fastest') return '<span class="tradeoff-tag fastest">⚡ En Hızlı</span>';
        if (t === 'cheapest') return '<span class="tradeoff-tag cheapest">💰 En Ucuz</span>';
        if (t === 'greenest') return '<span class="tradeoff-tag greenest">🌿 En Çevreci</span>';
        return '';
      }).join(" ");
      return `<button type="button" class="bar-row" data-index="${index}">
      <span class="bar-name">${total.label}${
        total.is_all_road ? '<span class="baseline-tag">temel</span>' : ""
      } <div class="tags">${tagsHtml}</div></span>
      <span class="bar-track"><span class="bar-stack">${
        MODE_ORDER.map((m) => `<span class="${m}" data-mode="${m}" style="flex:0 0 0%"></span>`).join("")
      }</span></span>
      <span class="bar-value">
        <span class="val-co2"><span data-num></span></span>
        <span class="val-cost">${total.total_cost_eur ? nf.format(total.total_cost_eur) + ' €' : ''}</span>
        <span class="val-time">${total.total_hours ? total.total_hours + ' sa' : ''}</span>
      </span>
    </button>`;
    }).join("")}</div>
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
    const tagsHtml = (total.tradeoff_tags || []).map(t => {
      if (t === 'fastest') return '<span class="tradeoff-tag fastest">⚡ En Hızlı</span>';
      if (t === 'cheapest') return '<span class="tradeoff-tag cheapest">💰 En Ucuz</span>';
      if (t === 'greenest') return '<span class="tradeoff-tag greenest">🌿 En Çevreci</span>';
      return '';
    }).join(" ");
    const tagsEl = element.querySelector(".tags");
    if (tagsEl) tagsEl.innerHTML = tagsHtml;
    const costEl = element.querySelector(".val-cost");
    if (costEl) costEl.textContent = total.total_cost_eur ? nf.format(total.total_cost_eur) + ' €' : '';
    const timeEl = element.querySelector(".val-time");
    if (timeEl) timeEl.textContent = total.total_hours ? total.total_hours + ' sa' : '';
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
  const chosen = scenario.totals[selectedIndex] ?? scenario.totals[0];
  const alternative = payload.alternatives.find((a) => a.label === chosen.label);
  // Route notes are computed per leg and were reaching the browser unread.
  const messages = [
    ...(alternative?.notes ?? []),
    ...scenario.warnings,
    // A derived reefer figure has to say so wherever it is shown, not only in its panel.
    ...(chosen.reefer?.is_verified === false ? chosen.reefer.warnings : []),
  ];
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

function renderCEOWidget(scenario) {
  const ctaSlot = $("ceo-cta-slot");
  if (!ctaSlot) return;

  const baseline = scenario.totals.find((t) => t.is_all_road);
  const best = scenario.totals.reduce((prev, curr) => 
    (curr.total_cost_eur < prev.total_cost_eur ? curr : prev), scenario.totals[0]);

  if (!baseline || !best || baseline.label === best.label || baseline.total_cost_eur <= best.total_cost_eur) {
    ctaSlot.hidden = true;
    return;
  }

  const costSaving = baseline.total_cost_eur - best.total_cost_eur;
  const etsSaving = (baseline.ets?.cost_eur || 0) - (best.ets?.cost_eur || 0);
  const co2Saving = baseline.total_co2_kg - best.total_co2_kg;
  const co2Pct = (co2Saving / baseline.total_co2_kg) * 100;

  ctaSlot.hidden = false;
  ctaSlot.innerHTML = `
    <div style="background: var(--surface-float); border: 2px solid var(--sea); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: space-between; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
      <div>
        <h3 style="margin: 0 0 0.5rem 0; color: var(--sea); font-size: 1.2rem;">💡 Yönetici Özeti: Optimizasyon Fırsatı</h3>
        <p style="margin: 0; font-size: 1.05rem;">
          Sadece Karayolu yerine <strong>${best.label}</strong> rotasını tercih ederek sefer başına toplam <strong>${nf.format(costSaving)} €</strong> tasarruf edebilirsiniz.
          <br>
          <small style="color: var(--text-muted);">Bu tasarrufun <strong>${nf.format(etsSaving)} €</strong>'su düşen EU ETS karbon vergisinden kaynaklanmaktadır (Karbon ayak iziniz %${nf1.format(co2Pct)} azalır).</small>
        </p>
      </div>
      <button type="button" class="primary" style="background: var(--sea); color: white; padding: 0.75rem 1.5rem; font-weight: 600; border-radius: 4px; white-space: nowrap; margin-left: 1rem;" onclick="document.querySelector('.kpi-row').scrollIntoView({behavior: 'smooth'})">Detayları İncele</button>
    </div>
  `;
}

/** Update values in place so the bars and dots animate between scenarios. */
function applyScenario() {
  const scenario = currentScenario();
  if (!scenario) return;
  if (selectedIndex >= scenario.totals.length) selectedIndex = 0;

  renderScenarioBar();
  renderCEOWidget(scenario);
  updateKpis(scenario);
  renderNotices(scenario);
  updateComparison(scenario);
  updateSensitivity(scenario);
  renderTimeline(scenario);
  renderRiskCost(scenario);
  renderLegDetail(scenario);

  const chosen = scenario.totals[selectedIndex];
  const shown = payload.alternatives.find((a) => a.label === chosen.label);
  drawAlternatives(payload.alternatives, scenario);
  // The player belongs to one alternative; switching scenario or route reloads it.
  resetPlayer(shown);
  loadConformance(scenario);
}

/* ── requests ────────────────────────────────────────────────────────── */

async function loadFactorSets() {
  const all = await fetch("/api/factor-sets").then((r) => r.json());
  // A set without a factor for every mode can only ever answer with an error, so it is
  // never offered as a scenario.
  factorSets = all.filter((set) => Object.keys(set.sea_factor_by_scope).length > 0);
  fillFuelSelect();
}

/** Offer the road fuels the engine actually has, read from the factor file through the
 *  API rather than hard-coded here. A list written into the front end drifts the moment
 *  a row is added, and the names are not guessable: asking for "diesel" or "electric"
 *  is an error, because the rows are diesel_b5 and electric_tr.
 *
 *  Derived factors say so in the option itself. A fuel whose number was scaled off the
 *  diesel row should not look like the published one beside it. */
function fillFuelSelect() {
  const select = $("road-fuel");
  if (!select) return;
  const primary = factorSets.find((set) => set.name === "glec") ?? factorSets[0];
  const fuels = primary?.road_fuels ?? [];
  if (!fuels.length) { select.parentElement.hidden = true; return; }

  const fallback = fuels.find((f) => f.is_default);
  select.innerHTML = [
    `<option value="">Varsayılan${fallback ? ` — ${fallback.label}` : ""}</option>`,
    ...fuels
      .filter((f) => !f.is_default)
      .map((f) => `<option value="${f.fuel_type}">${f.label}${
        f.is_verified ? "" : " · türetme"}</option>`),
  ].join("");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const origin = parsePoint(data.get("origin"));
  const destination = parsePoint(data.get("destination"));
  if (!validatePoints()) {
    // The inline message names which endpoint is wrong; this one only says why nothing
    // happened when the button was pressed.
    statusLine.textContent = "Koordinatlar düzeltilmeden hesaplanamaz.";
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
    is_reefer: data.get("is_reefer") === "on",
    scenarios,
  };
  // Empty means "whatever the set calls default", which is what the engine does too.
  if (data.get("road_fuel_type")) body.road_fuel_type = data.get("road_fuel_type");
  if (data.get("load_factor")) body.load_factor = Number(data.get("load_factor"));
  if (data.get("empty_return_share")) body.empty_return_share = Number(data.get("empty_return_share"));

  submitButton.disabled = true;
  $("download-pdf").hidden = true;
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
    // There are numbers now, so the map stops being the only thing worth showing.
    dashboard.classList.remove("map-only");
    $("map-pick-hint").hidden = true;
    if (map) map.resize();
    buildDashboard();
    applyScenario();
    $("download-pdf").hidden = false;
  } catch (error) {
    emptyState.innerHTML = `<div class="error">Sunucuya ulaşılamadı: ${error.message}</div>`;
    emptyState.hidden = false; dashboard.hidden = true;
  } finally {
    submitButton.disabled = false;
    statusLine.textContent = "";
  }
});

$("download-pdf").addEventListener("click", () => {
  const element = document.querySelector(".layout");
  const opt = {
    margin:       10,
    filename:     'freightprint_karbon_sertifikasi.pdf',
    image:        { type: 'jpeg', quality: 0.98 },
    html2canvas:  { scale: 2, useCORS: true },
    jsPDF:        { unit: 'mm', format: 'a4', orientation: 'landscape' }
  };
  
  // hide sidebar in the PDF for a cleaner report? No, layout is responsive, but let's hide the form side temporarily
  const sidebar = document.querySelector(".sidebar");
  sidebar.style.display = 'none';
  
  html2pdf().set(opt).from(element).save().then(() => {
    sidebar.style.display = 'flex';
  });
});

const reportForm = $("report-form");
const reportStatus = $("report-status");
const reportSubmit = $("report-submit");

/** Upload as a background job and poll it.
 *
 * A cold shipment costs about six seconds, so a few hundred rows outlive any request
 * timeout. The upload returns a handle straight away and the progress comes from the
 * job rather than from a spinner that knows nothing.
 */
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
  reportStatus.textContent = "Dosya gönderiliyor…";
  try {
    const started = await fetch("/api/report/jobs", { method: "POST", body });
    const job = await started.json();
    if (!started.ok) {
      reportStatus.textContent = job.detail ?? `İstek başarısız (${started.status}).`;
      return;
    }
    const finished = await pollReportJob(job.id);
    if (finished.status === "failed") {
      reportStatus.textContent = `Rapor üretilemedi: ${finished.error}`;
      return;
    }
    await downloadReport(finished);
    reportStatus.textContent = `Rapor indirildi — ${finished.total} sevkiyat.`;
  } catch (error) {
    reportStatus.textContent = `Sunucuya ulaşılamadı: ${error.message}`;
  } finally {
    reportSubmit.disabled = false;
  }
});

});

$("portfolio-submit").addEventListener("click", async (event) => {
  event.preventDefault();
  const body = new FormData(reportForm);
  const scenario = currentScenario();
  body.set("scope", scenario?.scope ?? "TTW");
  body.set("factor_set", scenario?.factor_set ?? "glec");

  const btn = $("portfolio-submit");
  btn.disabled = true;
  reportStatus.textContent = "Portföy analiz ediliyor…";
  
  try {
    const response = await fetch("/api/portfolio", { method: "POST", body });
    const portfolio = await response.json();
    if (!response.ok) {
      reportStatus.textContent = portfolio.detail ?? `İstek başarısız (${response.status}).`;
      return;
    }
    reportStatus.textContent = `${portfolio.lanes.length} hat analiz edildi.`;
    
    // Switch to dashboard view
    $("dashboard").hidden = false;
    $("empty-state").hidden = true;
    
    renderPortfolio(portfolio);
    renderPortfolioOnMap(portfolio);
  } catch (error) {
    reportStatus.textContent = `Sunucuya ulaşılamadı: ${error.message}`;
  } finally {
    btn.disabled = false;
  }
});

function renderPortfolioOnMap(portfolio) {
  if (!map) return;
  
  if (map.getSource('portfolio-lines')) {
    map.removeLayer('portfolio-lines');
    map.removeSource('portfolio-lines');
  }
  
  const features = portfolio.lanes.map(lane => {
    return {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [lane.origin_lon, lane.origin_lat],
          [lane.destination_lon, lane.destination_lat]
        ]
      },
      properties: {
        risk: lane.empty_miles_risk,
        imbalance: lane.imbalance_ratio,
        label: lane.key,
        shipments: lane.shipments
      }
    };
  });
  
  map.addSource('portfolio-lines', {
    type: 'geojson',
    data: { type: "FeatureCollection", features }
  });
  
  map.addLayer({
    id: 'portfolio-lines',
    type: 'line',
    source: 'portfolio-lines',
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: {
      'line-color': ['case', ['==', ['get', 'risk'], true], '#e66767', '#3fb782'],
      'line-width': ['+', 2, ['/', ['get', 'shipments'], 10]],
      'line-opacity': 0.8
    }
  });
  
  const bounds = new maplibregl.LngLatBounds();
  features.forEach(f => {
    bounds.extend(f.geometry.coordinates[0]);
    bounds.extend(f.geometry.coordinates[1]);
  });
  if (!bounds.isEmpty()) {
    map.fitBounds(bounds, { padding: 50 });
  }
}

async function pollReportJob(jobId) {
  // Poll gently: the run is minutes long, so a tighter loop only adds requests.
  for (;;) {
    const job = await fetch(`/api/report/jobs/${jobId}`).then((r) => r.json());
    if (job.status === "done" || job.status === "failed") return job;
    reportStatus.textContent =
      `Hesaplanıyor — ${job.done}/${job.total} sevkiyat (%${Math.round(job.progress * 100)})`;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function downloadReport(job) {
  const blob = await fetch(`/api/report/jobs/${job.id}/file`).then((r) => r.blob());
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = job.filename;
  link.click();
  URL.revokeObjectURL(url);
}

/* ── place search ────────────────────────────────────────────────────── */

const placeQuery = $("place-query");
const placeResults = $("place-results");
let placeTimer;

/** Search by name, then make the user pick which place was meant.
 *
 *  Resolving a name to one point silently is how a shipment lands in the wrong province.
 *  One name in the validation set matches several villages, and two of the readings
 *  differ by seven points of route distance — both perfectly ordinary on screen. The
 *  choice is the user's to make.
 */
const placeMessage = (text) => {
  const note = document.createElement("p");
  note.className = "place-empty";
  note.textContent = text;
  placeResults.replaceChildren(note);
};

/** One candidate, built as nodes rather than interpolated into innerHTML.
 *
 *  Two reasons. A display name comes from OpenStreetMap, so it is a stranger's text
 *  and does not belong in innerHTML. And the name needs splitting anyway: Nominatim
 *  answers with five levels of address — town, province, region, postal code, country —
 *  which as one line wraps to three rows in a 320px sidebar and buries the word the
 *  user is actually looking for.
 *
 *  The kind — city, town, village, port — is shown because it is the whole reason this
 *  control offers a list at all. Two readings of one name in the validation set differ
 *  by seven points of route distance, and "village" against "city" is what tells them
 *  apart; without it the user picks the first row and the choice is decorative.
 */
function placeHit(hit, index, choose) {
  const [head, ...rest] = hit.name.split(",");

  const row = document.createElement("div");
  row.className = "place-hit";
  row.dataset.index = String(index);
  row.setAttribute("role", "option");

  const title = document.createElement("span");
  title.className = "place-title";
  const name = document.createElement("strong");
  name.className = "place-name";
  name.textContent = head.trim();
  title.append(name);
  if (hit.kind) {
    const kind = document.createElement("span");
    kind.className = "place-kind";
    kind.textContent = hit.kind.replace(/_/g, " ");
    title.append(kind);
  }

  const where = document.createElement("span");
  where.className = "place-where";
  where.textContent = rest.join(",").trim();

  const actions = document.createElement("span");
  actions.className = "place-actions";
  for (const [target, label] of [["origin", "kalkış"], ["destination", "varış"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.textContent = label;
    button.addEventListener("click", () => choose(hit, target === "origin"));
    actions.append(button);
  }

  row.append(title, where, actions);
  return row;
}

async function searchPlaces(query) {
  if (query.trim().length < 2) { placeResults.hidden = true; return; }
  placeResults.hidden = false;
  placeMessage("Aranıyor…");
  try {
    const response = await fetch(`/api/places?q=${encodeURIComponent(query)}&limit=5`);
    if (response.status === 503) {
      placeMessage("Arama servisi şu an meşgul, birkaç saniye sonra tekrar deneyin.");
      return;
    }
    const found = response.ok ? await response.json() : [];
    if (!found.length) {
      placeMessage("Sonuç yok.");
      return;
    }

    const choose = (hit, isOrigin) => {
      (isOrigin ? originInput : destinationInput).value = `${hit.lon}, ${hit.lat}`;
      // The first part of the display name is the place itself.
      form.elements[isOrigin ? "origin_name" : "destination_name"].value =
        hit.name.split(",")[0].trim();
      placeEndpointMarkers();
      validatePoints();
      if (map) map.flyTo({ center: [hit.lon, hit.lat], zoom: 6, duration: 800 });
      placeResults.hidden = true;
      placeQuery.value = "";
    };

    placeResults.replaceChildren(
      ...found.map((hit, index) => placeHit(hit, index, choose))
    );
  } catch (error) {
    placeMessage(`Arama başarısız: ${error.message}`);
  }
}

placeQuery.addEventListener("input", (event) => {
  // Nominatim asks for at most one request a second; debounce rather than type-ahead.
  clearTimeout(placeTimer);
  placeTimer = setTimeout(() => searchPlaces(event.target.value), 500);
});
placeQuery.addEventListener("blur", () => setTimeout(() => { placeResults.hidden = true; }, 200));

$("pick-origin").addEventListener("click", () => setPicking("origin"));
$("pick-destination").addEventListener("click", () => setPicking("destination"));
$("swap-endpoints").addEventListener("click", swapEndpoints);
setUpTerminalPicker("origin");
setUpTerminalPicker("destination");

// Armed picking is a mode, and a mode the keyboard cannot leave is a trap.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && picking) setPicking(null);
});
$("player-play").addEventListener("click", togglePlay);
$("player-speed").addEventListener("change", (event) => {
  playSpeed = Number(event.target.value);
});
$("player-scrub").addEventListener("input", (event) => {
  if (!playback) return;
  stopPlaying();   // scrubbing takes over from playing rather than fighting it
  playHead = (Number(event.target.value) / 1000) * playback.total_hours;
  drawPlayHead();
});

$("catchment-toggle").addEventListener("click", () => {
  // The map may have failed to load; the rest of the dashboard still works.
  if (map) toggleCatchment();
});
[originInput, destinationInput].forEach((input) => {
  input.addEventListener("change", placeEndpointMarkers);
  // On every keystroke, not only on change: a coordinate that cannot be used should
  // say so while it is being typed rather than at submit.
  input.addEventListener("input", validatePoints);
});
["load_factor", "empty_return_share", "road_fuel_type"].forEach((name) =>
  form.elements[name].addEventListener("input", markAdvancedInUse));
validatePoints();
markAdvancedInUse();

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

/* ── timeline ────────────────────────────────────────────────────────── */

const STEP_LABELS = { transit: "yolda", dwell: "aktarma", wait: "kalkış beklemesi" };

/** Refrigeration, shown inside the timeline panel because that is what it is billed
 *  against. The hours the cargo stands still are called out separately: they are the
 *  part a per-kilometre model scores as zero, and on a multimodal route they are a
 *  third of the refrigeration bill. */
function renderReefer(total) {
  const reefer = total.reefer;
  if (!reefer) return "";

  const share = reefer.co2_kg ? (reefer.stationary_co2_kg / reefer.co2_kg) * 100 : 0;
  const split = Object.entries(reefer.co2_by_kind)
    .map(([kind, kg]) => `${STEP_LABELS[kind]} ${nf.format(kg)} kg`)
    .join(" · ");

  return `<div class="reefer-block">
    <p class="reefer-head">Soğutma <strong>${nf.format(reefer.co2_kg)} kg CO2e</strong>
      <span class="card-note">${nf1.format(reefer.hours)} saat boyunca</span></p>
    <p class="hint">${split}</p>
    <p class="hint">Yük hareketsizken yanan: <strong>${nf.format(reefer.stationary_co2_kg)} kg</strong>
      (${nf.format(share)}%) — km bazlı bir hesap bunu sıfır sayardı.</p>
    <p class="hint">Taşıma ile toplanmadan ayrı gösteriliyor:
      taşıma rakamı yayınlanmış GLEC tablolarından, bu türetme.</p>
  </div>`;
}

/** Lay the journey out in time. Most of a multimodal disadvantage is not distance:
 *  it is handling and waiting for the next departure, which a distance figure hides. */
function renderTimeline(scenario) {
  const total = scenario.totals[selectedIndex] ?? scenario.totals[0];
  const alternative = payload.alternatives.find((a) => a.label === total.label);
  const timeline = alternative?.timeline;
  if (!timeline) { $("timeline").innerHTML = ""; return; }

  // Every alternative shares one scale, so the bars are comparable at a glance.
  const longest = Math.max(
    ...payload.alternatives.map((a) => a.timeline?.total_hours ?? 0), 1,
  );

  const rows = scenario.totals.map((row, index) => {
    const alt = payload.alternatives.find((a) => a.label === row.label);
    const line = alt?.timeline;
    if (!line) return "";
    const blocks = line.steps.map((step) => `<span class="step ${step.kind}"
        style="flex:0 0 ${(step.hours / line.total_hours) * 100}%"
        title="${STEP_LABELS[step.kind]}: ${step.label} · ${nf1.format(step.hours)} sa${
          step.is_estimated ? " (tahmin)" : ""}"></span>`).join("");
    return `<button type="button" class="time-row" data-index="${index}"
        aria-current="${index === selectedIndex}"
        aria-label="${row.label}: ${nf1.format(line.total_days)} gün">
        <span class="bar-name">${row.label}${
          row.is_all_road ? '<span class="baseline-tag">temel</span>' : ""}</span>
        <span class="time-track"><span class="time-stack"
          style="width:${(line.total_hours / longest) * 100}%">${blocks}</span></span>
        <span class="bar-value">${nf1.format(line.total_days)} gün</span>
      </button>`;
  }).join("");

  const split = Object.entries(timeline.hours_by_kind)
    .map(([kind, hours]) => `${STEP_LABELS[kind]} ${nf.format(hours)} sa`)
    .join(" · ");

  $("timeline").innerHTML = `<div class="bars">${rows}</div>
    <p class="hint">Seçili rota: ${split}</p>
    <div class="legend-row">
      ${Object.entries(STEP_LABELS).map(([kind, label]) =>
        `<span class="key"><span class="swatch step-${kind}"></span>${label}</span>`).join("")}
    </div>
    ${renderReefer(total)}
    ${timeline.notes.map((n) => `<p class="hint">${n}</p>`).join("")}`;

  $("timeline-note").textContent =
    `${nf1.format(timeline.total_days)} gün · ${timeline.steps.length} adım`;

  $("timeline").querySelectorAll(".time-row").forEach((element) =>
    element.addEventListener("click", () => {
      selectedIndex = Number(element.dataset.index);
      applyScenario();
    }));
}

/* ── risk & cost ─────────────────────────────────────────────────────── */

const eur = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });

/** Risk belongs to the alternative and the allowance bill to the scenario, so this
 *  panel reads from both. */
function renderRiskCost(scenario) {
  const total = scenario.totals[selectedIndex] ?? scenario.totals[0];
  const alternative = payload.alternatives.find((a) => a.label === total.label);
  const risk = alternative?.risk;
  const ets = total.ets;

  const zoneRows = (risk?.zones ?? []).map((z) => `<tr>
      <td>${z.name}</td><td class="num">${nf.format(z.distance_km)}</td>
      <td class="src">${z.source}</td>
    </tr>`).join("");

  const riskBlock = !risk
    ? ""
    : risk.is_exposed
      ? `<div class="risk-flag bad">
           <strong>${nf.format(risk.distance_in_zones_km)} km</strong> ilan edilmiş
           savaş riski bölgesi içinde
         </div>
         <table><thead><tr><th>Bölge</th><th>km</th><th>kaynak</th></tr></thead>
           <tbody>${zoneRows}</tbody></table>`
      : `<div class="risk-flag good">İlan edilmiş savaş riski bölgesiyle kesişim yok</div>`;

  const untracked = risk?.untracked_sea_km
    ? `<p class="hint">${nf.format(risk.untracked_sea_km)} km deniz bacağının izi yok —
       bu kısım <em>kontrol edilmedi</em>, temiz olduğu anlamına gelmez.</p>`
    : "";

  const passages = risk?.passages?.length
    ? `<p class="hint">Geçilen boğazlar: ${risk.passages.join(", ")}</p>`
    : "";

  const etsBlock = !ets || !ets.legs.length
    ? `<p class="hint">Bu rotanın deniz bacağı yok; ETS yükümlülüğü doğmuyor.
         Karayolu ETS2 kapsamında, ayrı bir şema.</p>`
    : `<table>
        <thead><tr><th>Deniz bacağı</th><th>kg CO2</th><th>kapsam</th><th>€</th></tr></thead>
        <tbody>${ets.legs.map((l) => `<tr>
          <td>${l.from_name} → ${l.to_name}</td>
          <td class="num">${nf.format(l.co2_kg)}</td>
          <td class="num">%${Math.round(l.coverage_share * 100)}</td>
          <td class="num">${eur.format(l.cost_eur)}</td>
        </tr>`).join("")}</tbody>
        <tfoot><tr>
          <td>Toplam · ${eur.format(ets.carbon_price_eur)} €/ton · ${ets.year}</td>
          <td class="num">${nf.format(ets.covered_tonnes * 1000)}</td>
          <td></td>
          <td class="num">${eur.format(ets.cost_eur)}</td>
        </tr></tfoot>
      </table>
      ${ets.notes.map((n) => `<p class="hint">${n}</p>`).join("")}`;

  // Carbon priced by a road authority rather than by the allowance market. Germany
  // charges 200 EUR a tonne in its truck toll — two and a half times the shipping
  // allowance price — so the route that loses on carbon can still win on the invoice,
  // and a carrier needs both numbers side by side.
  const toll = total.co2_toll;
  const tollBlock = !toll
    ? `<p class="hint">Bu rotanın karayolu bacağı yok.</p>`
    : `<table>
        <thead><tr><th>Ülke</th><th>km</th><th>kg CO2</th><th>€</th></tr></thead>
        <tbody>${toll.countries.map((c) => `<tr class="${c.priced ? "" : "muted-row"}">
          <td>${c.country}${c.priced ? "" : `<br><span class="card-note">${c.reason}</span>`}</td>
          <td class="num">${nf.format(c.distance_km)}</td>
          <td class="num">${nf.format(c.co2_kg)}</td>
          <td class="num">${c.priced ? eur.format(c.cost_eur) : "—"}</td>
        </tr>`).join("")}</tbody>
        <tfoot><tr>
          <td>Ücretlendirilen ${nf.format(toll.priced_co2_kg)} kg</td>
          <td></td><td></td>
          <td class="num">${eur.format(toll.total_eur)}</td>
        </tr></tfoot>
      </table>
      ${toll.notes.map((n) => `<p class="hint">${n}</p>`).join("")}`;

  $("risk-cost").innerHTML = `<div class="risk-grid">
      <section><h3 class="sub-title">Güzergâh riski</h3>${riskBlock}${passages}${untracked}</section>
      <section><h3 class="sub-title">ETS yükümlülüğü</h3>${etsBlock}</section>
      <section class="span-2"><h3 class="sub-title">CO2 geçiş ücreti — ülkeye göre</h3>${tollBlock}</section>
    </div>`;

  $("risk-note").textContent = risk?.is_exposed
    ? "seçilen rota ilan edilmiş bölgeden geçiyor"
    : "seçilen rota · ETS senaryoya bağlı";
}

/* ── diversion scenario ──────────────────────────────────────────────── */

const compareForm = $("compare-form");
const compareStatus = $("compare-status");
const compareSubmit = $("compare-submit");

/** Draw both sailings together so the diversion can be seen, not just read. */
function drawCompare(data) {
  if (!map) return;
  clearRoute();
  const bounds = new maplibregl.LngLatBounds();

  [["compare-direct", data.direct, "#b3261e"], ["compare-diverted", data.diverted, "#0f7a45"]]
    .forEach(([id, sailing, colour]) => {
      if (!sailing.geometry?.length) return;
      sailing.geometry.forEach((point) => bounds.extend(point));
      map.addSource(id, {
        type: "geojson",
        data: {
          type: "Feature",
          geometry: { type: "LineString", coordinates: sailing.geometry },
          properties: {
            label: sailing.label,
            km: nf.format(sailing.distance_km),
            days: sailing.duration_h ? nf.format(sailing.duration_h / 24) : "—",
            zone: nf.format(sailing.risk.distance_in_zones_km),
          },
        },
      });
      map.addLayer({
        id, type: "line", source: id,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": colour, "line-width": 3 },
      });
      map.addLayer({
        id: `${id}-hit`, type: "line", source: id,
        paint: { "line-color": "#000", "line-opacity": 0, "line-width": 18 },
      });
      drawnLayers.push(id);
    });

  attachCompareHover();
  highlightCrossedZones(
    (data.direct.risk.zones ?? []).map((z) => z.id),
  );
  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60, duration: 800 });
}

function attachCompareHover() {
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
  drawnLayers.forEach((id) => {
    map.on("mousemove", `${id}-hit`, (event) => {
      map.getCanvas().style.cursor = "pointer";
      const p = event.features[0].properties;
      popup.setLngLat(event.lngLat).setHTML(
        `<strong>${p.label}</strong>${p.km} km · ${p.days} gün<br>`
        + `<span style="color:#6e7783">riskli bölgede ${p.zone} km</span>`,
      ).addTo(map);
    });
    map.on("mouseleave", `${id}-hit`, () => {
      map.getCanvas().style.cursor = "";
      popup.remove();
    });
  });
}

function renderCompare(data) {
  const row = (s) => `<tr>
      <td>${s.label}</td>
      <td class="num">${nf.format(s.distance_km)}</td>
      <td class="num">${s.duration_h ? nf.format(s.duration_h / 24) : "—"}</td>
      <td class="num">${nf.format(s.co2_kg)}</td>
      <td class="num">${eur.format(s.ets_eur)}</td>
      <td class="num ${s.risk.is_exposed ? "exposed" : ""}">${
        nf.format(s.risk.distance_in_zones_km)}</td>
    </tr>`;

  const impossible = data.extra_distance_km === null;
  const verdict = impossible
    ? `<p class="verdict bad">Bu sefer seçilen geçitlerden kaçınamıyor —
         ${data.diverted.unreachable}</p>`
    : `<p class="verdict ${data.avoided_zone_km > 0 ? "good" : "bad"}">
         Sapma <strong>${nf.format(data.avoided_zone_km)} km</strong> riskli bölgeden
         çıkarıyor; bedeli <strong>+${nf.format(data.extra_distance_km)} km</strong>,
         <strong>+${nf.format(data.extra_duration_h / 24)} gün</strong> ve
         <strong>+${nf.format(data.extra_co2_kg)} kg CO2</strong>.
       </p>
       <table class="cost-split">
         <tbody>
           <tr><td>Ek ETS yükümlülüğü</td><td class="num">${eur.format(data.extra_ets_eur)} €</td></tr>
           <tr><td>Armatörün ek ücreti <small>girdiğiniz</small></td>
               <td class="num">${eur.format(data.surcharge_eur)} €</td></tr>
           <tr class="total"><td>Toplam ek maliyet</td>
               <td class="num">${eur.format(data.total_extra_eur)} €</td></tr>
         </tbody>
       </table>
       <p class="hint">Sistem ek ücreti hesaplamaz — prim tekne değeri üzerinden
         pazarlıkla belirlenir ve yayımlanmaz. Yaptığı, o ücretin neyin karşılığı
         olduğunu ölçmek.</p>`;

  $("risk-cost").innerHTML = `<div class="card-head">
      <h3 class="sub-title">Sapma senaryosu — kaçınılan: ${data.avoided.join(", ")}</h3>
    </div>
    <table>
      <thead><tr><th>Sefer</th><th>km</th><th>gün</th><th>kg CO2</th><th>ETS €</th>
        <th>riskli km</th></tr></thead>
      <tbody>${row(data.direct)}${impossible ? "" : row(data.diverted)}</tbody>
    </table>
    ${verdict}`;
  $("risk-note").textContent = `${data.factor_set} · ${data.scope} · ${data.tonnage} ton`;
  $("map-legend").innerHTML =
    '<span class="key"><span class="swatch" style="background:#b3261e"></span>doğrudan</span>'
    + '<span class="key"><span class="swatch" style="background:#0f7a45"></span>sapma</span>'
    + '<span class="key"><span class="swatch" style="background:#8b93a0;opacity:.5"></span>'
    + "ilan edilmiş bölge</span>";
  drawCompare(data);
  $("risk-cost").scrollIntoView({ behavior: "smooth", block: "center" });
}

compareForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(compareForm);
  const origin = parsePoint(data.get("origin"));
  const destination = parsePoint(data.get("destination"));
  if (!origin || !destination) {
    compareStatus.textContent = "Limanlar 'boylam, enlem' biçiminde olmalı.";
    return;
  }
  if (!payload) {
    compareStatus.textContent = "Önce bir sevkiyat hesaplayın.";
    return;
  }

  const scenario = currentScenario();
  const body = {
    origin, destination,
    origin_name: data.get("origin_name") || "yükleme",
    destination_name: data.get("destination_name") || "boşaltma",
    // ETS coverage depends on whether each end is in the EEA, and a coordinate does not
    // say which country it is in. Left blank, the port counts as outside.
    origin_country: (data.get("origin_country") || "").trim().toUpperCase() || null,
    destination_country: (data.get("destination_country") || "").trim().toUpperCase() || null,
    tonnage: Number(new FormData(form).get("tonnage")),
    factor_set: scenario?.factor_set ?? "glec",
    scope: scenario?.scope ?? "TTW",
    avoid: data.get("avoid").split(","),
    surcharge_eur: Number(data.get("surcharge_eur")) || 0,
  };

  compareSubmit.disabled = true;
  compareStatus.textContent = "İki sefer de rotalanıyor…";
  try {
    const response = await fetch("/api/compare", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) {
      compareStatus.textContent = result.detail ?? "İstek başarısız.";
      return;
    }
    renderCompare(result);
    compareStatus.textContent = "";
  } catch (error) {
    compareStatus.textContent = `Sunucuya ulaşılamadı: ${error.message}`;
  } finally {
    compareSubmit.disabled = false;
  }
});

/* ── journey player ──────────────────────────────────────────────────── */

/** Play the shipment along its route.
 *
 *  The map was showing the same picture whether a crossing took two days or ten. This
 *  walks the clock the server laid out: where the box is, what it has emitted by then,
 *  and — the part the totals hide — the hours it spends going nowhere. On this corridor
 *  a third of the multimodal disadvantage is handling and waiting, and the marker
 *  sitting still at Trieste says that better than "18 sa aktarma" does.
 *
 *  Carbon comes from the segment that produced it, never interpolated across the whole
 *  clock: a parked truck burns nothing, and a counter that kept climbing through a
 *  handling stop would be lying in a way nobody watching could catch.
 */
let playback = null;
let playHead = 0;          // hours
let playTimer = null;
let playSpeed = 6;         // hours of journey per second of wall clock

const PLAY_KIND_LABELS = { transit: "yolda", dwell: "aktarma", wait: "kalkış beklemesi" };

function resetPlayer(alternative) {
  stopPlaying();
  playback = alternative?.playback ?? null;
  playHead = 0;
  lastMovingMode = null;
  const panel = $("player");
  if (!panel) return;
  if (!playback || !playback.segments.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $("player-scrub").value = 0;
  renderPlayerTrack();
  drawPlayHead();
}

/** The segment bar under the controls: one block per segment, width by duration. */
function renderPlayerTrack() {
  $("player-track").innerHTML = playback.segments.map((s) => `
    <span class="player-block ${s.kind}"
          style="flex:0 0 ${(s.hours / playback.total_hours) * 100}%"
          title="${PLAY_KIND_LABELS[s.kind]}: ${s.label}"></span>`).join("");
}

/** Position and running totals at a given hour. */
function stateAt(hours) {
  let co2 = 0;
  let reefer = 0;
  let current = playback.segments[0];
  let point = current.geometry[0] ?? null;

  for (const segment of playback.segments) {
    const end = segment.start_h + segment.hours;
    if (hours >= end) {
      co2 += segment.co2_kg;
      reefer += segment.reefer_co2_kg;
      if (segment.geometry.length) point = segment.geometry[segment.geometry.length - 1];
      continue;
    }
    // Inside this segment: take the fraction of it that has elapsed.
    const done = segment.hours > 0 ? (hours - segment.start_h) / segment.hours : 1;
    co2 += segment.co2_kg * done;
    reefer += segment.reefer_co2_kg * done;
    current = segment;
    point = pointAlong(segment.geometry, done) ?? point;
    break;
  }
  return { co2, reefer, segment: current, point };
}

/** Interpolate along a track by distance, so the marker moves at a steady pace rather
 *  than jumping between however densely OSRM happened to sample that stretch. */
function pointAlong(geometry, fraction) {
  if (!geometry.length) return null;
  if (geometry.length === 1) return geometry[0];

  const spans = [];
  let total = 0;
  for (let i = 1; i < geometry.length; i += 1) {
    const [x1, y1] = geometry[i - 1];
    const [x2, y2] = geometry[i];
    const d = Math.hypot(x2 - x1, y2 - y1);
    spans.push(d);
    total += d;
  }
  if (total === 0) return geometry[0];

  let travelled = Math.max(0, Math.min(1, fraction)) * total;
  for (let i = 0; i < spans.length; i += 1) {
    if (travelled <= spans[i] || i === spans.length - 1) {
      const t = spans[i] === 0 ? 0 : travelled / spans[i];
      const [x1, y1] = geometry[i];
      const [x2, y2] = geometry[i + 1];
      return [x1 + (x2 - x1) * t, y1 + (y2 - y1) * t];
    }
    travelled -= spans[i];
  }
  return geometry[geometry.length - 1];
}

function drawPlayHead() {
  if (!playback) return;
  const { co2, reefer, segment, point } = stateAt(playHead);
  const days = Math.floor(playHead / 24);
  const hours = Math.round(playHead % 24);
  const moving = segment.kind === "transit";

  if (moving) lastMovingMode = segment.mode ?? lastMovingMode;
  const zone = zoneAt(point);
  $("player-readout").innerHTML = `
    <span class="play-clock">${days} gün ${hours} sa</span>
    <span class="play-co2">${nf.format(co2)} kg CO2</span>
    ${reefer > 0 ? `<span class="play-reefer">+${nf.format(reefer)} kg soğutma</span>` : ""}
    <span class="play-what ${segment.kind}">${
      moving ? `${MODE_LABELS[segment.mode] ?? segment.mode} · ${segment.label}`
             : `⏸ ${PLAY_KIND_LABELS[segment.kind]} — ${segment.label}`}</span>
    ${segment.track_is_indicative && moving
      ? '<span class="play-flag">iz şematik</span>' : ""}
    ${zone ? `<span class="play-risk">⚠ ${zone}</span>` : ""}`;

  if (map && point) {
    if (!playMarker) {
      const element = document.createElement("div");
      element.className = "play-marker";
      playMarker = new maplibregl.Marker({ element }).setLngLat(point).addTo(map);
    } else {
      playMarker.setLngLat(point);
    }
    const element = playMarker.getElement();
    element.classList.toggle("stopped", !moving);
    // The vehicle is the mode currently carrying the box. Parked, it keeps the mode it
    // arrived on rather than becoming a generic dot: it is a trailer sitting on a quay,
    // not nothing.
    const mode = (moving ? segment.mode : lastMovingMode) ?? "road";
    // Rewritten only when the vehicle actually changes. Rebuilding the SVG on every
    // frame restarts its animation thirty times a second, which reads as a twitch.
    if (element.dataset.mode !== mode) {
      element.dataset.mode = mode;
      element.innerHTML = `<span class="play-vehicle">${VEHICLE[mode] ?? VEHICLE.road}</span>`;
    }
    // Point the vehicle the way it is travelling. Heading west would otherwise turn it
    // upside down, which reads as a bug rather than as a bearing, so it is mirrored
    // instead and stays upright.
    const bearing = bearingAt(segment, playHead);
    const svg = element.querySelector("svg");
    if (svg) {
      svg.style.setProperty("--bearing", `${bearing}deg`);
      svg.style.setProperty("--flip", Math.abs(bearing) > 90 ? -1 : 1);
    }
  }
}

let playMarker = null;
let lastMovingMode = null;

/* Simple silhouettes rather than emoji: emoji render differently on every platform and
   several of them carry a colour the palette does not control. */
const VEHICLE = {
  road: `<svg viewBox="0 0 32 20" aria-hidden="true">
    <path d="M1 5h17v10H1z"/><path d="M18 8h6l4 4v3h-10z"/>
    <circle cx="7" cy="16" r="2.6"/><circle cx="23" cy="16" r="2.6"/></svg>`,
  sea: `<svg viewBox="0 0 32 20" aria-hidden="true">
    <path d="M3 12h26l-3 5H6z"/><path d="M8 6h13v6H8z"/><path d="M13 2h3v4h-3z"/></svg>`,
  rail: `<svg viewBox="0 0 32 20" aria-hidden="true">
    <path d="M4 4h16v11H4z"/><path d="M20 7h5l3 4v4h-8z"/>
    <circle cx="9" cy="17" r="2"/><circle cx="16" cy="17" r="2"/><circle cx="24" cy="17" r="2"/></svg>`,
};

/** Heading along the current segment, in degrees clockwise from east. */
function bearingAt(segment, hours) {
  const track = segment.geometry;
  if (!track || track.length < 2) return 0;
  const done = segment.hours > 0 ? (hours - segment.start_h) / segment.hours : 1;
  const index = Math.min(
    track.length - 2,
    Math.max(0, Math.floor(done * (track.length - 1))),
  );
  const [x1, y1] = track[index];
  const [x2, y2] = track[index + 1];
  return (Math.atan2(y1 - y2, x2 - x1) * 180) / Math.PI;
}

/** Which listed area the shipment is inside, if any. Point-in-rectangle is enough:
 *  the zones are digitised as boxes and the README says so. */
function zoneAt(point) {
  if (!point || !map || !map.getSource("risk-zones")) return null;
  const zones = map.getSource("risk-zones")._data;
  for (const feature of zones.features) {
    for (const ring of feature.geometry.coordinates) {
      const lons = ring.map((p) => p[0]);
      const lats = ring.map((p) => p[1]);
      if (point[0] >= Math.min(...lons) && point[0] <= Math.max(...lons)
          && point[1] >= Math.min(...lats) && point[1] <= Math.max(...lats)) {
        return feature.properties.name;
      }
    }
  }
  return null;
}

function stopPlaying() {
  if (playTimer) { clearInterval(playTimer); playTimer = null; }
  const button = $("player-play");
  if (button) button.textContent = "▶";
}

function togglePlay() {
  if (!playback) return;
  if (playTimer) { stopPlaying(); return; }
  if (playHead >= playback.total_hours) playHead = 0;
  $("player-play").textContent = "⏸";
  const tick = 1 / 30;   // seconds of wall clock per frame
  playTimer = setInterval(() => {
    playHead += playSpeed * tick;
    if (playHead >= playback.total_hours) {
      playHead = playback.total_hours;
      stopPlaying();
    }
    $("player-scrub").value = (playHead / playback.total_hours) * 1000;
    drawPlayHead();
  }, tick * 1000);
}

$("player-play")?.addEventListener("click", togglePlay);

$("player-scrub")?.addEventListener("input", (e) => {
  if (!playback) return;
  playHead = (e.target.value / 1000) * playback.total_hours;
  if (!playTimer) drawPlayHead();
});

$("player-speed")?.addEventListener("change", (e) => {
  playSpeed = Number(e.target.value);
});

/* ── lane portfolio ──────────────────────────────────────────────────── */

/** Read the uploaded shipment file as a portfolio of lanes.
 *
 *  The bulk report answers what a shipment emitted. This answers the question a carrier
 *  with thousands of movements actually asks: which lanes are worth changing, and what
 *  would changing them cost. Totals say where the mass is, intensity says where a lane
 *  is run badly, and the robustness column says whether the saving survives a change of
 *  accounting basis — which is the one thing that decides if it can be defended.
 */
$("portfolio-submit").addEventListener("click", async () => {
  const file = reportForm.querySelector('input[type="file"]');
  if (!file.files.length) {
    reportStatus.textContent = "Önce bir sevkiyat dosyası seçin.";
    return;
  }

  const body = new FormData();
  body.set("file", file.files[0]);
  const scenario = currentScenario();
  body.set("scope", scenario?.scope ?? "WTW");
  body.set("factor_set", scenario?.factor_set ?? "glec");

  const button = $("portfolio-submit");
  button.disabled = true;
  reportStatus.textContent = "Hatlar çıkarılıyor — her sevkiyat bir kez rotalanır…";
  try {
    const response = await fetch("/api/portfolio", { method: "POST", body });
    const data = await response.json();
    if (!response.ok) {
      reportStatus.textContent = data.detail ?? `İstek başarısız (${response.status}).`;
      return;
    }
    renderPortfolio(data);
    reportStatus.textContent = `${data.lanes.length} hat çıkarıldı.`;
  } catch (error) {
    reportStatus.textContent = `Sunucuya ulaşılamadı: ${error.message}`;
  } finally {
    button.disabled = false;
  }
});

function renderPortfolio(data) {
  $("portfolio-card").hidden = false;
  $("portfolio-note").textContent =
    `${data.factor_set} · ${data.scope} · ${data.tested_sets.length} esasa karşı sınandı`;

  const worst = Math.max(...data.lanes.map((l) => l.baseline_co2_kg), 1);
  const rows = data.lanes.map((lane) => {
    // Three states, and the middle one is the point: a saving that only holds under
    // some bases is not a saving anyone can act on without an argument.
    const state = lane.is_robust
      ? `<span class="lane-tag robust">her esasta kazanıyor</span>`
      : lane.is_contested
        ? `<span class="lane-tag contested">yalnız ${lane.wins_under.length}/${
            lane.tested_under.length} esasta</span>`
        : `<span class="lane-tag none">kazanç yok</span>`;

    const cost = lane.saving_kg > 0
      ? `<span class="lane-cost">${signed(-lane.extra_hours)} sa · ${
          lane.ets_delta_eur >= 0 ? "+" : "−"}€${nf.format(Math.abs(lane.ets_delta_eur))}${
          lane.eur_per_tonne_abated !== null
            ? ` · €${nf.format(lane.eur_per_tonne_abated)}/ton` : ""}</span>`
      : "";

    const emptyMilesWarning = lane.empty_miles_risk 
      ? `<span class="lane-tag bad" style="margin-left:8px;" title="Gidiş/Dönüş dengesizliği: ${lane.imbalance_ratio}">⚠️ Boş Dönüş Riski</span>`
      : "";

    return `<tr ${lane.empty_miles_risk ? 'style="background-color:rgba(230,103,103,0.05);"' : ''}>
      <td class="lane-name">${lane.key}<br><span class="card-note">${
        lane.shipments} sevkiyat · ${nf.format(lane.tonne_km)} ton-km</span>${emptyMilesWarning}</td>
      <td class="num">${nf3.format(lane.intensity_kg_per_tonne_km)}</td>
      <td class="num">${nf.format(lane.baseline_co2_kg)}
        <span class="lane-bar" style="width:${(lane.baseline_co2_kg / worst) * 100}%"></span></td>
      <td class="num ${lane.saving_kg > 0 ? "good" : "bad"}">${signed(lane.saving_kg)}</td>
      <td>${state}${cost}</td>
    </tr>`;
  }).join("");

  const anomalousLanes = data.lanes.filter(l => l.empty_miles_risk);
  let anomalyNotice = "";
  if (anomalousLanes.length > 0) {
      anomalyNotice = `<div class="notice warn" style="margin-bottom:1rem;">
          <strong>⚠️ ${anomalousLanes.length} hatta Boş Dönüş (Empty Miles) Riski tespit edildi!</strong><br>
          Bu hatlarda gidiş ve dönüş yük hacimleri dengesiz (Katsayı > 0.75). Bu durum nakliyecinin dönüşte boş dönme ihtimalini artırarak maliyetlere ve emisyona olumsuz yansıyabilir. Tedarik zinciri planlamasında çift yönlü yük konsolidasyonu önerilir.
      </div>`;
  }

  const headline = data.addressable_co2_kg > 0
    ? `Her esasta kazanan hatlarda toplam <strong>${
        nf.format(data.addressable_co2_kg)} kg</strong> azaltım var.`
    : `<strong>Hiçbir hat her esasta kazanmıyor.</strong> Test edilen GLEC esasları
       altında çok modlu alternatif, denetimde savunulabilir bir azaltım vermiyor —
       bu bir hesap hatası değil, ro-ro deniz bacağının faktörünün sonucu.`;

  $("portfolio").innerHTML = `
    ${anomalyNotice}
    <p class="hint">${headline}</p>
    <div class="table-scroll">
      <table class="lane-table">
        <thead><tr>
          <th>Hat</th><th class="num">kg/ton-km</th><th class="num">Toplam kg</th>
          <th class="num">Fark kg</th><th>Dayanıklılık</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${data.failed.length
      ? `<p class="hint">Rotalanamayan ${data.failed.length} sevkiyat hiçbir hatta sayılmadı.</p>`
      : ""}
    ${data.notes.map((n) => `<p class="hint">${n}</p>`).join("")}`;

  renderCarrierScorecard(data);
  renderConsolidation(data);
  renderGlidepath(data);
}

function renderCarrierScorecard(data) {
  const slot = $("carrier-scorecard-slot");
  if (!slot) return;
  if (!data.carriers || data.carriers.length === 0) {
    slot.hidden = true;
    return;
  }
  
  slot.hidden = false;
  const maxIntensity = Math.max(...data.carriers.map(c => c.intensity_kg_per_tonne_km), 0.0001);
  const rows = data.carriers.map((c) => {
    return `<tr>
      <td><strong>${c.carrier}</strong></td>
      <td class="num">${c.shipments}</td>
      <td class="num">${nf.format(c.tonnes)}</td>
      <td class="num">${nf.format(c.tonne_km)}</td>
      <td class="num">${nf.format(c.total_co2_kg)}</td>
      <td class="num">${nf3.format(c.intensity_kg_per_tonne_km)}
        <span class="lane-bar" style="width:${(c.intensity_kg_per_tonne_km / maxIntensity) * 100}%; background-color: var(--status-bad);"></span>
      </td>
    </tr>`;
  }).join("");

  slot.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Tedarikçi Karbon Karnesi</h2>
        <span class="card-note">Performans bazlı ölçüm</span>
      </div>
      <p class="hint">Taşıyıcıların (Carrier) emisyon yoğunluğuna (kg CO2 / ton-km) göre sıralanmış listesi.</p>
      <div class="table-scroll">
        <table class="lane-table">
          <thead>
            <tr>
              <th>Taşıyıcı</th>
              <th class="num">Sevkiyat</th>
              <th class="num">Ton</th>
              <th class="num">Ton-km</th>
              <th class="num">Toplam CO2 (kg)</th>
              <th class="num">Yoğunluk (kg/ton-km)</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

function renderConsolidation(data) {
  const slot = $("consolidation-slot");
  if (!slot) return;
  
  const opportunities = data.lanes.filter(l => l.consolidation_potential);
  if (opportunities.length === 0) {
    slot.hidden = true;
    return;
  }
  
  slot.hidden = false;
  const rows = opportunities.map(l => {
    const avgTonnage = l.tonnes / l.shipments;
    return `<tr>
      <td>${l.key}</td>
      <td class="num">${l.shipments}</td>
      <td class="num">${nf.format(l.tonnes)}</td>
      <td class="num">${nf1.format(avgTonnage)}</td>
    </tr>`;
  }).join("");

  slot.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Yük Konsolidasyonu (FTL) Fırsatları</h2>
        <span class="card-note">${opportunities.length} hatta potansiyel</span>
      </div>
      <div class="notice good" style="margin-bottom:1rem;">
        <strong>Tavsiye:</strong> LTL (Parsiyel) sevkiyatları birleştirip FTL (Komple) taşıma yaparak rotalama verimliliğini artırın. Ortalama ağırlığı 18 ton altı olan, birden fazla sevkiyat içeren hatlar aşağıdadır.
      </div>
      <div class="table-scroll">
        <table class="lane-table">
          <thead>
            <tr>
              <th>Hat</th>
              <th class="num">Sevkiyat Sayısı</th>
              <th class="num">Toplam Ton</th>
              <th class="num">Ortalama Tonaj</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

function renderGlidepath(data) {
  const slot = $("glidepath-slot");
  if (!slot) return;
  if (!data.glidepath) {
    slot.hidden = true;
    return;
  }
  
  slot.hidden = false;
  const gp = data.glidepath;
  
  // Calculate reductions
  const multiModalReduction = gp.baseline_co2_kg - gp.best_scenario_co2_kg;
  const netZeroReduction = gp.best_scenario_co2_kg - gp.target_2030_co2_kg;
  
  slot.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2 class="card-title">Net-Zero 2030 Yörüngesi (Glidepath)</h2>
        <span class="card-note">Karbon azaltım hedefleri</span>
      </div>
      <p class="hint">Portföyünüzün bugünden 2030'a emisyon azaltım potansiyeli.</p>
      
      <div class="glidepath-metrics" style="display:flex; gap:1rem; margin-top:1rem; margin-bottom:1.5rem;">
        <div style="flex:1; padding:1rem; background:rgba(0,0,0,0.02); border-radius:8px; border-left:4px solid var(--border-subtle);">
          <div style="font-size:0.875rem; color:var(--text-muted); margin-bottom:0.25rem;">Mevcut Karayolu (Baseline)</div>
          <div style="font-size:1.5rem; font-weight:600; color:var(--text-main);">${nf.format(gp.baseline_co2_kg)} kg</div>
        </div>
        <div style="flex:1; padding:1rem; background:rgba(15,122,69,0.05); border-radius:8px; border-left:4px solid var(--status-good);">
          <div style="font-size:0.875rem; color:var(--status-good); margin-bottom:0.25rem;">Multimodal Geçişi (-${Math.round((multiModalReduction/gp.baseline_co2_kg)*100)}%)</div>
          <div style="font-size:1.5rem; font-weight:600; color:var(--text-main);">${nf.format(gp.best_scenario_co2_kg)} kg</div>
        </div>
        <div style="flex:1; padding:1rem; background:rgba(30,136,229,0.05); border-radius:8px; border-left:4px solid #1e88e5;">
          <div style="font-size:0.875rem; color:#1e88e5; margin-bottom:0.25rem;">2030 Hedefi (Biyoyakıt & FTL)</div>
          <div style="font-size:1.5rem; font-weight:600; color:var(--text-main);">${nf.format(gp.target_2030_co2_kg)} kg</div>
        </div>
      </div>
    </div>
  `;
}

/* ── ISO 14083 self-assessment ───────────────────────────────────────── */

/** What the figure on screen can and cannot be used for.
 *
 *  Reloaded whenever the scenario changes, because the answer depends on it: the same
 *  shipment priced tank-to-wheel is not reportable under the standard at all, and the
 *  dashboard should say that where the choice is made rather than in a footnote.
 *
 *  The gaps are the point. Two of them never close from our own data — hub emissions
 *  are not computed, and every factor is a published default where the standard ranks
 *  the carrier's own fuel measurements above them — so they are shown as absent rather
 *  than dropped from the list.
 */
const CONFORMANCE_STATUS = {
  met: { mark: "✓", label: "karşılanıyor", css: "met" },
  partial: { mark: "~", label: "kısmen", css: "partial" },
  missing: { mark: "✗", label: "eksik", css: "missing" },
};

async function loadConformance(scenario) {
  const card = $("conformance-card");
  if (!card || !scenario) return;
  const params = new URLSearchParams({
    factor_set: scenario.factor_set,
    scope: scenario.scope,
  });
  const fuel = new FormData(form).get("road_fuel_type");
  if (fuel) params.set("road_fuel_type", fuel);

  try {
    const response = await fetch(`/api/conformance?${params}`);
    if (!response.ok) { card.hidden = true; return; }
    renderConformance(await response.json());
  } catch {
    card.hidden = true;   // an assessment that cannot load must not block the dashboard
  }
}

function renderConformance(data) {
  $("conformance-card").hidden = false;
  $("conformance-note").textContent = `${data.factor_set} · ${data.scope}`;

  const verdictClass = data.verdict === "reportable"
    ? "good" : data.verdict === "not-reportable" ? "bad" : "warn";

  const rows = data.checks.map((check) => {
    const state = CONFORMANCE_STATUS[check.status] ?? CONFORMANCE_STATUS.missing;
    return `<li class="check ${state.css}${check.is_blocking ? " blocking" : ""}">
      <span class="check-mark" aria-hidden="true">${state.mark}</span>
      <span class="check-body">
        <span class="check-req">${check.requirement}</span>
        <span class="card-note">${check.clause} · ${check.evidence}</span>
        ${check.gap ? `<span class="check-gap">Eksik: ${check.gap}</span>` : ""}
      </span>
    </li>`;
  }).join("");

  $("conformance").innerHTML = `
    <p class="verdict ${verdictClass}">${data.verdict_tr}</p>
    <p class="hint">Veri kalitesi <strong>${nf1.format(data.data_quality)}/5</strong> —
      ${data.data_quality_note}</p>
    <ul class="check-list">${rows}</ul>
    ${data.notes.map((n) => `<p class="hint">${n}</p>`).join("")}`;
}

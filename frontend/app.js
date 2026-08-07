// Slots 1-3 of the dataviz reference palette, checked against a white surface with
// the skill's validator: lightness band, chroma floor, CVD separation and the
// normal-vision floor all pass on both the adjacent and the all-pairs list.
const MODE_COLOURS = { road: "#eb6834", sea: "#2a78d6", rail: "#1baf7a" };
const MODE_LABELS = { road: "karayolu", sea: "deniz", rail: "demiryolu" };
const MODE_ORDER = ["road", "sea", "rail"];

const form = document.getElementById("shipment-form");
const results = document.getElementById("results");
const statusLine = document.getElementById("status");
const submitButton = document.getElementById("submit");
const factorSelect = document.getElementById("factor-set");
const scopeSelect = document.getElementById("scope");
const factorHint = document.getElementById("factor-hint");
const originInput = document.getElementById("origin");
const destinationInput = document.getElementById("destination");
const pickOrigin = document.getElementById("pick-origin");
const pickDestination = document.getElementById("pick-destination");
const mapElement = document.getElementById("map");

let factorSets = [];
let map;
let drawnLayers = [];
let endpointMarkers = {};
let picking = null;
let latest = null;

const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 4 });

function parsePoint(value) {
  const [lon, lat] = value.split(",").map((part) => Number(part.trim()));
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  if (Math.abs(lon) > 180 || Math.abs(lat) > 90) return null;
  return { lon, lat };
}

const formatPoint = (lngLat) => `${lngLat.lng.toFixed(4)}, ${lngLat.lat.toFixed(4)}`;

/* ── map ─────────────────────────────────────────────────────────────────── */

/** The map is a nice-to-have. Losing it must not take the calculator down with it. */
function initMap() {
  if (typeof maplibregl === "undefined") {
    mapElement.innerHTML =
      '<p class="map-unavailable">Harita kütüphanesi yüklenemedi (çevrimdışı olabilirsiniz). '
      + "Hesaplama, sonuç tablosu ve rapor indirme çalışmaya devam eder.</p>";
    return;
  }
  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      // Symbol layers need a glyph source; without it the terminal labels never render.
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
    zoom: 3.6,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  map.on("load", () => {
    loadTerminals();
    placeEndpointMarkers();
  });
  map.on("click", onMapClick);
}

function endpointMarker(kind, lngLat) {
  const element = document.createElement("div");
  element.style.cssText =
    "width:15px;height:15px;border-radius:50%;box-shadow:0 0 0 2px #fff,0 1px 3px rgba(0,0,0,.4);"
    + `background:${kind === "origin" ? "#14181d" : "#b3261e"}`;
  const marker = new maplibregl.Marker({ element, draggable: true })
    .setLngLat(lngLat)
    .setPopup(new maplibregl.Popup({ offset: 14, closeButton: false })
      .setText(kind === "origin" ? "Kalkış — sürükleyebilirsiniz" : "Varış — sürükleyebilirsiniz"))
    .addTo(map);
  marker.on("dragend", () => {
    const input = kind === "origin" ? originInput : destinationInput;
    input.value = formatPoint(marker.getLngLat());
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
  pickOrigin.setAttribute("aria-pressed", String(picking === "origin"));
  pickDestination.setAttribute("aria-pressed", String(picking === "destination"));
  mapElement.classList.toggle("picking", picking !== null);
}

function onMapClick(event) {
  if (!picking) return;
  const input = picking === "origin" ? originInput : destinationInput;
  input.value = formatPoint(event.lngLat);
  placeEndpointMarkers();
  setPicking(null);
}

async function loadTerminals() {
  const terminals = await fetch("/api/terminals").then((r) => r.json());
  map.addSource("terminals", {
    type: "geojson",
    data: {
      type: "FeatureCollection",
      features: terminals
        .filter((t) => t.is_connected)
        .map((t) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [t.lon, t.lat] },
          properties: { name: t.name, type: t.type, country: t.country },
        })),
    },
  });
  // A 2px surface ring keeps the dot legible where a route line crosses it.
  map.addLayer({
    id: "terminals",
    type: "circle",
    source: "terminals",
    paint: {
      "circle-radius": 4.5,
      "circle-color": "#ffffff",
      "circle-stroke-color": "#454d59",
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "terminal-labels",
    type: "symbol",
    source: "terminals",
    minzoom: 4.6,
    layout: {
      "text-field": ["get", "name"],
      "text-size": 11,
      "text-offset": [0, 1.1],
      "text-anchor": "top",
      // The glyph server above serves Noto, not Open Sans; asking for the wrong
      // fontstack 404s and the labels silently never appear.
      "text-font": ["Noto Sans Regular"],
    },
    paint: { "text-color": "#454d59", "text-halo-color": "#ffffff", "text-halo-width": 1.5 },
  });

  const hover = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
  map.on("mouseenter", "terminals", (event) => {
    map.getCanvas().style.cursor = "pointer";
    const { name, type, country } = event.features[0].properties;
    hover.setLngLat(event.features[0].geometry.coordinates)
      .setHTML(`<strong>${name}</strong>${type} · ${country}`)
      .addTo(map);
  });
  map.on("mouseleave", "terminals", () => {
    map.getCanvas().style.cursor = picking ? "crosshair" : "";
    hover.remove();
  });
}

function clearRoute() {
  if (!map) return;
  drawnLayers.forEach((id) => {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getLayer(`${id}-hit`)) map.removeLayer(`${id}-hit`);
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
function drawAlternative(alternative) {
  if (!map) return;
  clearRoute();
  const bounds = new maplibregl.LngLatBounds();

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
          co2: nf.format(leg.co2_kg),
          factor: `${nf1.format(leg.factor_value)} kg CO2/ton-km`,
          schematic: schematic ? "1" : "",
        },
      },
    });
    map.addLayer({
      id,
      type: "line",
      source: id,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": MODE_COLOURS[leg.mode] ?? "#6b7480",
        "line-width": 3,
        "line-dasharray": schematic ? [2, 1.6] : [1],
      },
    });
    // An invisible fat line under the thin one: the hit target is bigger than the mark.
    map.addLayer({
      id: `${id}-hit`,
      type: "line",
      source: id,
      paint: { "line-color": "#000", "line-opacity": 0, "line-width": 18 },
    });
    drawnLayers.push(id);
  });

  attachLegHover();
  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60, duration: 600 });
}

function attachLegHover() {
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8 });
  drawnLayers.forEach((id) => {
    const hit = `${id}-hit`;
    map.on("mousemove", hit, (event) => {
      map.getCanvas().style.cursor = "pointer";
      const p = event.features[0].properties;
      popup
        .setLngLat(event.lngLat)
        .setHTML(
          `<strong>${p.label}</strong>${p.mode} · ${p.km} km · ${p.co2} kg CO2<br>`
          + `<span style="color:#6b7480">faktör ${p.factor}`
          + `${p.schematic ? " · şematik çizim" : ""}</span>`,
        )
        .addTo(map);
    });
    map.on("mouseleave", hit, () => {
      map.getCanvas().style.cursor = picking ? "crosshair" : "";
      popup.remove();
    });
  });
}

/* ── results ─────────────────────────────────────────────────────────────── */

function modeBar(alternative) {
  const total = MODE_ORDER.reduce((sum, m) => sum + (alternative.co2_by_mode[m] ?? 0), 0);
  if (!total) return "";
  const segments = MODE_ORDER.filter((m) => alternative.co2_by_mode[m])
    .map((m) => {
      const share = (alternative.co2_by_mode[m] / total) * 100;
      return `<span class="${m}" style="flex:0 0 ${share.toFixed(2)}%"
                title="${MODE_LABELS[m]}: ${nf.format(alternative.co2_by_mode[m])} kg CO2"></span>`;
    })
    .join("");
  return `<div class="mode-bar" role="img"
            aria-label="Moda göre CO2 dağılımı: ${MODE_ORDER
              .filter((m) => alternative.co2_by_mode[m])
              .map((m) => `${MODE_LABELS[m]} ${nf.format(alternative.co2_by_mode[m])} kg`)
              .join(", ")}">${segments}</div>`;
}

function verdict(alternative) {
  if (alternative.is_all_road || alternative.saving_co2_kg === null) return "";
  const saving = alternative.saving_co2_kg;
  if (saving > 0) {
    const trees = alternative.tree_equivalent.average_tree ?? 0;
    return `<p class="verdict good">Tam karayoluna göre <strong>${nf.format(saving)} kg</strong>
      daha az CO2 — yıllık ${nf.format(trees)} ağacın tuttuğu kadar.</p>`;
  }
  return `<p class="verdict bad">Tam karayoluna göre <strong>${nf.format(-saving)} kg</strong>
    <em>daha fazla</em> CO2. Bu faktör setiyle çok modlu seçenek kazandırmıyor.</p>`;
}

function renderAlternative(alternative, index) {
  const legRows = alternative.legs
    .map(
      (leg) => `<tr>
        <td><span class="leg-mark ${leg.mode}"></span>${leg.from_name} → ${leg.to_name}</td>
        <td class="num">${nf.format(leg.distance_km)}</td>
        <td class="num">${nf.format(leg.co2_kg)}</td>
      </tr>`,
    )
    .join("");

  const range = alternative.emission_range
    ? `<p class="hint">Belirsizlik aralığı (%${Math.round(
        alternative.emission_range.confidence * 100,
      )} güven): ${nf.format(alternative.emission_range.low_co2_kg)} –
       ${nf.format(alternative.emission_range.high_co2_kg)} kg CO2</p>`
    : "";

  return `<article class="alternative${index === 0 ? " selected" : ""}" data-index="${index}"
            tabindex="0" role="button" aria-label="${alternative.label} rotasını haritada göster">
      <div class="alt-head">
        <h3>${alternative.label}</h3>
        ${alternative.is_all_road ? '<span class="baseline-tag">karşılaştırma temeli</span>' : ""}
      </div>
      <p class="headline">
        <span class="value">${nf.format(alternative.total_co2_kg)}</span>
        <span class="unit">kg CO2</span>
      </p>
      ${modeBar(alternative)}
      <p class="sub">${nf.format(alternative.total_distance_km)} km ·
        ${MODE_ORDER.filter((m) => alternative.distance_by_mode[m])
          .map((m) => `${MODE_LABELS[m]} ${nf.format(alternative.distance_by_mode[m])} km`)
          .join(" · ")}</p>
      <table>
        <thead><tr><th>Bacak</th><th>km</th><th>kg CO2</th></tr></thead>
        <tbody>${legRows}</tbody>
      </table>
      ${verdict(alternative)}
      ${range}
    </article>`;
}

function render(data) {
  latest = data;
  const warnings = data.warnings.length
    ? `<div class="notice"><strong>Uyarılar</strong><ul>${data.warnings
        .map((w) => `<li>${w}</li>`)
        .join("")}</ul></div>`
    : "";

  results.innerHTML = `
    <div class="legend">
      ${MODE_ORDER.map(
        (m) => `<span class="key"><span class="swatch ${m}"></span>${MODE_LABELS[m]}</span>`,
      ).join("")}
      <span class="sep"></span>
      <span class="key"><span class="dashed-key"></span>şematik çizim</span>
    </div>
    ${warnings}
    ${data.alternatives.map(renderAlternative).join("")}
    <p class="provenance">Faktör seti <strong>${data.factor_set}</strong> ·
      kapsam <strong>${data.scope}</strong> ·
      ${data.sources.join("; ") || "doğrulanmamış"}</p>`;

  results.querySelectorAll(".alternative").forEach((element) => {
    const select = () => {
      results.querySelectorAll(".alternative").forEach((e) => e.classList.remove("selected"));
      element.classList.add("selected");
      drawAlternative(data.alternatives[Number(element.dataset.index)]);
    };
    element.addEventListener("click", select);
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
    });
  });

  if (data.alternatives.length) drawAlternative(data.alternatives[0]);
}

/* ── factor sets ─────────────────────────────────────────────────────────── */

function updateScopes() {
  const chosen = factorSets.find((set) => set.name === factorSelect.value);
  const previous = scopeSelect.value;
  scopeSelect.innerHTML = chosen.scopes.map((s) => `<option value="${s}">${s}</option>`).join("");
  if (chosen.scopes.includes(previous)) scopeSelect.value = previous;

  const sea = chosen.sea_factor_by_scope[scopeSelect.value];
  factorHint.textContent = chosen.description
    + (sea ? ` · deniz faktörü ${sea} kg CO2/ton-km` : "")
    + (chosen.is_verified ? "" : " · DOĞRULANMAMIŞ, rapora girmemeli");
}

async function loadFactorSets() {
  const all = await fetch("/api/factor-sets").then((r) => r.json());
  // A set without a factor for every mode can only ever answer with an error, so it is
  // not offered as a choice.
  factorSets = all.filter((set) => Object.keys(set.sea_factor_by_scope).length > 0);
  factorSelect.innerHTML = factorSets
    .map((set) => `<option value="${set.name}">${set.name}</option>`)
    .join("");
  factorSelect.value = factorSets.some((s) => s.name === "glec") ? "glec" : factorSets[0].name;
  updateScopes();
}

/* ── wiring ──────────────────────────────────────────────────────────────── */

pickOrigin.addEventListener("click", () => setPicking("origin"));
pickDestination.addEventListener("click", () => setPicking("destination"));
[originInput, destinationInput].forEach((input) =>
  input.addEventListener("change", placeEndpointMarkers),
);
factorSelect.addEventListener("change", updateScopes);
scopeSelect.addEventListener("change", updateScopes);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const origin = parsePoint(data.get("origin"));
  const destination = parsePoint(data.get("destination"));

  if (!origin || !destination) {
    statusLine.textContent = "Kalkış ve varış 'boylam, enlem' biçiminde olmalı.";
    return;
  }

  const body = {
    origin,
    destination,
    origin_name: data.get("origin_name") || "kalkış",
    destination_name: data.get("destination_name") || "varış",
    tonnage: Number(data.get("tonnage")),
    factor_set: data.get("factor_set"),
    scope: data.get("scope"),
  };
  if (data.get("load_factor")) body.load_factor = Number(data.get("load_factor"));
  if (data.get("empty_return_share")) {
    body.empty_return_share = Number(data.get("empty_return_share"));
  }

  submitButton.disabled = true;
  statusLine.textContent = "Hesaplanıyor — soğuk istek birkaç saniye sürebilir…";
  try {
    const response = await fetch("/api/routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      results.innerHTML = `<div class="error">${payload.detail ?? "İstek başarısız."}</div>`;
      statusLine.textContent = "";
      return;
    }
    render(payload);
    statusLine.textContent = "";
  } catch (error) {
    results.innerHTML = `<div class="error">Sunucuya ulaşılamadı: ${error.message}</div>`;
    statusLine.textContent = "";
  } finally {
    submitButton.disabled = false;
  }
});

const reportForm = document.getElementById("report-form");
const reportStatus = document.getElementById("report-status");
const reportSubmit = document.getElementById("report-submit");

reportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = new FormData(reportForm);
  // The report is priced with whatever the panel above is set to, so one screen
  // cannot hand back two different answers for the same shipment.
  body.set("scope", scopeSelect.value);
  body.set("factor_set", factorSelect.value);
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
    link.href = url;
    link.download = "freightprint-rapor.csv";
    link.click();
    URL.revokeObjectURL(url);
    reportStatus.textContent = "Rapor indirildi.";
  } catch (error) {
    reportStatus.textContent = `Sunucuya ulaşılamadı: ${error.message}`;
  } finally {
    reportSubmit.disabled = false;
  }
});

initMap();
loadFactorSets();

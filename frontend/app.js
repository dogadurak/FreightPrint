const MODE_COLOURS = { road: "#d97706", sea: "#0e7490", rail: "#15803d" };
const MODE_LABELS = { road: "karayolu", sea: "deniz", rail: "demiryolu" };

const form = document.getElementById("shipment-form");
const results = document.getElementById("results");
const statusLine = document.getElementById("status");
const submitButton = document.getElementById("submit");
const factorSelect = document.getElementById("factor-set");
const scopeSelect = document.getElementById("scope");
const factorHint = document.getElementById("factor-hint");

let factorSets = [];
let map;
let drawnLayers = [];

const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });

function parsePoint(value) {
  const [lon, lat] = value.split(",").map((part) => Number(part.trim()));
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
  if (Math.abs(lon) > 180 || Math.abs(lat) > 90) return null;
  return { lon, lat };
}

function initMap() {
  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
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
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  map.on("load", loadTerminals);
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
          properties: { name: t.name, type: t.type },
        })),
    },
  });
  map.addLayer({
    id: "terminals",
    type: "circle",
    source: "terminals",
    paint: {
      "circle-radius": 4,
      "circle-color": "#ffffff",
      "circle-stroke-color": "#5b6470",
      "circle-stroke-width": 1.5,
    },
  });
  map.on("click", "terminals", (event) => {
    const { name, type } = event.features[0].properties;
    new maplibregl.Popup()
      .setLngLat(event.lngLat)
      .setText(`${name} (${type})`)
      .addTo(map);
  });
}

function clearRoute() {
  drawnLayers.forEach((id) => {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  });
  drawnLayers = [];
}

function terminalCoordinate(name) {
  const source = map.getSource("terminals");
  if (!source) return null;
  const match = source._data.features.find((f) => f.properties.name === name);
  return match ? match.geometry.coordinates : null;
}

/** Draw one alternative. Legs without geometry are dashed: schematic, not surveyed. */
function drawAlternative(alternative) {
  clearRoute();
  const bounds = new maplibregl.LngLatBounds();

  alternative.legs.forEach((leg, index) => {
    let coordinates = leg.geometry;
    if (!coordinates.length) {
      const from = terminalCoordinate(leg.from_name.replace(" (ferry)", ""));
      const to = terminalCoordinate(leg.to_name);
      if (!from || !to) return;
      coordinates = [from, to];
    }
    coordinates.forEach((point) => bounds.extend(point));

    const id = `leg-${index}`;
    map.addSource(id, {
      type: "geojson",
      data: { type: "Feature", geometry: { type: "LineString", coordinates } },
    });
    map.addLayer({
      id,
      type: "line",
      source: id,
      paint: {
        "line-color": MODE_COLOURS[leg.mode] ?? "#666",
        "line-width": 3.5,
        "line-dasharray": leg.geometry.length ? [1] : [2, 1.6],
      },
    });
    drawnLayers.push(id);
  });

  if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 50, duration: 600 });
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
        <td><span class="mode-tag mode-${leg.mode}"></span>${leg.from_name} → ${leg.to_name}</td>
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
      <header>
        <h3>${alternative.label}</h3>
        ${alternative.is_all_road ? '<span class="baseline-tag">karşılaştırma temeli</span>' : ""}
      </header>
      <p class="hint">${nf.format(alternative.total_distance_km)} km ·
         <span class="total">${nf.format(alternative.total_co2_kg)} kg CO2</span></p>
      <table>
        <thead><tr><th>Bacak</th><th>km</th><th>kg CO2</th></tr></thead>
        <tbody>${legRows}</tbody>
      </table>
      ${verdict(alternative)}
      ${range}
    </article>`;
}

function render(data) {
  const warnings = data.warnings.length
    ? `<div class="notice"><strong>Uyarılar</strong><ul>${data.warnings
        .map((w) => `<li>${w}</li>`)
        .join("")}</ul></div>`
    : "";

  results.innerHTML = `
    <div class="legend">
      <span><span class="mode-tag mode-road"></span>karayolu</span>
      <span><span class="mode-tag mode-sea"></span>deniz</span>
      <span><span class="mode-tag mode-rail"></span>demiryolu</span>
      <span>kesikli çizgi = şematik (gerçek güzergâh değil)</span>
    </div>
    ${warnings}
    ${data.alternatives.map(renderAlternative).join("")}
    <div class="legend">Faktör seti: <strong>${data.factor_set}</strong> ·
      kapsam <strong>${data.scope}</strong> · ${data.sources.join("; ") || "doğrulanmamış"}</div>`;

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
  factorSets = await fetch("/api/factor-sets").then((r) => r.json());
  factorSelect.innerHTML = factorSets
    .map((set) => `<option value="${set.name}">${set.name}</option>`)
    .join("");
  factorSelect.value = factorSets.some((s) => s.name === "glec") ? "glec" : factorSets[0].name;
  updateScopes();
}

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

initMap();
loadFactorSets();

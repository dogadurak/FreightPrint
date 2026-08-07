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

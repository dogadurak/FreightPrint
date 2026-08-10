"""A self-assessment of a report against ISO 14083, and what it would take to close it.

This is **not a certification and does not claim conformance.** It is the answer to a
question a carrier's sustainability lead asks before sending a figure anywhere: *will
this survive being looked at?* The value is in the gaps it names, not the boxes it
ticks — a tool that reported "conformant" because it checked only the things it happens
to do would be worse than no tool.

Two of the checks can never pass on this engine's own data and are the honest headline:
the standard wants hub emissions, which are not computed here at all, and it ranks
operator-measured fuel data above any published default, which nothing here has. Both
are reported as absent rather than quietly scoped out.

Data quality follows the GLEC Framework's five-point index. The mapping from our
evidence to those points is stated in `DATA_QUALITY` rather than left implicit, because
a score whose derivation is hidden is a score nobody can argue with.
"""

from dataclasses import dataclass, field
from datetime import date

from .emissions import EmissionFactor, load_emission_factors

# GLEC's data quality index, and what each point means in terms of what we can show.
# Nothing here can reach 5 or 4: both require data measured by the operator running the
# vehicle, which this engine never sees.
DATA_QUALITY = {
    5: "Operator-measured fuel or energy, third-party verified",
    4: "Operator-measured fuel or energy, unverified",
    3: "Published modal default matched to the actual vehicle and fuel",
    2: "Published default derived or adapted from another basis",
    1: "Proxy or placeholder; not suitable for reporting",
}

# A factor older than this is still usable but has to be flagged: fleets and grids move.
STALE_AFTER_YEARS = 6


@dataclass
class Check:
    id: str
    clause: str
    requirement: str
    status: str  # "met" | "partial" | "missing"
    evidence: str
    gap: str = ""

    @property
    def is_blocking(self) -> bool:
        """A missing check that stops the figure being reportable at all, rather than
        merely weakening it."""
        return self.status == "missing" and self.id in {"scope", "empty_running"}


@dataclass
class Conformance:
    checks: list[Check]
    data_quality: float
    data_quality_note: str
    factor_set: str
    scope: str
    notes: list[str] = field(default_factory=list)

    @property
    def met(self) -> list[Check]:
        return [c for c in self.checks if c.status == "met"]

    @property
    def missing(self) -> list[Check]:
        return [c for c in self.checks if c.status == "missing"]

    @property
    def partial(self) -> list[Check]:
        return [c for c in self.checks if c.status == "partial"]

    @property
    def verdict(self) -> str:
        """What the figure may honestly be used for."""
        if any(c.is_blocking for c in self.checks):
            return "not-reportable"
        if self.missing or self.data_quality < 3:
            return "reportable-not-verifiable"
        return "reportable"

    @property
    def verdict_tr(self) -> str:
        return {
            "not-reportable": "Raporlanamaz — zorunlu bir gereklilik karşılanmıyor",
            "reportable-not-verifiable": "Beyan edilebilir, doğrulanamaz",
            "reportable": "Beyan edilebilir",
        }[self.verdict]


def _used_factors(
    factor_set: str, scope: str, factors: list[EmissionFactor],
    road_fuel_type: str | None, modes: tuple[str, ...],
) -> list[EmissionFactor]:
    """The factors a report on this basis would actually reach for.

    Not every row in the set: a report priced on diesel must not be marked down because
    the same set also carries derived HVO rows it never touched. This resolves the same
    way `find_factor` does — the named fuel, or the row the data declares default.
    """
    used = []
    for mode in modes:
        rows = [
            f for f in factors
            if f.factor_set == factor_set and f.scope == scope and f.mode == mode
        ]
        if not rows:
            continue
        if mode == "road" and road_fuel_type:
            rows = [f for f in rows if f.fuel_type == road_fuel_type] or rows
        elif len(rows) > 1:
            rows = [f for f in rows if f.is_default] or rows
        used.extend(rows[:1] if len(rows) > 1 else rows)
    return used


def assess(
    factor_set: str,
    scope: str,
    road_fuel_type: str | None = None,
    modes: tuple[str, ...] = ("road", "sea", "rail"),
    factors: list[EmissionFactor] | None = None,
    today: date | None = None,
) -> Conformance:
    """Assess what a report priced on this basis can and cannot claim."""
    factors = factors if factors is not None else load_emission_factors()
    used = _used_factors(factor_set, scope, factors, road_fuel_type, modes)
    today = today or date.today()

    if not used:
        raise ValueError(f"no factors for set {factor_set!r} at scope {scope!r}")

    checks: list[Check] = []

    # ISO 14083 quantifies on a well-to-wheel basis. A tank-to-wheel figure omits fuel
    # production entirely, which for electricity is the whole of it.
    checks.append(Check(
        id="scope", clause="ISO 14083:2023, 6.2",
        requirement="Emisyonlar kuyudan-tekere (WTW) esasında hesaplanmalı",
        status="met" if scope == "WTW" else "missing",
        evidence=f"Rapor kapsamı: {scope}",
        gap="" if scope == "WTW" else "TTW yakıt üretimini hiç saymaz; --scope WTW kullanın",
    ))

    sourced = [f for f in used if f.source.strip()]
    checks.append(Check(
        id="factor_source", clause="ISO 14083:2023, 9.2",
        requirement="Her faktörün kaynağı ve yılı beyan edilmeli",
        status="met" if len(sourced) == len(used) else "partial",
        evidence=f"{len(sourced)}/{len(used)} faktör kaynağını taşıyor",
        gap="" if len(sourced) == len(used) else "Kaynaksız faktörler var",
    ))

    stale = [f for f in used if today.year - f.year > STALE_AFTER_YEARS]
    checks.append(Check(
        id="factor_vintage", clause="ISO 14083:2023, 9.2",
        requirement=f"Faktörler güncel olmalı ({STALE_AFTER_YEARS} yıldan eski değil)",
        status="met" if not stale else "partial",
        evidence=f"En eski faktör yılı: {min(f.year for f in used)}",
        gap="" if not stale else f"{len(stale)} faktör {STALE_AFTER_YEARS} yıldan eski",
    ))

    unverified = [f for f in used if not f.is_verified]
    checks.append(Check(
        id="factor_verified", clause="ISO 14083:2023, 9.3",
        requirement="Faktörler yayımlanmış bir kaynakla doğrulanabilmeli",
        status="met" if not unverified else "partial",
        evidence=f"{len(used) - len(unverified)}/{len(used)} faktör doğrulanmış",
        gap="" if not unverified else (
            f"Türetme faktörler: {', '.join(sorted({f.fuel_type for f in unverified}))}"
        ),
    ))

    road = [f for f in used if f.mode == "road"]
    with_empty = [f for f in road if f.basis_empty_share > 0]
    checks.append(Check(
        id="empty_running", clause="ISO 14083:2023, 7.4",
        requirement="Boş dönüş mesafesi hesaba katılmalı",
        status="met" if road and len(with_empty) == len(road) else "missing",
        evidence=(
            f"Karayolu faktörleri %{road[0].basis_empty_share * 100:.0f} boş dönüş içeriyor"
            if with_empty else "Boş dönüş payı yok"
        ),
        gap="" if with_empty else "Boş dönüşü içermeyen faktör raporlanamaz",
    ))

    checks.append(Check(
        id="load_factor", clause="ISO 14083:2023, 7.3",
        requirement="Doluluk oranı beyan edilmeli",
        status="met" if all(f.basis_load_factor for f in used) else "partial",
        evidence=f"Karayolu doluluk esası: %{road[0].basis_load_factor * 100:.0f}" if road else "-",
    ))

    checks.append(Check(
        id="chain_elements", clause="ISO 14083:2023, 6.4",
        requirement="Taşıma zinciri unsurları (TCE) ayrı ayrı tanımlanmalı",
        status="met",
        evidence="Her bacak mod, kalkış, varış, mesafe ve faktörüyle raporlanıyor",
    ))

    checks.append(Check(
        id="allocation", clause="ISO 14083:2023, 8.2",
        requirement="Tahsis yöntemi beyan edilmeli",
        status="met",
        evidence="Ton-kilometre esaslı tahsis; sevkiyat ağırlığı × bacak mesafesi",
    ))

    # The two that cannot pass here, and saying so is the point of the module.
    checks.append(Check(
        id="hub_emissions", clause="ISO 14083:2023, 6.5",
        requirement="Taşıma merkezi (hub) emisyonları dâhil edilmeli",
        status="missing",
        evidence="Terminal ve depo enerjisi hiç hesaplanmıyor",
        gap="Liman/terminal başına kWh veya yakıt tüketimi gerekir; işletmeciden alınmalı. "
            "Ro-ro elleçlemede tipik olarak zincirin %1-3'ü, yani beyanı düşürmez ama eksiktir.",
    ))

    checks.append(Check(
        id="primary_data", clause="ISO 14083:2023, 9.1",
        requirement="Mümkün olduğunda taşıyıcının ölçtüğü yakıt verisi kullanılmalı",
        status="missing",
        evidence="Tüm faktörler yayımlanmış varsayılan değerlerden; birincil veri yok",
        gap="Taşıyıcıdan sefer başına yakıt tüketimi alınırsa veri kalitesi 3'ten 4'e çıkar "
            "ve rakam doğrulanabilir hâle gelir.",
    ))

    # Data quality: the best point every used factor can support. Never above 3 here,
    # because 4 and 5 both require the operator's own measurements.
    if unverified:
        quality = 2.0 if len(unverified) < len(used) else 1.0
        note = ("Türetme faktörler kullanıldığı için 3'ün altında; "
                f"{len(unverified)}/{len(used)} faktör yayımlanmış bir değerden uyarlanmış.")
    else:
        quality = 3.0
        note = ("Yayımlanmış modal varsayılanlar, gerçek araç ve yakıta eşleştirilmiş. "
                "4 ve 5 yalnızca taşıyıcının kendi ölçtüğü veriyle mümkündür.")

    return Conformance(
        checks=checks,
        data_quality=quality,
        data_quality_note=note,
        factor_set=factor_set,
        scope=scope,
        notes=[
            "Bu bir öz değerlendirmedir, belgelendirme değildir. Yalnızca motorun kendi "
            "verisinden kontrol edilebilen maddeleri kapsar.",
            f"Veri kalitesi GLEC beş puanlı indeksine göre: {DATA_QUALITY[int(quality)]}",
        ],
    )

"""The alternative-fuel rows are derived, not published, so their chain is pinned here.

GLEC covers only Diesel, CNG, LNG and Bio-LNG; there is no HVO row and no electric row
in it. These factors are therefore built from the GLEC diesel row plus outside sources,
and the arithmetic is asserted rather than trusted so it cannot drift unnoticed.
"""

import pytest

from app.core.emissions import find_factor, load_emission_factors

# The published inputs the derivation rests on.
DIESEL_TTW_PER_L, DIESEL_WTT_PER_L = 2.58354, 0.61101  # DEFRA/DESNZ 2026
HVO_TTW_PER_L, HVO_WTT_PER_L = 0.03558, 0.559  # DEFRA 2026 / DEFRA 2024
MJ_PER_L_DIESEL, MJ_PER_L_HVO = 35.8, 34.4
EFFICIENCY_DIESEL, EFFICIENCY_ELECTRIC = 0.375, 0.78
GRID_LOSS = 1.07

GLEC_DIESEL_WTW = 0.075
FACTOR_SETS = ("glec", "glec_accompanied", "glec_freight_average")


@pytest.fixture(scope="module")
def factors():
    return load_emission_factors()


def factor_for(factors, fuel, scope, factor_set="glec"):
    return find_factor(
        factors, mode="road", scope=scope, factor_set=factor_set, fuel_type=fuel
    )


def test_the_derivation_assumes_no_payload_of_its_own(factors):
    """The whole point of scaling off the GLEC row rather than building from a truck's
    consumption: GLEC's load factor and empty running carry over instead of a second,
    unrelated payload sneaking in. An earlier version assumed 21 t against GLEC's
    implied 14 t and flattered electric by half again."""
    diesel = factor_for(factors, "diesel_b5", "WTW")
    electric = factor_for(factors, "electric_eu", "WTW")
    hvo = factor_for(factors, "hvo", "WTW")

    for derived in (electric, hvo):
        assert derived.basis_load_factor == diesel.basis_load_factor
        assert derived.basis_empty_share == diesel.basis_empty_share


def test_hvo_reproduces_the_defra_ratio(factors):
    """Same engine, same load, same route — only the fuel differs, so the per-litre
    ratio transfers, corrected for HVO's lower volumetric energy."""
    volume_penalty = MJ_PER_L_DIESEL / MJ_PER_L_HVO
    expected = GLEC_DIESEL_WTW * (
        (HVO_TTW_PER_L + HVO_WTT_PER_L) * volume_penalty
        / (DIESEL_TTW_PER_L + DIESEL_WTT_PER_L)
    )

    assert factor_for(factors, "hvo", "WTW").value == pytest.approx(expected, rel=1e-3)


def test_hvo_is_a_large_cut_but_not_a_free_one(factors):
    """Roughly a fifth of diesel — not the zero its biogenic TTW figure suggests."""
    share = factor_for(factors, "hvo", "WTW").value / GLEC_DIESEL_WTW

    assert 0.15 < share < 0.25
    assert factor_for(factors, "hvo", "TTW").value > 0, "trace CH4/N2O is not zero"


def test_electric_reproduces_the_energy_chain(factors):
    litres_per_tonne_km = GLEC_DIESEL_WTW / (DIESEL_TTW_PER_L + DIESEL_WTT_PER_L)
    kwh_per_tonne_km = (
        litres_per_tonne_km * MJ_PER_L_DIESEL * (EFFICIENCY_DIESEL / EFFICIENCY_ELECTRIC) / 3.6
    )

    for fuel, grid in (("electric_eu", 0.242), ("electric_de", 0.330),
                       ("electric_se", 0.035), ("electric_pl", 0.589)):
        expected = kwh_per_tonne_km * grid * GRID_LOSS
        assert factor_for(factors, fuel, "WTW").value == pytest.approx(expected, rel=1e-3)


def test_a_battery_truck_emits_nothing_at_the_kerb(factors):
    """TTW zero is correct and is exactly why it must not be used to compare fuels."""
    for fuel in ("electric_eu", "electric_de", "electric_se", "electric_pl"):
        assert factor_for(factors, fuel, "TTW").value == 0.0


def test_the_grid_decides_whether_electric_helps_at_all(factors):
    """The reason a single 'EU average' is refused: on the Polish grid a battery truck
    is within a few percent of diesel, while on the Swedish one it is a twentieth."""
    sweden = factor_for(factors, "electric_se", "WTW").value
    poland = factor_for(factors, "electric_pl", "WTW").value

    assert poland / GLEC_DIESEL_WTW > 0.85
    assert sweden / GLEC_DIESEL_WTW < 0.10
    assert poland / sweden > 10


def test_every_derived_row_is_marked_underived_and_names_its_source(factors):
    for factor_set in FACTOR_SETS:
        for fuel in ("hvo", "electric_eu", "electric_de", "electric_se", "electric_pl"):
            for scope in ("TTW", "WTW"):
                factor = factor_for(factors, fuel, scope, factor_set)
                assert not factor.is_verified, f"{fuel}/{scope} claims to be verified"
                assert "Derived" in factor.source or "Derived" in factor.notes


def test_the_feedstock_caveat_travels_with_the_hvo_figure(factors):
    """DEFRA publishes one generic HVO number; crop feedstock with land-use change can
    be several times worse. Losing that warning would make the row quietly optimistic."""
    notes = factor_for(factors, "hvo", "WTW").notes.lower()

    assert "feedstock" in notes
    assert "land-use" in notes or "land use" in notes


def test_the_electric_rows_admit_what_they_leave_out(factors):
    notes = factor_for(factors, "electric_eu", "WTW").notes.lower()

    assert "excludes upstream" in notes
    assert "understates" in notes


def test_no_fuel_row_exists_without_a_source(factors):
    """Turkey's grid and a waste/crop HVO split were both once carried with no citable
    source and were deleted rather than kept, because an uncited number in a tool that
    claims a standard is worse than a gap. Both are back now that primary sources were
    found -- Ember for the grid, JRC's RED II pathways for the feedstocks -- so what is
    guarded is the rule, not the absence."""
    invented = {"hvo_waste", "hvo_crop"}  # never had a source; superseded by the JRC rows
    fuels = {f.fuel_type for f in factors}

    assert not fuels & invented
    for factor in factors:
        assert factor.source.strip(), f"{factor.fuel_type}/{factor.scope} has no source"


def test_the_corridors_own_grid_is_covered(factors):
    """Turkiye is where the pilot corridor starts; an electric scenario that could not
    price its origin country was not much of a scenario."""
    turkiye = factor_for(factors, "electric_tr", "WTW")

    assert "Ember" in turkiye.source
    assert not turkiye.is_verified
    # Between Germany and Poland, which is where a ~400 g/kWh grid belongs.
    assert (factor_for(factors, "electric_de", "WTW").value
            < turkiye.value
            < factor_for(factors, "electric_pl", "WTW").value)


# JRC typical values, g CO2eq/MJ of final fuel, AR6 basis (Table 43 of the RED II
# pathway update). These are the primary figures the rows were built from.
JRC_TYPICAL = {
    "hvo_uco": 12.3,
    "hvo_tallow": 20.2,
    "hvo_rapeseed": 43.1,
    "hvo_palm": 65.2,
}


def test_each_feedstock_reproduces_its_jrc_pathway(factors):
    """Converted on GLEC's own fuel intensity, so the payload basis carries over."""
    diesel_wtw_per_litre = DIESEL_TTW_PER_L + DIESEL_WTT_PER_L
    mj_per_tonne_km = GLEC_DIESEL_WTW / diesel_wtw_per_litre * MJ_PER_L_DIESEL

    for fuel, g_per_mj in JRC_TYPICAL.items():
        expected = mj_per_tonne_km * g_per_mj / 1000
        assert factor_for(factors, fuel, "WTW").value == pytest.approx(expected, rel=1e-3)


def test_the_feedstock_decides_whether_hvo_is_worth_switching_to(factors):
    """The headline reason the split exists: waste cooking oil is a seventh of diesel,
    palm from an open effluent pond is nearly three quarters of it. One generic HVO
    figure hides a fivefold spread."""
    uco = factor_for(factors, "hvo_uco", "WTW").value
    palm = factor_for(factors, "hvo_palm", "WTW").value

    assert uco / GLEC_DIESEL_WTW < 0.20
    assert palm / GLEC_DIESEL_WTW > 0.65
    assert palm / uco > 4


def test_the_feedstocks_are_ordered_the_way_the_pathways_are(factors):
    ordered = [factor_for(factors, fuel, "WTW").value for fuel in JRC_TYPICAL]

    assert ordered == sorted(ordered), "a feedstock is out of order against JRC"


def test_combustion_does_not_vary_by_feedstock(factors):
    """Tank-to-wheel is the same molecule whatever it was made from: trace CH4 and N2O,
    with the biogenic CO2 reported out of scope."""
    values = {factor_for(factors, fuel, "TTW").value for fuel in JRC_TYPICAL}

    assert len(values) == 1
    assert 0 < values.pop() < 0.005


def test_every_feedstock_row_says_land_use_change_is_excluded(factors):
    """It is large enough to erase the saving for crop-grown feedstock, so a row that
    quietly omitted it would read as far better than it is."""
    for fuel in JRC_TYPICAL:
        notes = factor_for(factors, fuel, "WTW").notes.lower()
        assert "land-use" in notes or "land use" in notes
        assert "excludes" in notes


def test_the_generic_row_points_at_the_specific_ones(factors):
    """Whoever reaches for plain `hvo` should learn that the answer depends on something
    they can go and ask their supplier."""
    notes = factor_for(factors, "hvo", "WTW").notes.lower()

    assert "hvo_uco" in notes and "hvo_palm" in notes
    assert "unknown" in notes


def test_the_generic_figure_falls_inside_the_pathway_range(factors):
    """DEFRA's single number cross-checks against JRC: a mostly waste-based market should
    land between waste cooking oil and tallow. If it fell outside, one of the two sources
    would be being read wrong."""
    generic = factor_for(factors, "hvo", "WTW").value

    assert factor_for(factors, "hvo_uco", "WTW").value < generic
    assert generic < factor_for(factors, "hvo_tallow", "WTW").value

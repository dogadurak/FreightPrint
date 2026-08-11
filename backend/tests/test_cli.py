"""The command line, which had no tests at all while being a shipped entry point.

Coverage measured it at 0%: a hundred statements a user can reach and nothing that
would notice them breaking. Two of the things checked here — that the printed column
adds up to the printed total, and that the quoted range contains the quoted figure —
are what a reader does with the output the moment they see it.
"""

import json
from pathlib import Path

import pytest

from app.core import route as route_module
from app.core.road import RoadRoute, RoadRoutingError

CORRIDOR_ROADS = json.loads(
    (Path(__file__).parent / "fixtures" / "corridor_roads.json").read_text(encoding="utf-8")
)
GEBZE, DUSSELDORF = "29.4306,40.7889", "6.7735,51.2277"


@pytest.fixture(autouse=True)
def recorded_roads(monkeypatch):
    def replay(origin, destination):
        leg = CORRIDOR_ROADS[f"{origin[0]},{origin[1]}|{destination[0]},{destination[1]}"]
        return RoadRoute(
            distance_km=leg["distance_km"], duration_h=leg["duration_h"],
            ferry_km=leg["ferry_km"], geometry=tuple(map(tuple, leg["geometry"])),
        )

    monkeypatch.setattr(route_module, "road_route", replay)


def run(capsys, *argv) -> str:
    """Run the CLI as a user would and hand back what they would see."""
    import sys

    from app import cli

    original = sys.argv
    sys.argv = ["freightprint", *argv]
    try:
        cli.main()
    finally:
        sys.argv = original
    return capsys.readouterr().out


def _corridor(capsys, *extra):
    return run(capsys, "--origin", GEBZE, "--destination", DUSSELDORF, *extra)


def test_it_prices_a_corridor_and_names_the_basis(capsys):
    output = _corridor(capsys, "--scope", "WTW", "--factor-set", "glec")

    assert "Faktor seti: glec | kapsam: WTW" in output
    assert "GLEC Framework" in output, "a figure without its source cannot be checked"
    assert "all-road" in output


def test_the_printed_legs_add_up_to_the_printed_total(capsys):
    """An all-road route is one leg, so its leg and its total are the same number and
    must print as the same number. They did not: the leg was written at full precision
    under a total cut to three significant figures, so 4,527 sat above 4,530."""
    output = _corridor(capsys, "--scope", "WTW", "--factor-set", "glec")

    block = output.split("=== all-road")[1].split("===")[0]
    legs = [line for line in block.splitlines() if " km " in line and "TOPLAM" not in line]
    total_line = next(line for line in block.splitlines() if "TOPLAM" in line)

    read = lambda line: float(line.rsplit("km", 1)[1].replace("kg CO2", "").replace(",", ""))
    assert len(legs) == 1, "the all-road baseline should be a single road leg"
    assert read(legs[0]) == read(total_line)


def test_the_quoted_range_contains_the_quoted_figure(capsys):
    """The defect this whole audit started from, at the surface a user reads."""
    output = _corridor(capsys, "--scope", "WTW", "--factor-set", "glec")

    block = output.split("=== all-road")[1].split("===")[0]
    total = float(
        next(line for line in block.splitlines() if "TOPLAM" in line)
        .rsplit("km", 1)[1].replace("kg CO2", "").replace(",", "")
    )
    band = next(line for line in block.splitlines() if "belirsizlik araligi" in line)
    low, high = (float(part.replace("kg CO2", "").replace(",", ""))
                 for part in band.split(":")[1].split("-"))

    assert low <= total <= high, f"{total} is outside the range printed beside it"


def test_alternatives_are_ranked_by_emissions_not_distance(capsys):
    """The shortest alternative is regularly not the cleanest; ranking by distance would
    drop the lowest-emission option before it was ever shown."""
    output = _corridor(capsys, "--scope", "WTW", "--factor-set", "glec")

    totals = [
        float(line.rsplit("km", 1)[1].replace("kg CO2", "").replace(",", ""))
        for line in output.splitlines()
        if "TOPLAM" in line
    ]

    assert totals[0] == pytest.approx(4530, rel=0.01), "the baseline is reported first"
    assert totals[1:] == sorted(totals[1:]), "alternatives are not in emission order"


def test_the_factor_set_changes_the_sign_of_the_answer(capsys):
    """The finding the project rests on, reachable from the command line: the same
    corridor saves carbon on one published basis and loses it on another."""
    reference = _corridor(capsys, "--factor-set", "reference")
    glec = _corridor(capsys, "--scope", "WTW", "--factor-set", "glec")

    def best_saving(output):
        savings = [
            float(line.rsplit(" ", 3)[1].replace(",", ""))
            for line in output.splitlines() if "tasarruf" in line
        ]
        return max(savings)

    assert best_saving(reference) > 0, "reference factors favour the multimodal option"
    assert best_saving(glec) < 0, "GLEC factors turn the same corridor into a loss"


def test_listing_fuels_needs_no_route(capsys):
    """It answers a question about the factor file, so demanding an origin would be rude."""
    output = run(capsys, "--list-fuels", "--factor-set", "glec")

    assert "diesel_b5" in output and "varsayilan" in output
    assert "hvo_uco" in output and "turetme" in output, "derived rows must say so"


def test_a_route_needs_both_endpoints():
    with pytest.raises(SystemExit) as exit_info:
        run(None, "--origin", GEBZE)

    assert "--destination" in str(exit_info.value)


@pytest.mark.parametrize("point", ["29.4306", "not,a,point", "999,999"])
def test_an_impossible_point_is_refused_before_any_routing(point):
    with pytest.raises(SystemExit):
        run(None, "--origin", point, "--destination", DUSSELDORF)


def test_an_unroutable_origin_reports_why_rather_than_stack_tracing(monkeypatch):
    def refuse(origin, destination):
        raise RoadRoutingError("no road access")

    monkeypatch.setattr(route_module, "road_route", refuse)

    with pytest.raises(SystemExit) as exit_info:
        run(None, "--origin", GEBZE, "--destination", DUSSELDORF)

    assert "Rota bulunamadi" in str(exit_info.value)


def test_an_unknown_fuel_names_what_the_set_does_offer(capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(None, "--origin", GEBZE, "--destination", DUSSELDORF,
            "--factor-set", "glec", "--fuel", "diesel")

    message = str(exit_info.value)
    assert "diesel_b5" in message, "a rejected guess should be told the real name"


def test_a_load_factor_prices_the_point_at_the_middle_of_its_own_band(capsys):
    """Quoting the figure at the requested utilisation while the band is clipped below
    it puts the headline outside its own range."""
    output = _corridor(
        capsys, "--scope", "WTW", "--factor-set", "glec",
        "--load-factor", "1.0", "--load-uncertainty", "0.3",
    )

    assert "doluluk: 0.70-1.00" in output
    block = output.split("=== all-road")[1].split("===")[0]
    total = float(
        next(line for line in block.splitlines() if "TOPLAM" in line)
        .rsplit("km", 1)[1].replace("kg CO2", "").replace(",", "")
    )
    band = next(line for line in block.splitlines() if "belirsizlik araligi" in line)
    low, high = (float(part.replace("kg CO2", "").replace(",", ""))
                 for part in band.split(":")[1].split("-"))

    assert low <= total <= high


def test_an_unverified_factor_is_announced(capsys):
    """A derived factor may be used; it may not be used quietly."""
    output = _corridor(capsys, "--factor-set", "glec", "--fuel", "hvo_uco")

    assert "unverified factor used" in output

"""Rules the stylesheet has to keep that no Python import would ever notice.

There is no browser here, so these read the files as text. That is enough for the small
number of places where a CSS property silently breaks a JavaScript library's contract —
which has now happened twice in the same afternoon and both times looked like a routing
bug rather than a styling one.
"""

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@pytest.fixture(scope="module")
def stylesheet() -> str:
    return (FRONTEND / "style.css").read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    """The declarations of the rule whose selector is exactly `selector`."""
    match = re.search(rf"(?m)^\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    return match.group(1) if match else ""


@pytest.mark.parametrize("prop", ["position", "transform"])
def test_the_map_marker_leaves_maplibre_its_own_properties(stylesheet, prop):
    """MapLibre places a marker by writing `transform` every frame onto an element it
    has set to `position: absolute`. Setting either here detaches the marker from its
    coordinate: `transform` made the vehicles leap about the screen, and `position`
    dropped the ship a few hundred kilometres inland of Istanbul. Ours go on the inner
    `.play-vehicle` wrapper instead.
    """
    declarations = _block(stylesheet, ".play-marker")

    assert declarations, ".play-marker rule not found; this test is no longer checking anything"
    assert not re.search(rf"(?m)^\s*{prop}\s*:", declarations), (
        f".play-marker sets {prop}, which belongs to MapLibre"
    )


def test_the_vehicle_wrapper_exists_to_carry_them(stylesheet):
    """The rule above is only safe because there is somewhere else for motion to live."""
    wrapper = _block(stylesheet, ".play-vehicle")

    assert "position" in wrapper
    assert "animation" in wrapper


def test_the_player_reads_the_ids_the_page_provides(stylesheet):
    """A renamed control fails silently in the browser: the listener is simply never
    attached and the button does nothing."""
    page = (FRONTEND / "index.html").read_text(encoding="utf-8")
    script = (FRONTEND / "app.js").read_text(encoding="utf-8")

    for element_id in ("player", "player-play", "player-scrub", "player-speed",
                       "player-readout", "player-track", "road-fuel", "catchment-toggle"):
        assert f'id="{element_id}"' in page, f"{element_id} is missing from the page"
        assert f'$("{element_id}")' in script, f"{element_id} is never read by the script"

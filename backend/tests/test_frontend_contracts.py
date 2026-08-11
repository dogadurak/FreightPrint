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


@pytest.fixture(scope="module")
def page() -> str:
    return (FRONTEND / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script() -> str:
    return (FRONTEND / "app.js").read_text(encoding="utf-8")


def test_the_player_reads_the_ids_the_page_provides(page, script):
    """A renamed control fails silently in the browser: the listener is simply never
    attached and the button does nothing."""
    for element_id in ("player", "player-play", "player-scrub", "player-speed",
                       "player-readout", "player-track", "road-fuel", "catchment-toggle"):
        assert f'id="{element_id}"' in page, f"{element_id} is missing from the page"
        assert f'$("{element_id}")' in script, f"{element_id} is never read by the script"


def test_every_element_the_script_looks_up_exists(page, script):
    """The general form of the rule above, so it keeps holding for controls added later.

    `document.getElementById` returns null rather than throwing, so a mistyped or
    removed id surfaces as a dead button or a `Cannot read properties of null` deep in
    an unrelated handler — never as anything pointing at the markup.
    """
    looked_up = set(re.findall(r'\$\("([^"]+)"\)', script))
    present = set(re.findall(r'id="([^"]+)"', page))

    assert looked_up <= present, f"read by the script, absent from the page: {looked_up - present}"


def test_the_terminal_picker_exists_for_both_endpoints(page, script):
    """These ids are assembled from a template literal, so the check above cannot see
    them — and `getElementById` returning null is silent, so a rename in the markup
    would show up only as a caret that never opens.

    Both endpoints, because the picker is set up per endpoint and one of them missing
    would leave a button wired to nothing on exactly one half of the form.
    """
    assert "$(`terminals-${kind}`)" in script, "the picker no longer builds ids this way"
    assert "$(`terminal-list-${kind}`)" in script

    for kind in ("origin", "destination"):
        assert f'id="terminals-{kind}"' in page, f"no terminal caret for {kind}"
        assert f'id="terminal-list-{kind}"' in page, f"no terminal list for {kind}"

    assert 'setUpTerminalPicker("origin")' in script
    assert 'setUpTerminalPicker("destination")' in script


def test_the_terminal_list_hangs_off_the_field_it_belongs_to(stylesheet, page):
    """It is positioned `top: 100%`, which is the bottom of its offset parent. If the
    list ever moves out of `.name-row`, that parent becomes some outer block and the
    dropdown opens somewhere other than under its own field."""
    for kind in ("origin", "destination"):
        start = page.index('<div class="name-row">', page.index(f'name="{kind}_name"') - 400)
        between = page[start:page.index(f'id="terminal-list-{kind}"', start)]
        assert "</div>" not in between, (
            f"the {kind} terminal list sits outside its own name row"
        )

    row = _block(stylesheet, ".name-row")
    assert "position: relative" in row, "the list would anchor to some outer block"


def test_every_form_field_the_script_reads_exists(page, script):
    """The shipment form is built from `name` attributes, and a missing one is silent:
    `FormData.get` returns null and the field is simply dropped from the request, so the
    engine prices a shipment the user did not describe."""
    read = set(re.findall(r'data\.get\("([^"]+)"\)', script))
    read |= set(re.findall(r'form\.elements\.([A-Za-z_]\w*)', script))
    read |= set(re.findall(r'form\.elements\["([^"]+)"\]', script))
    present = set(re.findall(r'name="([^"]+)"', page))

    assert read <= present, f"read by the script, absent from the page: {read - present}"


def test_the_route_input_anchors_its_own_swap_button(stylesheet):
    """`.swap-btn` is absolutely positioned at the middle of the route block, which is
    where the destination's controls are — so the block both anchors it and reserves a
    lane for it. Without `position: relative` the button anchors to the page instead and
    lands in a corner; without the right padding it sits on top of "haritadan seç"."""
    block = _block(stylesheet, ".route-input")
    button = _block(stylesheet, ".swap-btn")

    assert "position: relative" in block, "the swap button would anchor to the page"
    assert "position: absolute" in button
    padding = re.search(r"padding:\s*([^;]+);", block)
    assert padding, ".route-input declares no padding to reserve the button's lane"
    parts = padding.group(1).split()
    assert len(parts) == 4 and parts[1] != parts[3], (
        f"padding {padding.group(1)!r} reserves no extra room on the right"
    )

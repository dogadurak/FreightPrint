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


def test_picking_an_endpoint_can_reveal_the_map_before_any_results(page, script, stylesheet):
    """The map sits inside the results dashboard, and that dashboard starts `hidden`.

    So before the first calculation, "haritadan seç" armed a map that was not on screen:
    the button changed colour and, as far as the user could tell, did nothing else. But
    choosing where the freight starts is the step *before* calculating, so the map has
    to be reachable first. `map-only` is what reveals it, and losing either half of that
    — the class or the rules — brings the dead button back without a sound.
    """
    assert '<main class="dashboard" id="dashboard" hidden>' in page, (
        "the dashboard no longer starts hidden; this test is checking nothing"
    )
    assert 'id="map"' in page and page.index('id="dashboard"') < page.index('id="map"'), (
        "the map is no longer inside the dashboard"
    )
    assert 'classList.add("map-only")' in script, "nothing reveals the map for picking"
    assert _block(stylesheet, ".dashboard.map-only > *:not(.panel-grid)"), (
        "map-only has no rule hiding the sections that have no numbers yet"
    )


def test_armed_picking_can_be_left_from_the_keyboard(script):
    """A mode with no way out is a trap, and this one changes what a map click does."""
    assert '"Escape"' in script and "setPicking(null)" in script


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


# Words this engine does not get to use about its own output. The brief's section 3 is
# explicit that it is not an audit tool and its figures are not a verdict on anyone, and
# the ISO 14083 module opens by saying it certifies nothing. A control labelled with one
# of these contradicts both, and a reviewer finds that before they find anything else.
FORBIDDEN_CLAIMS = ("sertifika", "certificate", "certified", "belgelendir", "onaylı rapor")


def test_the_interface_never_claims_to_certify(page, script):
    """A "Karbon Sertifikası" button once screenshotted the dashboard and saved it under
    that name. The engine does not certify anything and says so in the one module that
    would know, so nothing on screen may say otherwise."""
    # The rule is about what a reader sees, so comments are exempt — one of them exists
    # precisely to record why the word is banned.
    comment = ("/*", "*", "//", "<!--", "#")

    for surface, text in (("index.html", page), ("app.js", script)):
        for claim in FORBIDDEN_CLAIMS:
            offending = [
                line for line in text.splitlines()
                if claim in line.lower() and not line.strip().startswith(comment)
            ]
            assert not offending, (
                f"{surface} claims to {claim}: {offending[0].strip()[:90]}"
            )


def test_the_page_loads_no_script_it_does_not_use(page, script):
    """A CDN tag is a network dependency, a supply-chain surface and a slower first
    paint. `turf.min.js` was fetched on every load and referenced nowhere; html2pdf was
    900 KB serving one button that has since gone."""
    tags = re.findall(r'<script src="(https?://[^"]+)"', page)

    for url in tags:
        library = url.rsplit("/", 1)[-1].split(".")[0].split("-")[0]
        assert library in script, (
            f"{url} is loaded on every page and never used"
        )


def test_every_panel_the_page_offers_is_actually_rendered(page, script):
    """A card in the markup with nothing calling its renderer is a panel that never
    appears, and nothing errors — it simply stays `hidden` forever.

    This has now happened twice. The benchmark module was written, tested and reachable
    from nowhere; the panel for it was added and, for a moment, called by nobody. Both
    times the code was correct and the feature did not exist.
    """
    cards = set(re.findall(r'id="([a-z-]+)-card"', page))
    assert cards, "no result cards found; this test is checking nothing"

    for card in cards:
        # Every card is revealed by something that also renders into it.
        assert f'$("{card}-card")' in script, f"{card}-card is never shown or hidden"


@pytest.mark.parametrize(
    "renderer",
    ["renderEmptyRunning", "renderVulnerability", "renderBackhaul", "renderHubPlan"],
)
def test_a_renderer_that_exists_is_a_renderer_that_is_called(script, renderer):
    """Defining it is half the work; the half that makes it a feature is the call."""
    assert f"function {renderer}(" in script, f"{renderer} is gone"

    calls = [
        line for line in script.splitlines()
        if f"{renderer}(" in line and not line.strip().startswith(("function", "*", "//"))
    ]
    assert calls, f"{renderer} is defined and never called"

"""Does the dashboard's script survive being loaded at all?

Nothing in a Python suite can notice a JavaScript error, and the failure mode is
unusually quiet: when app.js throws at module scope the browser reports it only in a
console nobody has open, no listener is ever attached, and every form silently falls
back to a native submission. That is how a stray `});` had the shipment form sending
`GET /?origin=...&tonnage=24` instead of `POST /api/routes` — the page rendered
perfectly and the button appeared to do nothing.

So this runs the real file through a real JavaScript engine against a DOM stubbed from
the real markup. It needs an engine: `node` if one is installed, otherwise the one
inside VS Code, which is present on most machines that edit this project. CI always has
node, so the check is never skipped where it matters.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check_frontend_boots.js"

# VS Code ships an Electron that runs as node when asked. Worth reaching for so the
# guard works on a developer's machine and not only in CI.
VSCODE = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/Code.exe",
    Path("/usr/share/code/code"),
    Path("/Applications/Visual Studio Code.app/Contents/MacOS/Electron"),
]


def _engine() -> tuple[list[str], dict] | None:
    node = shutil.which("node")
    if node:
        return [node], {}
    for candidate in VSCODE:
        if candidate.is_file():
            return [str(candidate)], {"ELECTRON_RUN_AS_NODE": "1"}
    return None


def test_the_dashboard_script_loads_without_throwing():
    engine = _engine()
    if engine is None:
        pytest.skip("no JavaScript engine found (node or VS Code); CI always has node")

    command, extra_env = engine
    result = subprocess.run(
        [*command, str(CHECK)],
        capture_output=True,
        text=True,
        env={**os.environ, **extra_env},
        cwd=REPO,
        timeout=120,
    )

    assert result.returncode == 0, (
        "frontend/app.js would throw in a browser, which silently disables every "
        f"control on the page:\n{result.stdout}\n{result.stderr}"
    )

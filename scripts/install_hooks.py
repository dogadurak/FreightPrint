"""Install the pre-commit hook that runs the customer-data checks.

The privacy check has caught real leaks twice, both times values that looked like
ordinary test fixtures. Remembering to run it by hand is not a control, so this wires
it into git.

    python scripts/install_hooks.py

Re-running is safe. An existing hook is left alone and reported rather than replaced.
"""

import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

HOOK = """#!/bin/sh
# Installed by scripts/install_hooks.py

# Runs against the staged diff, which is what is about to become a commit.
python scripts/check_privacy.py || exit 1
python scripts/check_tracked_files.py || exit 1
"""


def hooks_dir() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise SystemExit("not a git repository")
    # The path git reports is relative to the repository root, not to the caller.
    return (REPO_ROOT / result.stdout.strip()).resolve()


def main() -> int:
    directory = hooks_dir()
    directory.mkdir(parents=True, exist_ok=True)
    hook = directory / "pre-commit"

    if hook.exists() and HOOK.strip() not in hook.read_text(encoding="utf-8"):
        print(f"{hook} zaten var ve farkli; dokunulmadi.")
        print("Icerigine su iki satiri elle ekleyin:")
        print("  python scripts/check_privacy.py || exit 1")
        print("  python scripts/check_tracked_files.py || exit 1")
        return 1

    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"kuruldu: {hook}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

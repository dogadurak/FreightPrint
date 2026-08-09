"""Refuse to let a sensitive file become tracked.

`check_privacy.py` reads the validation dataset to search diffs for its values, so it
can only run where that file exists — on a developer's machine, never in CI. This one
needs nothing but the repository, so it is the check CI can actually enforce: it asks
whether any file that must stay untracked has been added to the index.

    python scripts/check_tracked_files.py

Exits non-zero when one is found.
"""

import fnmatch
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Customer data, and the caches that are derived from it. A cache is not obviously
# sensitive until you notice geocode_cache.json holds every address ever looked up.
FORBIDDEN = (
    "dogrulama_veriseti.csv",
    "*.xlsx",
    "*.xls",
    "data/geocode_cache.json",
    "data/route_cache.sqlite",
    "osrm-data/*",
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode != 0:
        raise SystemExit(f"git ls-files failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def notebooks_carrying_output(tracked: list[str]) -> list[tuple[str, str]]:
    """Notebook source is harmless; its stored output is not.

    The validation notebook reads the customer dataset, so a committed run would carry
    real city names and figures in its cells. The source itself gives nothing away, so
    the rule is about outputs rather than about the file.
    """
    findings = []
    for path in tracked:
        if not path.endswith(".ipynb"):
            continue
        try:
            notebook = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if any(cell.get("outputs") for cell in notebook.get("cells", [])):
            findings.append((path, "committed with cell output"))
    return findings


def main() -> int:
    tracked = tracked_files()
    findings = [
        (path, pattern)
        for path in tracked
        for pattern in FORBIDDEN
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern)
    ]
    findings += notebooks_carrying_output(tracked)

    if findings:
        print(f"TAKIP EDILMEMESI GEREKEN DOSYA: {len(findings)} adet")
        for path, pattern in findings:
            print(f"  {path}  (kural: {pattern})")
        print("\n.gitignore'a ekleyin ve 'git rm --cached <dosya>' ile indeksten cikarin.")
        return 1

    print(f"temiz ({len(tracked)} takipli dosya, {len(FORBIDDEN)} kurala karsi tarandi)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

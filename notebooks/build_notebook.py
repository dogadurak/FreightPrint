"""Build validation_analysis.ipynb from its paired script.

The notebook is generated without outputs on purpose: executing it locally reproduces the
figures from `dogrulama_veriseti.csv`, but the committed file never carries customer
routes, city names or tonnages.
"""

import json
from pathlib import Path

NOTEBOOK_DIR = Path(__file__).resolve().parent
SOURCE = NOTEBOOK_DIR / "validation_analysis.py"
TARGET = NOTEBOOK_DIR / "validation_analysis.ipynb"

CODE_MARKER = "# %%"
MARKDOWN_MARKER = "# %% [markdown]"


def _split_cells(text: str) -> list[tuple[str, list[str]]]:
    cells: list[tuple[str, list[str]]] = []
    kind = "code"
    body: list[str] = []

    for line in text.splitlines():
        if line.startswith(CODE_MARKER):
            if any(entry.strip() for entry in body):
                cells.append((kind, body))
            kind = "markdown" if line.startswith(MARKDOWN_MARKER) else "code"
            body = []
        else:
            body.append(line)

    if any(entry.strip() for entry in body):
        cells.append((kind, body))
    return cells


def _strip_comment_prefix(lines: list[str]) -> list[str]:
    return [line[2:] if line.startswith("# ") else line.removeprefix("#") for line in lines]


def _to_source(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return [f"{line}\n" for line in lines[:-1]] + lines[-1:] if lines else []


def build() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    # Drop the module docstring; it explains the pairing, not the analysis.
    _, _, body = text.partition('"""\n\n')

    cells = []
    for index, (kind, lines) in enumerate(_split_cells(body)):
        source = _to_source(_strip_comment_prefix(lines) if kind == "markdown" else lines)
        cell = {"id": f"cell-{index:02d}", "cell_type": kind, "metadata": {}, "source": source}
        if kind == "code":
            cell |= {"execution_count": None, "outputs": []}
        cells.append(cell)

    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    TARGET.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{TARGET.name}: {len(cells)} hucre yazildi")


if __name__ == "__main__":
    build()

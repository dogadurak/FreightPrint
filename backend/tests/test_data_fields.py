"""The guard against this project's most persistent defect.

Not a wrong number — a right number that reaches nobody. Three were found by hand in one
afternoon: `is_measured` parsed and read by nothing, the per-mode uncertainty table
switched off by a default, `valid_to` on a risk zone honoured by nothing. Each had been
built, measured and documented first.

`scripts/check_data_fields.py` looks for the signature: a field mentioned twice, once to
declare it and once to parse it, and never again. These tests make sure it can still
fail, because a guard that cannot fail is decoration.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_data_fields", REPO / "scripts" / "check_data_fields.py"
)
check_data_fields = importlib.util.module_from_spec(spec)
sys.modules["check_data_fields"] = check_data_fields
spec.loader.exec_module(check_data_fields)


def test_every_field_in_data_is_used_or_excused_in_writing():
    """The check itself. A new column that nothing reads fails the build."""
    assert check_data_fields.main() == 0


def test_a_field_nothing_mentions_is_found():
    """The guard has to be able to fail."""
    assert check_data_fields.mentions("a_column_no_code_has_ever_heard_of") == []


def test_a_field_the_code_really_uses_is_not_flagged():
    """`eur_per_tonne_co2` is parsed and then multiplied into the toll a few lines below.
    An earlier version of this check counted files rather than mentions and flagged it,
    which would have taught everyone to ignore the check."""
    found = check_data_fields.mentions("eur_per_tonne_co2")

    assert len(found) > check_data_fields.DEAD_AT_OR_BELOW


@pytest.mark.parametrize("field", ["is_measured", "valid_to"])
def test_the_fields_that_were_dead_are_now_alive(field):
    """Both were wired the day this check was written. If either drops back to two
    mentions it has been disconnected again, and that is exactly the regression this
    file exists to catch."""
    found = check_data_fields.mentions(field)

    assert len(found) > check_data_fields.DEAD_AT_OR_BELOW, (
        f"'{field}' is back to being parsed and never read: {found}"
    )


def test_every_excused_field_carries_a_reason():
    """The allowlist is the escape hatch, so the price of using it is saying why.
    A blank reason turns the guard into a list of fields somebody waved through."""
    for field, reason in check_data_fields.ALLOWED.items():
        assert reason.strip(), f"'{field}' is excused with no reason given"
        assert len(reason) > 12, f"'{field}': \"{reason}\" does not explain anything"


def test_the_allowlist_only_excuses_fields_that_exist():
    """An allowlist outlives the columns it was written for, and a stale entry silently
    widens the hole: the next field to take that name is excused before anyone looks at
    it. So every excuse has to point at a column that is really there.

    Note what this deliberately does *not* try to test — whether an excused field is
    secretly used after all. `mentions("notes")` matches every local variable called
    `notes` in the codebase, and there are many; the check would report agreement it had
    not established. Asking a question the method cannot answer is how a guard starts
    lying.
    """
    import csv
    import json

    columns = set()
    for path in (REPO / "data").rglob("*.csv"):
        with path.open(encoding="utf-8") as f:
            columns.update(h.strip() for h in next(csv.reader(f), []))
    for path in (REPO / "data").rglob("*.geojson"):
        for feature in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            columns.update(feature.get("properties", {}))

    orphans = sorted(set(check_data_fields.ALLOWED) - columns)

    assert not orphans, f"excused but no data file has this column any more: {orphans}"

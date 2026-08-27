"""One default for the factor set, in one place, reachable every way in.

`emissions.py` declares `DEFAULT_FACTOR_SET = "glec"` and every core function honours it.
The API did not: `RouteRequest` said `"reference"`, the two report endpoints said
`"reference"`, five others said `"glec"`, and none of them read the constant.

`"reference"` is the customer's own reported factors — road at 0.121 and sea at 0.012
kg CO2/ton-km — and it exists so Faz 3 can reproduce their report. It is not a set this
project defends; the note beside its sea row says the value is close to a container ship
despite the service being ro-ro. An API caller who named no set was priced with it.

**That default decided the sign of the headline finding.** On the pilot corridor the
multimodal route comes out 83% *better* than all-road under `reference`, and 19% *worse*
under `glec`. Nobody saw it, because the dashboard names its set explicitly and the
customer report was always run against `reference` on purpose — the only way in that used
the default was a bare API call.

The same defect was found and fixed in `cli.py` in an earlier round. The API half was
missed, which is why this is a test and not just a correction.
"""

import inspect

import pytest

from app.api import routes, schemas
from app.core import portfolio
from app.core.emissions import DEFAULT_FACTOR_SET, load_emission_factors


def test_the_engine_default_is_a_set_the_project_defends():
    """Whatever the constant points at must be published, not a single customer's."""
    sources = {f.source for f in load_emission_factors()
               if f.factor_set == DEFAULT_FACTOR_SET}
    assert sources, f"{DEFAULT_FACTOR_SET} kumesi bos"
    assert not any("Customer" in s for s in sources), (
        f"varsayilan faktor kumesi ({DEFAULT_FACTOR_SET}) musteri raporuna dayaniyor: "
        f"{sources}")


def test_the_request_schemas_take_the_default_from_the_engine():
    for name, model in vars(schemas).items():
        fields = getattr(model, "model_fields", None)
        if not fields or "factor_set" not in fields:
            continue
        field = fields["factor_set"]
        if field.is_required():
            continue  # A response model states the set that was used; it has no default.
        assert field.default == DEFAULT_FACTOR_SET, (
            f"{name}.factor_set varsayilani {field.default!r}, "
            f"DEFAULT_FACTOR_SET ise {DEFAULT_FACTOR_SET!r}")


@pytest.mark.parametrize("function", [
    obj for obj in vars(routes).values()
    if inspect.isfunction(obj) and "factor_set" in inspect.signature(obj).parameters
] + [portfolio.build_portfolio])
def test_every_endpoint_defaults_to_the_same_set(function):
    """A Form(...) default hides from the schema check above, so it is checked here."""
    default = inspect.signature(function).parameters["factor_set"].default
    value = getattr(default, "default", default)  # unwrap fastapi Form / Query
    if value is inspect.Parameter.empty:
        return
    assert value == DEFAULT_FACTOR_SET, (
        f"{function.__name__} varsayilani {value!r}, "
        f"DEFAULT_FACTOR_SET ise {DEFAULT_FACTOR_SET!r}")


def test_the_two_sets_disagree_enough_that_the_default_matters():
    """The guard above is only worth its runtime because the choice changes the answer.

    If these two ever converge, the test above stops defending anything and should be
    reconsidered rather than quietly kept.
    """
    factors = load_emission_factors()

    def value(factor_set: str, mode: str) -> float:
        rows = [f for f in factors if f.factor_set == factor_set and f.mode == mode
                and f.scope == "TTW" and f.is_default]
        assert rows, f"{factor_set}/{mode} yok"
        return rows[0].value

    # Road is twice as dirty and sea five times as clean under the customer's numbers,
    # which is exactly the combination that turns a multimodal penalty into a saving.
    assert value("reference", "road") > value("glec", "road") * 1.5
    assert value("reference", "sea") < value("glec", "sea") * 0.5

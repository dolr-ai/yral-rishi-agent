"""Surface targeting — the helper that decides which product sees a persona.

Requested by the amorae-web session 2026-08-10. The safety property under
test throughout: **nothing reaches the adult web surface without an
explicit opt-in**, and **nothing about mobile changes** until mobile asks.

The predicate lives in one module (app/services/surface.py) rather than
inline in the route, for the same reason the H2H list-vs-detail bug got
fixed in the shared helper: a second caller will otherwise get a subtly
different rule.
"""


# ─── normalize ──────────────────────────────────────────────────────────


def test_none_means_do_not_filter():
    """No ?surface= param → today's behaviour, byte for byte."""
    from services import surface

    assert surface.normalize(None) is None
    assert surface.visible_surfaces(None) is None


def test_known_surfaces_normalize():
    from services import surface

    assert surface.normalize("web") == "web"
    assert surface.normalize("mobile") == "mobile"
    assert surface.normalize("both") == "both"


def test_normalize_is_case_and_whitespace_tolerant():
    """amorae-web builds this from config; a stray space or capital must not
    change which catalogue comes back."""
    from services import surface

    assert surface.normalize(" WEB ") == "web"
    assert surface.normalize("Mobile") == "mobile"


def test_unknown_surface_normalizes_to_none():
    from services import surface

    assert surface.normalize("wbe") is None
    assert surface.normalize("") is None


# ─── visible_surfaces — the actual predicate ────────────────────────────


def test_web_sees_web_and_both_never_mobile():
    """The core guarantee. A mobile-only persona must be unreachable from
    the adult web surface."""
    from services import surface

    visible = surface.visible_surfaces("web")
    assert set(visible) == {"web", "both"}
    assert "mobile" not in visible


def test_mobile_sees_mobile_and_both_never_web():
    """The inverse guarantee: asking for mobile must never surface a
    web-only (adult) persona in the mainstream app."""
    from services import surface

    visible = surface.visible_surfaces("mobile")
    assert set(visible) == {"mobile", "both"}
    assert "web" not in visible


def test_both_is_a_single_value_match_not_everything():
    """'both' asks for the personas published to both products — it is not
    a wildcard. Treating it as "all" would leak mobile personas to web."""
    from services import surface

    assert set(surface.visible_surfaces("both")) == {"both"}


def test_unknown_surface_yields_no_filter_so_the_route_must_reject_it():
    """normalize() degrades rather than raising, which is why the ROUTE
    400s on an unknown surface. If the route ever stopped validating, a
    typo would return the unfiltered mainstream catalogue to amorae.ai —
    this test documents why that validation is load-bearing."""
    from services import surface

    assert surface.visible_surfaces("wbe") is None


def test_valid_surfaces_are_exactly_the_three():
    from services import surface

    assert set(surface.VALID_SURFACES) == {"mobile", "web", "both"}

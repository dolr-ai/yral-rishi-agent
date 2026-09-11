"""Which product surface a persona belongs to — one place that owns it.

amorae.ai (adult web) and the mobile app share one backend and one
ai_influencers catalogue. `ai_influencers.surface` says where each persona
belongs; this module is the only thing that interprets it.

Two rules, both learned the hard way:

1. **Opt-in filtering.** No `?surface=` param means NO filter, so today's
   callers keep today's behaviour byte-for-byte. Mobile is not silently
   re-scoped by a column it never asked about — the same "dormant until
   someone opts in" shape as MARKET_EXCLUSIVE_COUNTRIES.

2. **Filter discovery, never lookup.** This belongs on catalogue LISTINGS
   only. `GET /influencers/{id}` must keep resolving any persona for any
   caller, or every deep link into a web persona 404s for a mobile user
   and vice versa. Same failure shape as the H2H list-vs-detail bug: fix
   the shared helper, don't special-case one endpoint.

'both' exists so a persona can be listed in either product without
duplicating the row — which is why this is a containment question
(`surface IN (...)`) rather than equality.
"""

from __future__ import annotations

MOBILE = "mobile"
WEB = "web"
BOTH = "both"

VALID_SURFACES = (MOBILE, WEB, BOTH)

# Asking for one surface must also return the personas published to both.
# Keeping this as data rather than branching logic means adding a future
# surface is a one-line change here and nowhere else.
_VISIBLE_IN: dict[str, tuple[str, ...]] = {
    MOBILE: (MOBILE, BOTH),
    WEB: (WEB, BOTH),
}


def normalize(requested: str | None) -> str | None:
    """`?surface=` → a canonical surface, or None meaning "don't filter".

    Unknown values return None rather than raising: an unrecognised surface
    degrades to today's unfiltered behaviour instead of 500-ing a catalogue
    request. The caller validates and 400s if it wants strictness — see
    routes/influencers.py, which does exactly that so a typo'd amorae-web
    query is loud rather than silently returning the mainstream catalogue."""
    if requested is None:
        return None
    candidate = requested.strip().lower()
    return candidate if candidate in VALID_SURFACES else None


def visible_surfaces(requested: str | None) -> tuple[str, ...] | None:
    """The surface values a caller should see, or None for "no filter".

    Asking for 'both' explicitly is a request for the personas published to
    both products — not for everything — so it stays a single-value match.
    """
    surface = normalize(requested)
    if surface is None:
        return None
    return _VISIBLE_IN.get(surface, (surface,))

"""Phase 0 subscription stub — hardcoded YRAL-team allowlist.

Real billing.yral.com integration is a Phase 1 concern. For Phase 0
we ship the observable UX (blurred vs clear collage) using a
config-driven principal allowlist so the YRAL team can dogfood +
Rishi can validate the mobile blur/unblur flow end-to-end.

Contract: `is_subscribed(user_id) -> bool`. Same function name is
what the Phase 1 billing-service call site will retain, so the
route-level call doesn't churn on the swap."""

import config


def is_subscribed(user_id: str | None) -> bool:
    """True iff user_id is in the YRAL_TEAM_PRINCIPALS allowlist.
    None or empty user_id → False (unsubscribed by default)."""
    if not user_id:
        return False
    return user_id in config.YRAL_TEAM_PRINCIPALS

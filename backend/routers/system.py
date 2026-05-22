"""System metadata routes — deployment-mode banner.

Backs the login screen's deployment banner (UX-AUDIT F1). The banner used to
hardcode mil/gov classification framing that a stock open-source clone cannot
back; the mode now comes from the ``SENTINEL_DEPLOYMENT_MODE`` env var and
defaults to ``demo`` so a fresh deployment never implies accreditation.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter()

_VALID_MODES = ("demo", "internal", "accredited")

# Default banner text per mode. `internal` / `accredited` are normally
# overridden with a site-specific label via SENTINEL_DEPLOYMENT_LABEL.
_DEFAULT_LABELS = {
    "demo": "DEMO BUILD · NOT FOR OPERATIONAL USE",
    "internal": "INTERNAL DEPLOYMENT",
    "accredited": "ACCREDITED DEPLOYMENT",
}


@router.get("/api/system/deployment-mode")
def get_deployment_mode():
    """Return the deployment posture for the login banner.

    ``SENTINEL_DEPLOYMENT_MODE`` selects ``demo`` | ``internal`` |
    ``accredited``; anything unset or unrecognised falls back to ``demo``.
    ``SENTINEL_DEPLOYMENT_LABEL`` overrides the banner text — operators must
    opt in to a gov/mil banner explicitly. This route is intentionally
    unauthenticated: the login screen renders the banner before sign-in.
    """
    mode = (os.getenv("SENTINEL_DEPLOYMENT_MODE") or "demo").strip().lower()
    if mode not in _VALID_MODES:
        mode = "demo"
    label = os.getenv("SENTINEL_DEPLOYMENT_LABEL") or _DEFAULT_LABELS[mode]
    # Optional admin contact surfaced on the login screen for LDAP-enabled
    # deployments (UX-AUDIT F4). `None` when unset — the UI hides the line.
    support_contact = os.getenv("SENTINEL_AUTH_SUPPORT_CONTACT") or None
    return {"mode": mode, "label": label, "support_contact": support_contact}

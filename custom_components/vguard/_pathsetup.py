"""Ensure the vguard_client library is importable."""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_library_path() -> None:
    """Prefer a bundled copy, then a sibling checkout (dev layout)."""
    here = Path(__file__).resolve().parent

    # 1) Bundled: custom_components/vguard/vguard_client/
    bundled = here / "vguard_client"
    if bundled.is_dir() and str(here) not in sys.path:
        sys.path.insert(0, str(here))
        return

    # 2) Dev siblings: .../app/ha-vguard + .../app/vguard_client
    # here = .../ha-vguard/custom_components/vguard
    candidate = here.parents[2].parent / "vguard_client"
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

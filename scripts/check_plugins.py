"""CI smoke check — every installed plugin is discovered without failure.

Run after ``pip install -e`` of each bundled plugin (issue #185): it
builds the unified PluginHub and asserts no plugin record landed in the
FAILED state. LOCKED records (tier-gated) are reported but accepted —
that is the gate working as designed.

Exit code 0 = all good, 1 = at least one plugin failed to load.
"""

from __future__ import annotations

import sys

from core.plugin_hub import PluginHub
from core.plugin_model import PluginKind, PluginState


def main() -> int:
    records = PluginHub.get().records
    if not records:
        print("::warning::no plugin records discovered")
        return 0

    for rec in sorted(records, key=lambda r: (r.kind.value, r.name)):
        print(f"  {rec.kind.value:11} {rec.name:26} {rec.state.value:10} {rec.detail}")

    # Host-extension plugins ship in separately-installed packages
    # (e.g. gispulse-enterprise). A dangling extension entry-point is
    # that package's concern — warn, do not fail the bundled-plugin job.
    failed = [r for r in records if r.state is PluginState.FAILED]
    ext_failed = [r for r in failed if r.kind is PluginKind.EXTENSION]
    bundled_failed = [r for r in failed if r.kind is not PluginKind.EXTENSION]

    if ext_failed:
        print(f"::warning::{len(ext_failed)} host-extension plugin(s) unavailable: "
              f"{', '.join(r.name for r in ext_failed)}")
    if bundled_failed:
        print(f"::error::{len(bundled_failed)} bundled plugin(s) failed to load: "
              f"{', '.join(r.name for r in bundled_failed)}")
        return 1

    locked = sum(1 for r in records if r.state is PluginState.LOCKED)
    print(f"OK — {len(records)} plugin record(s), {locked} locked, "
          f"0 bundled failure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

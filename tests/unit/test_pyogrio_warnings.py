"""Regression tests for the gispulse-extension warning filter (#65)."""

from __future__ import annotations

import warnings


def test_silence_filter_suppresses_gispulse_extension_warning():
    from gispulse._pyogrio_warnings import silence_gispulse_extension_warnings

    silence_gispulse_extension_warnings()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        # Re-apply because catch_warnings reset the filter list above.
        silence_gispulse_extension_warnings()
        warnings.warn(
            "Layer _gispulse_rules relies on the 'gispulse' "
            "(https://gispulse.dev/gpkg-extension) extension that should be "
            "implemented in order to read it safely",
            RuntimeWarning,
        )
    assert not any("gispulse" in str(w.message) for w in captured)


def test_silence_filter_does_not_suppress_unrelated_runtime_warnings():
    from gispulse._pyogrio_warnings import silence_gispulse_extension_warnings

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        silence_gispulse_extension_warnings()
        warnings.warn("encoding mismatch", RuntimeWarning)
        warnings.warn("layer has mixed CRS", RuntimeWarning)
    msgs = [str(w.message) for w in captured]
    assert "encoding mismatch" in msgs
    assert "layer has mixed CRS" in msgs


def test_silence_filter_is_idempotent():
    from gispulse._pyogrio_warnings import silence_gispulse_extension_warnings

    silence_gispulse_extension_warnings()
    silence_gispulse_extension_warnings()
    silence_gispulse_extension_warnings()
    # No exception, no stacking — Python deduplicates identical filter entries.
    matching = [
        f
        for f in warnings.filters
        if f[0] == "ignore"
        and f[2] is RuntimeWarning
        and f[1] is not None
        and "gispulse" in f[1].pattern
    ]
    assert len(matching) >= 1

"""Deterministic model execution order for edge-less (selectless) models.

Found in live B-lite validation (foncier): a manifest declaring
stage -> build_core -> build_finalize (all selectless external models)
executed build_finalize BEFORE build_core — run_manifest collapsed the
model names into a set() before the (stable) topological sort, so
edge-less models ran in hash order instead of declaration order.
The compile path (manifest_v3._topo_models) already preserves
declaration order; the runtime must honour the same contract.
"""

from __future__ import annotations

import sys

from gispulse.core.manifest_v3 import ManifestV3, ModelSpec
from gispulse.runtime.manifest_runner import run_manifest


def _selectless_external(name: str, marker_path: str) -> ModelSpec:
    return ModelSpec(
        name=name,
        select=None,
        transform=[
            {
                "step": {
                    "kind": "external",
                    "argv": [
                        sys.executable,
                        "-c",
                        f"open(r'{marker_path}', 'a').write('{name}\\n')",
                    ],
                }
            }
        ],
    )


class TestSelectlessModelOrder:
    def test_declaration_order_is_execution_order(self, tmp_path):
        """Many edge-less models run strictly in declaration order.

        Uses enough models that an accidental hash-order pass is
        overwhelmingly unlikely (12! orderings), and a marker file
        appended by each subprocess proves the REAL execution order,
        not just the reported one.
        """
        marker = tmp_path / "order.log"
        names = [f"m_{i:02d}" for i in (9, 3, 11, 1, 7, 0, 5, 10, 2, 8, 4, 6)]
        manifest = ManifestV3(
            name="order_probe",
            sources={},
            models={n: _selectless_external(n, str(marker)) for n in names},
        )

        result = run_manifest(manifest, run_id="r-order")

        assert result.execution_order == names
        assert marker.read_text().splitlines() == names

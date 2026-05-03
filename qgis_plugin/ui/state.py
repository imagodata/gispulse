"""Qt-free state for the Attach-trigger dock widget.

Extracted from `dock_widget.py` so the validation rules (vector-only
layers, .yml/.yaml extension, "Run" button enable gate) can be unit
tested without a running QGIS instance.

The persistent layer↔rules mapping uses `QgsMapLayer.customProperty()`
under the key `CUSTOM_PROPERTY_KEY` so the assignment survives project
save/reload.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CUSTOM_PROPERTY_KEY = "gispulse/rules_yaml"
ALLOWED_RULES_SUFFIXES = (".yml", ".yaml")


@dataclass(frozen=True)
class ValidationOutcome:
    valid: bool
    message: str


def validate_rules_file(path: str | Path | None) -> ValidationOutcome:
    """The rules file must exist and be `.yml` / `.yaml`.

    Empty / None returns invalid with an empty message — the widget uses
    that to keep the inline label empty until the user actually picks
    something.
    """
    if not path:
        return ValidationOutcome(valid=False, message="")
    p = Path(path)
    if not p.is_file():
        return ValidationOutcome(valid=False, message=f"File not found: {p}")
    if p.suffix.lower() not in ALLOWED_RULES_SUFFIXES:
        return ValidationOutcome(
            valid=False,
            message=f"Rules file must end in .yml or .yaml (got {p.suffix!r}).",
        )
    return ValidationOutcome(valid=True, message="")


@dataclass
class AttachState:
    """Tracks dock-widget inputs and tells the widget when to enable Run.

    Layer types follow QGIS' `QgsMapLayer.LayerType` numeric values, but
    we only care about *vector* (=0). Anything else surfaces an inline
    "vector-only" message; the Run button stays disabled.
    """

    layer_id: str | None = None
    layer_is_vector: bool = False
    rules_path: str | None = None

    def set_layer(self, layer_id: str | None, *, is_vector: bool) -> None:
        self.layer_id = layer_id
        self.layer_is_vector = is_vector

    def set_rules_path(self, path: str | None) -> None:
        self.rules_path = path

    def rules_validation(self) -> ValidationOutcome:
        return validate_rules_file(self.rules_path)

    def layer_message(self) -> str:
        if self.layer_id is None:
            return ""
        if not self.layer_is_vector:
            return "GISPulse v1.4 supports vector layers only."
        return ""

    def can_run(self) -> bool:
        if self.layer_id is None or not self.layer_is_vector:
            return False
        return self.rules_validation().valid

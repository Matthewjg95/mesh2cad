"""Synthetic example assets for demos and tests.

SYNTHETIC: every dimension below is a round illustrative number chosen for
this demo. None of these assets describes a real product or a real
manufacturer drawing; do not use them as hardware references.
"""

from __future__ import annotations

from mra.sketch_assets.models import Arc, Circle, Line, Polygon, Profile, SketchAsset

_NOTICE = (
    "SYNTHETIC EXAMPLE - illustrative round-number dimensions; "
    "does not describe any real product."
)


def _provenance(builder: str) -> dict:
    return {
        "source": "synthetic-example",
        "generator": f"mra.sketch_assets.examples.{builder}",
        "notice": _NOTICE,
    }


def mounting_hole_pattern() -> SketchAsset:
    """Four Ø3.4 mm holes on a 50 x 30 mm rectangular pattern (schema 1.0)."""
    return SketchAsset(
        name="Synthetic 4-hole pattern 50x30",
        units="mm",
        circles=[
            Circle(center=(x, y), diameter=3.4, role="mounting-hole")
            for x, y in ((-25.0, -15.0), (25.0, -15.0), (25.0, 15.0), (-25.0, 15.0))
        ],
        metadata={
            "description": _NOTICE,
            "synthetic": True,
            "tags": ["synthetic-example", "hole-pattern", "mounting"],
        },
        provenance=_provenance("mounting_hole_pattern"),
    )


def rounded_plate() -> SketchAsset:
    """80 x 50 mm plate, R6 corners, four Ø3.4 holes and a 20 x 6 slot."""
    hw, hh, r = 40.0, 25.0, 6.0
    cx, cy = hw - r, hh - r
    outline = Profile(
        segments=(
            Line((hw, -cy), (hw, cy)),
            Arc((hw, cy), (cx, hh), center=(cx, cy)),
            Line((cx, hh), (-cx, hh)),
            Arc((-cx, hh), (-hw, cy), center=(-cx, cy)),
            Line((-hw, cy), (-hw, -cy)),
            Arc((-hw, -cy), (-cx, -hh), center=(-cx, -cy)),
            Line((-cx, -hh), (cx, -hh)),
            Arc((cx, -hh), (hw, -cy), center=(cx, -cy)),
        ),
        role="outline",
    )
    slot = Profile(
        segments=(
            Line((-7.0, -3.0), (7.0, -3.0)),
            Arc((7.0, -3.0), (7.0, 3.0), center=(7.0, 0.0)),
            Line((7.0, 3.0), (-7.0, 3.0)),
            Arc((-7.0, 3.0), (-7.0, -3.0), center=(-7.0, 0.0)),
        ),
        role="cutout",
    )
    return SketchAsset(
        name="Synthetic rounded plate 80x50 R6",
        units="mm",
        profiles=[outline, slot],
        circles=[
            Circle(center=(x, y), diameter=3.4, role="mounting-hole")
            for x, y in ((cx, cy), (-cx, cy), (-cx, -cy), (cx, -cy))
        ],
        metadata={
            "description": _NOTICE,
            "synthetic": True,
            "tags": ["synthetic-example", "plate", "rounded-rectangle"],
        },
        provenance=_provenance("rounded_plate"),
    )


def device_outline() -> SketchAsset:
    """Generic 2.0 x 3.5 in device outline, in INCHES.

    Origin at the bottom-left corner; R0.25 top corners; a keep-out zone
    (not a cut) for a connector area; two Ø0.125 in mounting holes.
    """
    outline = Profile(
        segments=(
            Line((0.0, 0.0), (2.0, 0.0)),
            Line((2.0, 0.0), (2.0, 3.25)),
            Arc((2.0, 3.25), (1.75, 3.5), center=(1.75, 3.25)),
            Line((1.75, 3.5), (0.25, 3.5)),
            Arc((0.25, 3.5), (0.0, 3.25), center=(0.25, 3.25)),
            Line((0.0, 3.25), (0.0, 0.0)),
        ),
        role="outline",
    )
    keep_out = Polygon(
        points=((0.5, 0.0), (1.5, 0.0), (1.5, 0.375), (0.5, 0.375)),
        role="keep-out",
    )
    return SketchAsset(
        name="Synthetic device outline 2.0x3.5in",
        units="in",
        profiles=[outline],
        polygons=[keep_out],
        circles=[
            Circle(center=(0.25, 3.0), diameter=0.125, role="mounting-hole"),
            Circle(center=(1.75, 3.0), diameter=0.125, role="mounting-hole"),
        ],
        metadata={
            "description": _NOTICE,
            "synthetic": True,
            "tags": ["synthetic-example", "device-outline", "imperial"],
        },
        provenance=_provenance("device_outline"),
    )


def all_examples() -> list[SketchAsset]:
    return [mounting_hole_pattern(), rounded_plate(), device_outline()]

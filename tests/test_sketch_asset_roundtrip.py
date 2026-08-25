"""Round-trip tests for the portable sketch-asset V1 schema."""

import json
import unittest

from mra.sketch_assets.models import Circle, Polygon, SketchAsset


class SketchAssetRoundTripTests(unittest.TestCase):
    def test_json_round_trip_preserves_v1_semantics(self) -> None:
        original = SketchAsset(
            name="UNO Q mounting interface",
            units="mm",
            origin=(2.5, -1.0),
            circles=[
                Circle(center=(3.2, 4.1), diameter=3.0, role="mounting-hole")
            ],
            polygons=[
                Polygon(
                    points=((0.0, 0.0), (68.6, 0.0), (68.6, 53.4), (0.0, 53.4)),
                    role="board-outline",
                )
            ],
            metadata={"tags": ["arduino", "mounting"]},
            provenance={"source": {"kind": "manufacturer-drawing", "page": 2}},
        )

        decoded = json.loads(json.dumps(original.to_dict()))
        restored = SketchAsset.from_dict(decoded)

        self.assertEqual(restored, original)

    def test_missing_optional_fields_receive_v1_defaults(self) -> None:
        restored = SketchAsset.from_dict({"name": "Minimal interface"})

        self.assertEqual(restored.units, "mm")
        self.assertEqual(restored.origin, (0.0, 0.0))
        self.assertEqual(restored.circles, [])
        self.assertEqual(restored.polygons, [])
        self.assertEqual(restored.schema_version, "1.0")

    def test_loaded_metadata_is_detached_from_caller(self) -> None:
        payload = {
            "name": "Detached",
            "metadata": {"nested": {"revision": "A"}},
        }

        restored = SketchAsset.from_dict(payload)
        payload["metadata"]["nested"]["revision"] = "B"

        self.assertEqual(restored.metadata["nested"]["revision"], "A")

    def test_rejects_unknown_fields_at_every_level(self) -> None:
        cases = (
            {"name": "Asset", "future_field": True},
            {
                "name": "Asset",
                "circles": [
                    {"center": [0.0, 0.0], "diameter": 3.0, "radius": 1.5}
                ],
            },
            {
                "name": "Asset",
                "polygons": [
                    {
                        "points": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
                        "closed": True,
                    }
                ],
            },
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "unknown fields"):
                    SketchAsset.from_dict(payload)

    def test_rejects_unsupported_schema_versions(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported sketch asset schema"):
            SketchAsset.from_dict({"name": "Future", "schema_version": "2.0"})

    def test_rejects_malformed_primitive_records(self) -> None:
        cases = (
            {"name": "Bad", "circles": {}},
            {"name": "Bad", "circles": [{"center": [0.0, 0.0]}]},
            {"name": "Bad", "polygons": [{"points": "not-an-array"}]},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    SketchAsset.from_dict(payload)


if __name__ == "__main__":
    unittest.main()

"""Contract tests for the portable, CAD-kernel-free sketch schema."""

import json
import unittest
from dataclasses import FrozenInstanceError

from mra.sketch_assets import Circle, Polygon, SketchAsset


class SketchAssetContractTests(unittest.TestCase):
    def test_public_package_import_exposes_v1_schema(self) -> None:
        asset = SketchAsset(
            name="UNO Q mounting interface",
            circles=[Circle(center=(0.0, 0.0), diameter=3.2)],
            polygons=[
                Polygon(
                    points=((0.0, 0.0), (68.6, 0.0), (68.6, 53.4), (0.0, 53.4))
                )
            ],
        )

        self.assertEqual(asset.name, "UNO Q mounting interface")
        self.assertEqual(asset.circles[0].role, "hole")
        self.assertEqual(asset.polygons[0].role, "outline")

    def test_asset_serializes_as_json_without_cad_dependencies(self) -> None:
        asset = SketchAsset(
            name="Four-hole pattern",
            units="mm",
            origin=(10.0, 20.0),
            circles=[
                Circle(center=(0.0, 0.0), diameter=3.0),
                Circle(center=(40.0, 0.0), diameter=3.0),
            ],
            metadata={"interface": "mounting"},
            provenance={"source": "manufacturer drawing"},
        )

        payload = asset.to_dict()
        encoded = json.dumps(payload)

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["circles"][1]["center"], (40.0, 0.0))
        self.assertIn("manufacturer drawing", encoded)

    def test_mutable_defaults_are_not_shared_between_assets(self) -> None:
        first = SketchAsset(name="First")
        second = SketchAsset(name="Second")

        first.metadata["owner"] = "first"
        first.circles.append(Circle(center=(0.0, 0.0), diameter=1.0))

        self.assertEqual(second.metadata, {})
        self.assertEqual(second.circles, [])

    def test_rejects_invalid_required_geometry(self) -> None:
        with self.assertRaisesRegex(ValueError, "asset name"):
            SketchAsset(name="   ")
        with self.assertRaisesRegex(ValueError, "units"):
            SketchAsset(name="Bad units", units="cm")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "diameter"):
            Circle(center=(0.0, 0.0), diameter=0.0)
        with self.assertRaisesRegex(ValueError, "three points"):
            Polygon(points=((0.0, 0.0), (1.0, 1.0)))

    def test_primitives_are_immutable_value_objects(self) -> None:
        circle = Circle(center=(1.0, 2.0), diameter=3.0)

        with self.assertRaises(FrozenInstanceError):
            circle.diameter = 4.0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()

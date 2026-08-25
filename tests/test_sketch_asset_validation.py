"""Validation tests for portable sketch assets."""

import math
import unittest

from mra.sketch_assets.models import Circle, Polygon, SketchAsset


class SketchAssetValidationTests(unittest.TestCase):
    def test_accepts_valid_engineering_geometry(self) -> None:
        outline = Polygon(
            points=((0.0, 0.0), (68.6, 0.0), (68.6, 53.4), (0.0, 53.4))
        )
        asset = SketchAsset(
            name="Board interface",
            origin=(1.0, -2.0),
            circles=[Circle(center=(3.0, 4.0), diameter=3.2)],
            polygons=[outline],
        )

        self.assertEqual(asset.units, "mm")

    def test_rejects_non_finite_geometry(self) -> None:
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "finite"):
                    Circle(center=(invalid, 0.0), diameter=1.0)
                with self.assertRaisesRegex(ValueError, "finite"):
                    Circle(center=(0.0, 0.0), diameter=invalid)
                with self.assertRaisesRegex(ValueError, "finite"):
                    SketchAsset(name="Bad origin", origin=(0.0, invalid))

    def test_rejects_boolean_and_non_numeric_values(self) -> None:
        for invalid in (True, "1.0", None):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "real number"):
                    Circle(center=(invalid, 0.0), diameter=1.0)  # type: ignore[arg-type]
                with self.assertRaisesRegex(ValueError, "real number"):
                    Circle(center=(0.0, 0.0), diameter=invalid)  # type: ignore[arg-type]

    def test_rejects_malformed_points(self) -> None:
        for invalid in ((0.0,), (0.0, 1.0, 2.0), [0.0, 1.0]):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "two-value tuple"):
                    Circle(center=invalid, diameter=1.0)  # type: ignore[arg-type]

    def test_rejects_degenerate_polygons(self) -> None:
        cases = (
            ((0.0, 0.0), (0.0, 0.0), (1.0, 0.0)),
            ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0)),
        )
        for points in cases:
            with self.subTest(points=points):
                with self.assertRaisesRegex(ValueError, "distinct|non-zero area"):
                    Polygon(points=points)

    def test_rejects_empty_roles_and_invalid_asset_containers(self) -> None:
        with self.assertRaisesRegex(ValueError, "role"):
            Circle(center=(0.0, 0.0), diameter=1.0, role=" ")
        with self.assertRaisesRegex(ValueError, "Circle objects"):
            SketchAsset(name="Bad circles", circles=["hole"])  # type: ignore[list-item]
        with self.assertRaisesRegex(ValueError, "dictionary"):
            SketchAsset(name="Bad metadata", metadata=[])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

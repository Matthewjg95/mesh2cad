"""User-adjustable tolerances that drive every recognition decision.

All distances are in the mesh's native unit (assumed millimetres for STL).
The GUI exposes these on the sidebar; recognition code must read them from a
``Tolerances`` instance rather than hard-coding values, so the user can tune
noisy scans versus clean CAD exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Tolerances:
    """Tunable thresholds for mesh repair, recognition and intent recovery.

    Attributes:
        point_distance: Max distance (mm) between a vertex and a fitted
            analytic surface for the vertex to count as an inlier.
        normal_angle_deg: Max angle (degrees) between a triangle normal and
            the fitted surface normal for region growing to accept it.
        merge_distance: Vertices closer than this are considered duplicates
            during repair.
        hole_perimeter_max: Boundary loops with a perimeter below this (mm)
            are treated as "small holes" and filled during repair.
        coplanar_angle_deg: Two planes within this angle are candidates for
            merging into one plane (asks the user below ``ask_threshold``).
        axis_parallel_angle_deg: Detected axes within this angle of a global
            axis are snapped to it (orthogonal-geometry preference).
        equal_dimension_ratio: Two dimensions (hole diameters, fillet radii)
            whose relative difference is below this are treated as intended
            to be equal.
        dimension_snap: Recovered dimensions are snapped to multiples of this
            (mm) when the snap moves them less than ``point_distance``.
        min_feature_size: Features smaller than this (mm) are treated as mesh
            noise and ignored.
        ask_threshold: Features with confidence below this pause the pipeline
            and ask the user (Stage 4).
        auto_accept_threshold: Features at or above this confidence are
            accepted without user interaction.
    """

    point_distance: float = 0.05
    normal_angle_deg: float = 8.0
    merge_distance: float = 1e-6
    hole_perimeter_max: float = 5.0
    coplanar_angle_deg: float = 1.0
    axis_parallel_angle_deg: float = 1.5
    equal_dimension_ratio: float = 0.02
    dimension_snap: float = 0.05
    min_feature_size: float = 0.3
    ask_threshold: float = 0.55
    auto_accept_threshold: float = 0.85

    def scaled(self, mesh_scale: float) -> "Tolerances":
        """Return a copy with distance thresholds scaled for a mesh.

        Args:
            mesh_scale: Characteristic size of the mesh (e.g. bounding-box
                diagonal) divided by the ~100 mm enclosure the defaults were
                tuned for. Angular and ratio thresholds are unchanged.
        """
        return Tolerances(
            point_distance=self.point_distance * mesh_scale,
            normal_angle_deg=self.normal_angle_deg,
            merge_distance=self.merge_distance * mesh_scale,
            hole_perimeter_max=self.hole_perimeter_max * mesh_scale,
            coplanar_angle_deg=self.coplanar_angle_deg,
            axis_parallel_angle_deg=self.axis_parallel_angle_deg,
            equal_dimension_ratio=self.equal_dimension_ratio,
            dimension_snap=self.dimension_snap * mesh_scale,
            min_feature_size=self.min_feature_size * mesh_scale,
            ask_threshold=self.ask_threshold,
            auto_accept_threshold=self.auto_accept_threshold,
        )

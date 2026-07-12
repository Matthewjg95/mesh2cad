"""Validation of reconstructed solids.

Checks the things a machinist cares about before trusting a STEP file:
is it a single closed solid, is the topology valid, are there open edges,
sliver faces or degenerate edges, and does the volume roughly match the
scanned evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape

# Faces smaller than this (mm^2) are flagged as slivers.
_SLIVER_FACE_AREA = 1e-4
# Edges shorter than this (mm) are flagged as degenerate.
_TINY_EDGE_LENGTH = 1e-3


@dataclass
class ValidationReport:
    """Outcome of Stage-6 checks.

    Attributes:
        is_valid: BRepCheck topology/geometry validity.
        solid_count: Number of solids in the shape.
        face_count: Total faces.
        open_edge_count: Free (unshared) boundary edges — 0 for a closed
            solid.
        sliver_face_count: Faces below the sliver-area threshold.
        tiny_edge_count: Edges below the degenerate-length threshold.
        volume: Enclosed volume (mm^3).
        surface_area: Total surface area (mm^2).
        problems: Human-readable list of everything wrong.
    """

    is_valid: bool = False
    solid_count: int = 0
    face_count: int = 0
    open_edge_count: int = 0
    sliver_face_count: int = 0
    tiny_edge_count: int = 0
    volume: float = 0.0
    surface_area: float = 0.0
    problems: list[str] = field(default_factory=list)

    @property
    def ready_for_export(self) -> bool:
        """True when the result is one closed, valid solid.

        Exactly ONE solid: a machinable part is a single connected body,
        so disjoint pieces (reconstruction dropped whatever bridged them)
        must fail here, not just show as a note.
        """
        return self.is_valid and self.solid_count == 1 \
            and self.open_edge_count == 0

    def summary_lines(self) -> list[str]:
        lines = [
            f"Topology valid: {'yes' if self.is_valid else 'NO'}",
            f"Solids: {self.solid_count}, faces: {self.face_count}",
            f"Open edges: {self.open_edge_count}",
            f"Volume: {self.volume:,.1f} mm³",
            f"Surface area: {self.surface_area:,.1f} mm²",
        ]
        if self.sliver_face_count:
            lines.append(f"Sliver faces: {self.sliver_face_count}")
        if self.tiny_edge_count:
            lines.append(f"Tiny edges: {self.tiny_edge_count}")
        lines.extend(self.problems)
        lines.append(
            "READY FOR EXPORT" if self.ready_for_export
            else "NOT ready for export"
        )
        return lines


def validate_shape(
    shape: TopoDS_Shape, reference_volume: float | None = None
) -> ValidationReport:
    """Run all Stage-6 checks on ``shape``.

    Args:
        shape: The reconstructed solid.
        reference_volume: Volume of the repaired evidence mesh; when given,
            a mismatch beyond 5 % is reported as a problem.
    """
    report = ValidationReport()

    report.is_valid = BRepCheck_Analyzer(shape).IsValid()
    if not report.is_valid:
        report.problems.append("BRepCheck found invalid topology/geometry")

    report.solid_count = _count(shape, TopAbs_SOLID)
    report.face_count = _count(shape, TopAbs_FACE)
    if report.solid_count == 0:
        report.problems.append("No solid in the result (open shell?)")
    elif report.solid_count > 1:
        report.problems.append(
            f"{report.solid_count} disjoint solids — expected one part. "
            "Features that connected them (fillets, freeform blends) were "
            "probably not reconstructed; inspect before machining."
        )

    report.open_edge_count = _open_edges(shape)
    if report.open_edge_count:
        report.problems.append(
            f"{report.open_edge_count} open edges — solid is not closed"
        )

    report.sliver_face_count = _sliver_faces(shape)
    report.tiny_edge_count = _tiny_edges(shape)

    volume_props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume_props)
    report.volume = float(volume_props.Mass())
    surface_props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, surface_props)
    report.surface_area = float(surface_props.Mass())

    if report.volume <= 0:
        report.problems.append("Non-positive volume — inverted solid?")
    if reference_volume and reference_volume > 0:
        deviation = abs(report.volume - reference_volume) / reference_volume
        if deviation > 0.05:
            report.problems.append(
                f"Volume differs from scan by {deviation * 100:.1f}% "
                f"({report.volume:,.0f} vs {reference_volume:,.0f} mm³)"
            )
    return report


def _count(shape: TopoDS_Shape, kind) -> int:
    n = 0
    explorer = TopExp_Explorer(shape, kind)
    while explorer.More():
        n += 1
        explorer.Next()
    return n


def _open_edges(shape: TopoDS_Shape) -> int:
    analyzer = ShapeAnalysis_FreeBounds(shape)
    closed = _count(analyzer.GetClosedWires(), TopAbs_EDGE)
    open_w = _count(analyzer.GetOpenWires(), TopAbs_EDGE)
    return closed + open_w


def _sliver_faces(shape: TopoDS_Shape) -> int:
    n = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(explorer.Current(), props)
        if props.Mass() < _SLIVER_FACE_AREA:
            n += 1
        explorer.Next()
    return n


def _tiny_edges(shape: TopoDS_Shape) -> int:
    n = 0
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(explorer.Current(), props)
        length = props.Mass()
        if 0 < length < _TINY_EDGE_LENGTH:
            n += 1
        explorer.Next()
    return n

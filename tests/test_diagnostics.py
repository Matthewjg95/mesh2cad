"""Tests for the repro-bundle data pipeline."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import trimesh

from mra.diagnostics import ReproReport, build_repro_bundle
from mra.intent import recover_intent
from mra.reconstruction import build_solid
from mra.recognition import segment_mesh


def _plate():
    box = trimesh.creation.box(extents=(40, 30, 5))
    drill = trimesh.creation.cylinder(radius=2, height=20, sections=48)
    return trimesh.boolean.difference([box, drill])


class TestReproBundle:
    def test_bundle_contains_all_parts(self, tmp_path: Path) -> None:
        mesh = _plate()
        seg = segment_mesh(mesh)
        intent = recover_intent(mesh, seg)
        build = build_solid(mesh, seg, intent)
        report = ReproReport(
            user_notes="hole came out square",
            part_class="bracket",
            stage_where_wrong="reconstruct",
            expected="round hole",
            actual="square hole",
        )
        out = build_repro_bundle(
            tmp_path / "bug", mesh, report, seg, intent, build
        )
        assert out.exists() and out.suffix == ".zip"
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
            assert {"mesh.stl", "report.json", "report.md"} <= names
            data = json.loads(zf.read("report.json"))
            md = zf.read("report.md").decode()

        # The report is a self-describing labeled example.
        assert data["schema_version"] >= 1
        assert data["user"]["part_class"] == "bracket"
        assert data["mesh"]["triangles"] > 0
        assert data["recognition"]["patch_count"] >= 6
        assert "by_type" in data["intent"]
        assert data["build"]["succeeded"] is True
        assert "hole came out square" in md

    def test_bundle_without_downstream_stages(self, tmp_path: Path) -> None:
        # A repair/recognition-only failure still produces a valid bundle.
        mesh = _plate()
        out = build_repro_bundle(
            tmp_path / "early", mesh, ReproReport(user_notes="crash")
        )
        with zipfile.ZipFile(out) as zf:
            data = json.loads(zf.read("report.json"))
        assert "mesh" in data
        assert "recognition" not in data  # not run
        assert data["user"]["notes"] == "crash"

    def test_reimportable_mesh(self, tmp_path: Path) -> None:
        mesh = _plate()
        out = build_repro_bundle(tmp_path / "b", mesh, ReproReport())
        with zipfile.ZipFile(out) as zf:
            stl = zf.read("mesh.stl")
        (tmp_path / "m.stl").write_bytes(stl)
        reloaded = trimesh.load(tmp_path / "m.stl")
        assert len(reloaded.faces) == len(mesh.faces)

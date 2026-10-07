"""Sketch asset schema 1.1, local library persistence, and DXF/SVG export.

All headless and kernel-free: no OpenCascade or Qt needed.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mra.sketch_assets import (
    Arc,
    Circle,
    Line,
    Profile,
    SketchAsset,
    SketchLibrary,
    default_library_dir,
)
from mra.sketch_assets.examples import all_examples, device_outline, rounded_plate
from mra.sketch_assets.export_dxf import dxf_text
from mra.sketch_assets.export_svg import svg_text
from mra.sketch_assets.geometry import asset_bounds
from mra.sketch_assets.library import asset_json, read_asset_file

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "sketch_assets" / "library"
SVG_NS = "{http://www.w3.org/2000/svg}"


# ------------------------------------------------------------- schema 1.1


def test_profiles_select_schema_1_1_and_plain_assets_stay_1_0():
    assert rounded_plate().schema_version == "1.1"
    plain = SketchAsset(name="Holes", circles=[Circle((0.0, 0.0), 3.0)])
    assert plain.schema_version == "1.0"
    assert "profiles" not in plain.to_dict()


def test_explicit_1_0_with_profiles_is_rejected():
    outline = rounded_plate().profiles[0]
    with pytest.raises(ValueError, match="require schema_version '1.1'"):
        SketchAsset(name="Bad", profiles=[outline], schema_version="1.0")
    payload = rounded_plate().to_dict()
    payload["schema_version"] = "1.0"
    with pytest.raises(ValueError, match="profiles require"):
        SketchAsset.from_dict(payload)


def test_profile_json_round_trip_is_exact():
    for original in all_examples():
        restored = SketchAsset.from_dict(json.loads(asset_json(original)))
        assert restored == original
        assert restored.schema_version == original.schema_version
        assert restored.units == original.units


def test_rounded_plate_area_counts_true_arcs():
    outline = rounded_plate().profiles[0]
    expected = 80.0 * 50.0 - (4.0 - math.pi) * 6.0**2
    assert outline.signed_area() == pytest.approx(expected, rel=1e-12)
    slot = rounded_plate().profiles[1]
    assert slot.signed_area() == pytest.approx(14.0 * 6.0 + math.pi * 9.0, rel=1e-12)


def test_clockwise_profile_has_negative_area_and_is_accepted():
    ccw = rounded_plate().profiles[1]
    reversed_segments = tuple(
        Line(s.end, s.start) if isinstance(s, Line)
        else Arc(s.end, s.start, center=s.center, ccw=not s.ccw)
        for s in reversed(ccw.segments)
    )
    cw = Profile(reversed_segments, role="cutout")
    assert cw.signed_area() == pytest.approx(-ccw.signed_area())


@pytest.mark.parametrize(
    "build, message",
    [
        (lambda: Profile((Line((0, 0), (1, 0)), Line((1, 0), (0, 1)),
                          Line((0, 1), (0, 0.5)))), "not closed"),
        (lambda: Arc((1.0, 0.0), (0.0, 2.0), center=(0.0, 0.0)), "equidistant"),
        (lambda: Arc((1.0, 0.0), (1.0, 0.0), center=(0.0, 0.0)), "use a Circle"),
        (lambda: Arc((1.0, 0.0), (0.0, 1.0), center=(0.0, 0.0), ccw=1), "true or false"),
        (lambda: Line((0.0, 0.0), (0.0, 0.0)), "non-zero length"),
        (lambda: Profile((Line((0.0, 0.0), (1.0, 0.0)), Line((1.0, 0.0), (0.0, 0.0)))),
         "non-zero area"),
        (lambda: Profile((Circle((0.0, 0.0), 1.0),)), "at least two"),
        (lambda: Line((0.0, math.nan), (1.0, 0.0)), "finite"),
    ],
)
def test_malformed_profile_geometry_is_rejected(build, message):
    with pytest.raises(ValueError, match=message):
        build()


@pytest.mark.parametrize(
    "segment, message",
    [
        ({"type": "spline", "points": []}, "unsupported segment type"),
        ({"type": "arc", "start": [1, 0], "end": [0, 1], "center": [0, 0]}, "requires"),
        ({"type": "line", "start": [0, 0], "end": [1, 0], "bulge": 0.5}, "unknown fields"),
    ],
)
def test_malformed_profile_records_are_rejected(segment, message):
    payload = rounded_plate().to_dict()
    payload["profiles"][1]["segments"][0] = json.loads(json.dumps(segment))
    with pytest.raises(ValueError, match=message):
        SketchAsset.from_dict(json.loads(json.dumps(payload)))


# ------------------------------------------------------------- library


def test_save_load_round_trip_preserves_everything(tmp_path):
    library = SketchLibrary(tmp_path / "lib")
    for asset in all_examples():
        path = library.save(asset)
        assert path.parent == tmp_path / "lib"
        assert json.loads(path.read_text()) == json.loads(asset_json(asset))
        loaded = library.load(path.name[: -len(".sketch.json")])
        assert loaded == asset
        assert loaded.provenance == asset.provenance
    assert not list((tmp_path / "lib").glob(".tmp-*"))


def test_inch_asset_is_not_rescaled(tmp_path):
    library = SketchLibrary(tmp_path)
    path = library.save(device_outline())
    raw = json.loads(path.read_text())
    assert raw["units"] == "in"
    assert raw["circles"][0] == {"center": [0.25, 3.0], "diameter": 0.125,
                                 "role": "mounting-hole"}
    assert asset_bounds(read_asset_file(path)) == (0.0, 0.0, 2.0, 3.5)


def test_save_refuses_to_overwrite_unless_asked(tmp_path):
    library = SketchLibrary(tmp_path)
    library.save(rounded_plate())
    with pytest.raises(FileExistsError, match="already exists"):
        library.save(rounded_plate())
    library.save(rounded_plate(), overwrite=True)


def test_search_by_name_and_tags(tmp_path):
    library = SketchLibrary(tmp_path)
    for asset in all_examples():
        library.save(asset)
    names = lambda result: sorted(e.asset.name for e in result[0])  # noqa: E731
    assert len(names(library.search())) == 3
    assert names(library.search("PLATE")) == ["Synthetic rounded plate 80x50 R6"]
    assert names(library.search("imperial")) == ["Synthetic device outline 2.0x3.5in"]
    assert names(library.search(tags=["synthetic-example", "hole-pattern"])) == [
        "Synthetic 4-hole pattern 50x30"
    ]
    assert names(library.search("plate", tags=["imperial"])) == []


def test_malformed_files_are_reported_not_hidden(tmp_path):
    library = SketchLibrary(tmp_path)
    library.save(rounded_plate())
    bad = {
        "not-json.sketch.json": "{",
        "nan.sketch.json": '{"name": "n", "origin": [NaN, 0]}',
        "dupe.sketch.json": '{"name": "a", "name": "b"}',
        "future.sketch.json": '{"name": "f", "schema_version": "9.0"}',
        "unknown.sketch.json": '{"name": "u", "colour": "red"}',
        "tags.sketch.json": '{"name": "t", "metadata": {"tags": "plate"}}',
        "gap.sketch.json": json.dumps({
            "name": "g", "schema_version": "1.1",
            "profiles": [{"segments": [
                {"type": "line", "start": [0, 0], "end": [1, 0]},
                {"type": "line", "start": [1, 0], "end": [1, 1]},
                {"type": "line", "start": [1, 1], "end": [0, 0.01]},
            ]}],
        }),
    }
    for name, text in bad.items():
        (tmp_path / name).write_text(text)
    entries, problems = library.scan()
    assert [e.asset.name for e in entries] == ["Synthetic rounded plate 80x50 R6"]
    errors = {p.path.name: p.error for p in problems}
    assert set(errors) == set(bad)
    assert "invalid JSON" in errors["not-json.sketch.json"]
    assert "non-finite" in errors["nan.sketch.json"]
    assert "duplicate JSON key" in errors["dupe.sketch.json"]
    assert "unsupported sketch asset schema" in errors["future.sketch.json"]
    assert "unknown fields" in errors["unknown.sketch.json"]
    assert "tags" in errors["tags.sketch.json"]
    assert "not closed" in errors["gap.sketch.json"]


def test_bad_tags_are_rejected_on_save(tmp_path):
    asset = SketchAsset(name="x", circles=[Circle((0.0, 0.0), 1.0)],
                        metadata={"tags": ["ok", ""]})
    with pytest.raises(ValueError, match="tags"):
        SketchLibrary(tmp_path).save(asset)


def test_library_directory_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("MESH2CAD_SKETCH_LIBRARY", str(tmp_path / "env-lib"))
    assert default_library_dir() == tmp_path / "env-lib"
    assert SketchLibrary().root == tmp_path / "env-lib"
    assert SketchLibrary(tmp_path / "explicit").root == tmp_path / "explicit"
    monkeypatch.delenv("MESH2CAD_SKETCH_LIBRARY")
    assert default_library_dir() == Path.home() / ".mesh2cad" / "sketch_assets"


def test_committed_examples_match_generator():
    files = sorted(EXAMPLES_DIR.glob("*.sketch.json"))
    assert len(files) == 3
    by_name = {read_asset_file(f).name: f for f in files}
    for asset in all_examples():
        assert by_name[asset.name].read_text() == asset_json(asset)
        assert asset.metadata["synthetic"] is True
        assert "SYNTHETIC" in asset.provenance["notice"]


# ------------------------------------------------------------------ DXF


def _dxf_pairs(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    return [(int(lines[i]), lines[i + 1]) for i in range(0, len(lines) - 1, 2)]


def _dxf_entities(text: str) -> list[dict]:
    pairs = _dxf_pairs(text)
    start = pairs.index((2, "ENTITIES"))
    entities: list[dict] = []
    for code, value in pairs[start + 1:]:
        if code == 0:
            if value in ("ENDSEC", "EOF"):
                break
            entities.append({"type": value})
        else:
            entities[-1][code] = value
    return entities


def _header_value(text: str, name: str) -> str:
    pairs = _dxf_pairs(text)
    return pairs[pairs.index((9, name)) + 1][1]


def test_dxf_declares_units_without_scaling():
    assert _header_value(dxf_text(rounded_plate()), "$INSUNITS") == "4"
    inch = dxf_text(device_outline())
    assert _header_value(inch, "$INSUNITS") == "1"
    circles = [e for e in _dxf_entities(inch) if e["type"] == "CIRCLE"]
    assert {(float(e[10]), float(e[20]), float(e[40])) for e in circles} == {
        (0.25, 3.0, 0.0625), (1.75, 3.0, 0.0625)
    }


def test_dxf_keeps_arcs_analytic_with_correct_angles():
    asset = rounded_plate()
    entities = _dxf_entities(dxf_text(asset))
    counts: dict[tuple[str, str], int] = {}
    for e in entities:
        counts[(e["type"], e[8])] = counts.get((e["type"], e[8]), 0) + 1
    assert counts == {
        ("LINE", "OUTLINE"): 4, ("ARC", "OUTLINE"): 4,
        ("LINE", "CUTOUT"): 2, ("ARC", "CUTOUT"): 2,
        ("CIRCLE", "MOUNTING-HOLE"): 4,
    }
    # Each DXF arc (always CCW from 50 to 51) must reproduce the asset arc's
    # endpoints and its midpoint.
    asset_arcs = [s for p in asset.profiles for s in p.segments if isinstance(s, Arc)]
    dxf_arcs = [e for e in entities if e["type"] == "ARC"]
    for arc, e in zip(asset_arcs, dxf_arcs):
        cx, cy, r = float(e[10]), float(e[20]), float(e[40])
        a0, a1 = math.radians(float(e[50])), math.radians(float(e[51]))
        sweep = (a1 - a0) % (2 * math.pi)
        mid = a0 + sweep / 2
        assert (cx, cy) == arc.center and r == pytest.approx(arc.radius, abs=1e-12)
        assert sweep == pytest.approx(arc.sweep, abs=1e-12)
        assert (cx + r * math.cos(mid), cy + r * math.sin(mid)) == pytest.approx(
            arc.point_at(0.5), abs=1e-9
        )


def test_dxf_clockwise_arc_written_as_equivalent_ccw_arc():
    cw = Arc((0.0, 1.0), (1.0, 0.0), center=(0.0, 0.0), ccw=False)  # quarter, cw
    asset = SketchAsset(name="cw", profiles=[Profile(
        (Line((0.0, 0.0), (0.0, 1.0)), cw, Line((1.0, 0.0), (0.0, 0.0))))])
    arc = next(e for e in _dxf_entities(dxf_text(asset)) if e["type"] == "ARC")
    assert float(arc[50]) == pytest.approx(0.0) and float(arc[51]) == pytest.approx(90.0)


def test_dxf_is_readable_by_ezdxf():
    ezdxf = pytest.importorskip("ezdxf")
    import io

    for asset in all_examples():
        doc = ezdxf.read(io.StringIO(dxf_text(asset)))
        assert not doc.audit().has_errors
        assert doc.header["$INSUNITS"] == {"mm": 4, "in": 1}[asset.units]


# ------------------------------------------------------------------ SVG


def _svg_arc_center(x1, y1, x2, y2, r, large, sweep):
    """SVG spec F.6.5 endpoint-to-center conversion (rx = ry, phi = 0)."""
    xp, yp = (x1 - x2) / 2, (y1 - y2) / 2
    num = r * r * r * r - r * r * yp * yp - r * r * xp * xp
    coef = math.sqrt(max(0.0, num / (r * r * yp * yp + r * r * xp * xp)))
    if large == sweep:
        coef = -coef
    cx = coef * yp + (x1 + x2) / 2
    cy = coef * -xp + (y1 + y2) / 2
    return cx, cy


def test_svg_flips_y_in_transform_not_in_numbers():
    asset = device_outline()
    root = ET.fromstring(svg_text(asset))
    assert root.get("width").endswith("in") and root.get("height").endswith("in")
    vb = [float(v) for v in root.get("viewBox").split()]
    # viewBox top is -max_y (flipped) and contains the whole outline.
    assert vb[0] < 0.0 and vb[0] + vb[2] > 2.0
    assert vb[1] < -3.5 and vb[1] + vb[3] > 0.0
    assert float(root.get("width")[:-2]) == pytest.approx(vb[2])
    group = root.find(f"{SVG_NS}g")
    assert group.get("transform") == "scale(1 -1)"
    circles = group.findall(f"{SVG_NS}circle")
    assert {(float(c.get("cx")), float(c.get("cy")), float(c.get("r"))) for c in circles} == {
        (0.25, 3.0, 0.0625), (1.75, 3.0, 0.0625)
    }
    meta = json.loads(root.find(f"{SVG_NS}metadata").text)
    assert meta["units"] == "in" and meta["schema_version"] == "1.1"
    assert meta["provenance"] == asset.provenance


def test_svg_arcs_reproduce_asset_centers_and_direction():
    asset = rounded_plate()
    root = ET.fromstring(svg_text(asset))
    paths = root.find(f"{SVG_NS}g").findall(f"{SVG_NS}path")
    assert [p.get("data-role") for p in paths] == ["outline", "cutout"]
    for profile, path in zip(asset.profiles, paths):
        tokens = path.get("d").split()
        assert tokens[0] == "M"
        x, y = float(tokens[1]), float(tokens[2])
        i, arcs = 3, []
        while tokens[i] != "Z":
            if tokens[i] == "L":
                x, y = float(tokens[i + 1]), float(tokens[i + 2])
                i += 3
            else:
                r, _, _, large, sweep, x2, y2 = tokens[i + 1:i + 8]
                arcs.append(_svg_arc_center(x, y, float(x2), float(y2), float(r),
                                            int(large), int(sweep)) + (int(sweep),))
                x, y = float(x2), float(y2)
                i += 8
        expected = [s for s in profile.segments if isinstance(s, Arc)]
        assert len(arcs) == len(expected)
        for (cx, cy, sweep), arc in zip(arcs, expected):
            assert (cx, cy) == pytest.approx(arc.center, abs=1e-9)
            assert sweep == (1 if arc.ccw else 0)


def test_svg_escapes_names_and_roles():
    asset = SketchAsset(name='<plate & "co">', circles=[Circle((0.0, 0.0), 2.0, role='a"b<c')])
    root = ET.fromstring(svg_text(asset))
    assert root.find(f"{SVG_NS}title").text == '<plate & "co">'
    assert root.find(f"{SVG_NS}g").find(f"{SVG_NS}circle").get("data-role") == 'a"b<c'


def test_cli_end_to_end(tmp_path, capsys):
    from mra.sketch_assets.__main__ import main

    lib = str(tmp_path / "lib")
    assert main(["--library", lib, "examples"]) == 0
    assert main(["--library", lib, "examples"]) == 1  # refuses overwrite
    capsys.readouterr()
    assert main(["--library", lib, "list", "--tag", "plate"]) == 0
    assert "synthetic-rounded-plate-80x50-r6" in capsys.readouterr().out
    out_svg, out_dxf = tmp_path / "p.svg", tmp_path / "p.dxf"
    assert main(["--library", lib, "export", "synthetic-rounded-plate-80x50-r6",
                 "--svg", str(out_svg), "--dxf", str(out_dxf)]) == 0
    assert out_svg.read_text() == svg_text(rounded_plate())
    assert out_dxf.read_text() == dxf_text(rounded_plate())
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "x", "units": "cm"}')
    assert main(["--library", lib, "save", str(bad)]) == 1
    assert "units" in capsys.readouterr().err

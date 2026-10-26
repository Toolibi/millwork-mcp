import json
from pathlib import Path

import ezdxf
import pytest

from millwork_mcp.construction import build_parts, hinge_count, slide_length, review_job
from millwork_mcp.estimate import estimate_job, load_pricing, materials_from_pricing
from millwork_mcp.models import Cabinet, Job, Material, Part
from millwork_mcp.nesting import nest_parts
from millwork_mcp.pipeline import job_from_dict, run_job

ROOT = Path(__file__).resolve().parent.parent
PRICING = load_pricing(ROOT / "examples" / "pricing.json")
SAMPLE = json.loads((ROOT / "examples" / "sample_kitchen.json").read_text())


def job_with(*cabs):
    return Job("T-1", "test", list(cabs))


def find(parts, name):
    return next(p for p in parts if p.name == name)


def test_base_cabinet_dimensions():
    cab = Cabinet("B1", "base", 30, 34.5, 24, doors=2, adj_shelves=1)
    parts = build_parts(cab, job_with(cab))
    side, bottom = find(parts, "Side"), find(parts, "Bottom")
    assert (side.length, side.width, side.qty) == (34.5, 24, 2)
    assert bottom.length == pytest.approx(28.5)          # 30 - 2 x 3/4
    assert find(parts, "Top Stretcher").qty == 2
    door = find(parts, "Door L")
    # full overlay: (30 - 2/16 reveal - 1/8 gap) / 2
    assert door.width == pytest.approx((30 - 0.125 - 0.125) / 2)
    assert door.length == pytest.approx(34.5 - 0.125)


def test_wall_cabinet_has_top_not_stretchers():
    cab = Cabinet("W1", "wall", 30, 30, 12, doors=2)
    names = {p.name for p in build_parts(cab, job_with(cab))}
    assert "Top" in names and "Top Stretcher" not in names


def test_drawer_base_fronts_fill_height():
    cab = Cabinet("D1", "drawer_base", 18, 34.5, 24, drawers=3)
    parts = build_parts(cab, job_with(cab))
    fronts = [p for p in parts if p.name.startswith("Drawer Front")]
    total = sum(f.width for f in fronts) + 2 * 0.125
    assert total == pytest.approx(34.5 - 0.125)
    assert find(parts, "Drw1 Side").length == 21


def test_hinge_and_slide_rules():
    assert [hinge_count(h) for h in (30, 50, 70, 90)] == [2, 3, 4, 5]
    assert slide_length(24) == 21 and slide_length(12) == 9
    with pytest.raises(ValueError):
        slide_length(8)


def test_review_flags_catch_problems():
    job = job_with(Cabinet("B1", "base", 54, 34.5, 24, doors=1),
                   Cabinet("B1", "wall", 6, 30, 18))
    flags = " ".join(review_job(job))
    assert "Duplicate cabinet tag B1" in flags
    assert "fewer than 2 doors" in flags
    assert "filler" in flags
    assert "wall cabinet depth" in flags


def test_nest_never_overlaps_and_stays_on_sheet():
    mat = Material("M", "test", 0.75, 97, 49)
    parts = [Part("X", f"P{i}", "M", 30 + i % 5, 10 + i % 7, 3) for i in range(20)]
    res = nest_parts(parts, mat)
    for sh in res.sheets:
        rects = [p for st in sh.strips for p in st.placements]
        for r in rects:
            assert r.x >= 0 and r.y >= 0
            assert r.x + r.length <= sh.length + 1e-6
            assert r.y + r.width <= sh.width + 1e-6
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                overlap = not (a.x + a.length <= b.x or b.x + b.length <= a.x or
                               a.y + a.width <= b.y or b.y + b.width <= a.y)
                assert not overlap, (a, b)
    placed = sum(len(st.placements) for sh in res.sheets for st in sh.strips)
    assert placed == 60


def test_grained_material_is_not_rotated():
    mat = Material("G", "grained", 0.75, 97, 49, grained=True)
    res = nest_parts([Part("X", "Long", "G", 90, 20, 2)], mat)
    assert all(not p.rotated for sh in res.sheets for st in sh.strips for p in st.placements)


def test_oversize_part_is_reported_not_dropped_silently():
    mat = Material("M", "test", 0.75, 97, 49)
    res = nest_parts([Part("X", "Huge", "M", 120, 30, 1)], mat)
    assert res.unplaced and res.sheet_count == 0


def test_sample_estimate_is_internally_consistent():
    est = estimate_job(job_from_dict(SAMPLE), PRICING)
    t = est["totals"]
    assert t["sell_price"] > t["direct_cost"] > 0
    expected = sum(m["cost"] for m in est["materials"]) + est["edgeband"]["cost"] \
        + sum(h["cost"] for h in est["hardware"]) + est["labor"]["cost"]
    assert t["direct_cost"] == pytest.approx(expected, abs=0.05)
    assert est["cabinet_count"] == 10


def test_full_run_writes_valid_dxfs(tmp_path):
    result = run_job(SAMPLE, PRICING, tmp_path)
    dxfs = list((tmp_path / "dxf").glob("*.dxf"))
    assert len(dxfs) == result["files"]["dxf_count"] > 0
    doc = ezdxf.readfile(tmp_path / "dxf" / "B1_Side.dxf")
    layers = {l.dxf.name for l in doc.layers}
    assert "CUT_THRU_T0.750" in layers
    assert any(l.startswith("DRILL_5MM") for l in layers)
    assert any(l.startswith("DADO_") for l in layers)
    door = ezdxf.readfile(tmp_path / "dxf" / "B1_Door_L.dxf")
    assert any(e.dxf.layer.startswith("POCKET_35MM") for e in door.modelspace())

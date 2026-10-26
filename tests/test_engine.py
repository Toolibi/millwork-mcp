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
    front, rear = find(parts, "Top Rail Front"), find(parts, "Top Rail Rear")
    assert (front.width, rear.width) == (4.0, 4.0)
    assert front.edgeband == {"L1": True} and rear.edgeband == {}   # front rail banded only
    door = find(parts, "Door L")
    # full overlay: (30 - 2/16 reveal - 1/8 gap) / 2
    assert door.width == pytest.approx((30 - 0.125 - 0.125) / 2)
    assert door.length == pytest.approx(34.5 - 0.125)


def test_wall_cabinet_has_top_not_stretchers():
    cab = Cabinet("W1", "wall", 30, 30, 12, doors=2)
    names = {p.name for p in build_parts(cab, job_with(cab))}
    assert "Top" in names and "Top Rail Front" not in names


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


# ---------------------------------------------------------------- joinery
from millwork_mcp.construction import cabinet_connectors
from millwork_mcp.joinery import METHODS, connector_positions
from millwork_mcp.nesting import consolidate_grain_groups

MATS = materials_from_pricing(PRICING)
MM = 1 / 25.4


def test_connector_positions_sit_on_the_32mm_grid():
    for key, m in METHODS.items():
        if key == "dowel":
            continue   # dowels follow the shop's own 32/96/96 layout, tested below
        pos = connector_positions(24, m)
        assert pos[0] == pytest.approx(37 * MM, abs=1e-3), key
        for p in pos:
            steps = (p - 37 * MM) / (32 * MM)
            assert steps == pytest.approx(round(steps), abs=1e-3), (key, p)
        assert pos[-1] <= 24 - 25 * MM + 1e-6
        assert len(pos) >= m.min_per_joint


def test_dowel_cabinet_bores_both_parts():
    cab = Cabinet("B1", "base", 30, 34.5, 24, doors=2, joinery="dowel")
    parts = build_parts(cab, job_with(cab))
    side, bottom = find(parts, "Side"), find(parts, "Bottom")
    side_dowels = [o for o in side.machining if o["op"] == "drill" and o["dia_mm"] == 8]
    edge_dowels = [o for o in bottom.machining if o["op"] == "edge_drill"]
    n_bottom = len(connector_positions(24 - 0.75 - 0.25, METHODS["dowel"]))
    n_stretch = len(connector_positions(4, METHODS["dowel"]))
    n_nailer = len(connector_positions(4, METHODS["dowel"]))
    # bottom + front & rear top rails + top & bottom nailers
    assert len(side_dowels) == n_bottom + 2 * n_stretch + 2 * n_nailer
    assert len(edge_dowels) == 2 * n_bottom                    # both ends of the bottom
    assert all(o["y"] == pytest.approx(0.375) for o in side_dowels[:n_bottom])
    count = cabinet_connectors(cab, job_with(cab))
    assert count["dowel_8x35"] == 2 * (n_bottom + 2 * n_stretch + 2 * n_nailer)


def test_confirmat_uses_through_holes_and_caps():
    cab = Cabinet("W1", "wall", 30, 30, 12, doors=2, joinery="confirmat")
    side = find(build_parts(cab, job_with(cab)), "Side")
    assert any(o.get("through") and o["dia_mm"] == 7 for o in side.machining)
    c = cabinet_connectors(cab, job_with(cab))
    assert c["confirmat"] == c["confirmat_cap"] > 0


def test_cam_dowel_puts_cams_at_the_ends_and_dowels_between():
    cab = Cabinet("T1", "tall", 24, 84, 24, doors=2, joinery="cam_dowel")
    bottom = find(build_parts(cab, job_with(cab)), "Bottom")
    cams = [o for o in bottom.machining if o.get("role") == "cam"]
    assert len(cams) == 4 and all(o["dia_mm"] == 15 for o in cams)   # 2 per end
    c = cabinet_connectors(cab, job_with(cab))
    # (2 horizontals + 2 nailers) x 2 ends x 2 cams
    assert c["cam_bolt_set"] == 4 * 2 * 2 and c["dowel_8x35"] > 0


def test_clamex_cuts_p_grooves_and_access_holes():
    cab = Cabinet("BC1", "bookcase", 30, 84, 12, adj_shelves=4, joinery="lamello_clamex")
    parts = build_parts(cab, job_with(cab))
    assert any(o["op"] == "pgroove" for o in find(parts, "Side").machining)
    top = find(parts, "Top").machining
    assert any(o["op"] == "pgroove" and o["orient"] == "edge" for o in top)
    assert any(o.get("role") == "access" for o in top)


def test_confirmat_on_finished_or_open_cabinet_is_flagged():
    job = job_with(Cabinet("B1", "base", 18, 34.5, 24, doors=1, finished_ends="L"),
                   Cabinet("BC1", "bookcase", 30, 72, 12))
    job.joinery = "confirmat"
    flags = " ".join(review_job(job))
    assert "B1: confirmat heads" in flags and "BC1: confirmat heads" in flags


def test_unknown_joinery_is_flagged():
    job = job_with(Cabinet("B1", "base", 18, 34.5, 24, joinery="nails"))
    assert "unknown joinery" in " ".join(review_job(job))


def test_shelf_pins_stay_clear_of_fixed_parts():
    cab = Cabinet("BC1", "bookcase", 30, 84, 12, adj_shelves=4)
    parts = build_parts(cab, job_with(cab))
    side = find(parts, "Side")
    fixed_y = 0.75 + (84 - 1.5) / 2
    pins = [o for o in side.machining if o.get("role") == "shelf_pin"]
    assert pins and all(abs(o["y"] - fixed_y) > 1.375 for o in pins)


def test_joinery_changes_assembly_labor():
    def hours(method):
        d = dict(SAMPLE, joinery=method)
        return estimate_job(job_from_dict(d), PRICING)["labor"]["hours"]
    assert hours("confirmat") < hours("dowel")


# ---------------------------------------------------------------- grain
def grained_job(*cabs, **kw):
    return Job("G-1", "grain", list(cabs), door_material="WO-RIFT-34",
               exposed_material="WO-RIFT-34", exposed_back_material="WO-RIFT-14", **kw)


def test_grain_matched_fronts_share_one_blank_in_sequence():
    cab = Cabinet("DB1", "drawer_base", 30, 34.5, 24, drawers=4)
    parts = build_parts(cab, grained_job(cab), materials=MATS)
    fronts = [p for p in parts if p.name.startswith("Drawer Front")]
    assert {p.grain_group for p in fronts} == {"DB1-GM"}
    assert [p.seq for p in fronts] == [1, 2, 3, 4]
    # vertical grain: length is the front height, and the blank covers the whole bank
    assert all(p.length < p.width for p in fronts)
    assert fronts[0].grain_blank == pytest.approx((34.375 + 0.5, 29.875 + 0.5))
    blanks = [p for p in consolidate_grain_groups(parts) if p.name.startswith("GM blank")]
    assert len(blanks) == 1 and blanks[0].material == "WO-RIFT-34"


def test_door_and_drawer_front_on_one_cabinet_are_matched_together():
    cab = Cabinet("B1", "base", 18, 34.5, 24, doors=1, drawers=1)
    parts = build_parts(cab, grained_job(cab), materials=MATS)
    gm = sorted((p for p in parts if p.grain_group), key=lambda p: p.seq)
    assert [p.name for p in gm] == ["Drawer Front 1", "Door L"]


def test_horizontal_grain_turns_fronts_and_their_hinge_cups():
    cab = Cabinet("W1", "wall", 30, 30, 12, doors=2)
    v = find(build_parts(cab, grained_job(cab), materials=MATS), "Door L")
    h = find(build_parts(cab, grained_job(cab, front_grain="horizontal"), materials=MATS), "Door L")
    assert (h.length, h.width) == (v.width, v.length)
    assert h.grain_blank == (v.grain_blank[1], v.grain_blank[0])
    for a, b in zip(v.machining, h.machining):
        assert (a["x"], a["y"]) == (b["y"], b["x"])


def test_plain_material_is_not_grain_matched():
    cab = Cabinet("W1", "wall", 30, 30, 12, doors=2)
    parts = build_parts(cab, job_with(cab), materials=MATS)
    assert not any(p.grain_group for p in parts)


def test_open_cabinets_use_exposed_material_with_correct_grain():
    mw = Cabinet("MW1", "microwave", 30, 36, 15, doors=2, opening_height=17)
    parts = build_parts(mw, grained_job(mw), materials=MATS)
    for name in ("Side", "Bottom", "Top", "Fixed Shelf"):
        p = find(parts, name)
        assert p.material == "WO-RIFT-34" and p.exposed
    assert find(parts, "Back").material == "WO-RIFT-14"
    # grain: sides vertical (length = height), horizontals run side to side
    assert find(parts, "Side").length == 36
    assert find(parts, "Fixed Shelf").length == pytest.approx(28.5)
    # doors only cover the area above the fixed shelf
    door = find(parts, "Door L")
    assert door.length == pytest.approx(36 - 0.0625 - (0.75 + 17 + 0.375) - 0.0625)


def test_finished_ends_use_exposed_material():
    cab = Cabinet("B1", "base", 18, 34.5, 24, doors=1, finished_ends="L")
    parts = build_parts(cab, grained_job(cab), materials=MATS)
    assert find(parts, "Side (finished)").qty == 1
    assert find(parts, "Side (finished)").material == "WO-RIFT-34"
    assert find(parts, "Side").qty == 1 and find(parts, "Side").material == "MEL-WHT-34"


def test_microwave_without_appliance_spec_is_flagged():
    job = job_with(Cabinet("MW1", "microwave", 30, 36, 15, doors=2))
    assert "no opening height" in " ".join(review_job(job))


def test_white_oak_sample_runs_and_writes_joinery_layers(tmp_path):
    data = json.loads((ROOT / "examples" / "white_oak_kitchen.json").read_text())
    r = run_job(data, PRICING, tmp_path)
    assert r["grain_match_groups"] == 7
    assert set(r["joinery"]) == set(METHODS)
    layers = set()
    for f in (tmp_path / "dxf").glob("*.dxf"):
        layers |= {l.dxf.name for l in ezdxf.readfile(f).layers}
    for prefix in ("HDRILL_8MM", "DRILL_7MM_THRU", "LAMELLO_P14_FACE", "LAMELLO_P14_EDGE",
                   "DRILL_15MM", "POCKET_35MM"):
        assert any(l.startswith(prefix) for l in layers), prefix
    saw = (tmp_path / "saw_list.csv").read_text()
    assert "GM blank" in saw


def test_joint_bores_stay_out_of_the_back_dado():
    for method in METHODS:
        cab = Cabinet("B1", "base", 30, 34.5, 24, doors=2, joinery=method)
        side = find(build_parts(cab, job_with(cab)), "Side")
        dado_front, dado_rear = 24 - 0.75 - 0.25, 24 - 0.75
        for o in side.machining:
            if o["op"] in ("drill", "pgroove") and o.get("role") != "shelf_pin":
                r = 0.1 if o["op"] == "pgroove" and o.get("orient") == "face_v" else \
                    o.get("dia_mm", 55) / 25.4 / 2
                # every joint bore clears the dado: case joints in front, nailer joints behind
                assert o["x"] + r < dado_front or o["x"] - r > dado_rear, (method, o)


# ---------------------------------------------------------------- CNC routing
from millwork_mcp.export import route, op_summary


def test_shelf_pin_sides_route_to_cnc_and_plain_panels_stay_on_the_saw():
    cab = Cabinet("W1", "wall", 30, 30, 12, doors=2, adj_shelves=2)
    parts = build_parts(cab, job_with(cab))
    side = find(parts, "Side")
    assert route(side) == "saw > CNC"
    assert op_summary(side)["shelf_pin_holes"] > 0
    assert route(find(parts, "Back")) == "saw"
    assert route(find(parts, "Adj Shelf")) == "saw"
    assert route(find(parts, "Door L")) == "saw > CNC"   # hinge cups


def test_cnc_list_and_cnc_hours(tmp_path):
    r = run_job(SAMPLE, PRICING, tmp_path)
    rows = (tmp_path / "cnc_list.csv").read_text().splitlines()
    assert rows[0].startswith("cabinet,part,material,qty,program,shelf_pin_holes")
    assert any(",Side," in row for row in rows[1:])
    assert not any(",Back," in row for row in rows[1:])
    lab = r["labor"]
    assert lab["cnc_hours"] > 0 and lab["cnc_pieces"] > 0
    assert lab["hours"] > lab["saw_hours"] + lab["cnc_hours"]



# ---------------------------------------------------------------- shop construction spec
def test_wall_attached_cabinets_get_quarter_back_and_two_ply_nailers():
    for cab in (Cabinet("W1", "wall", 30, 30, 12, doors=2),
                Cabinet("B1", "base", 30, 34.5, 24, doors=2),
                Cabinet("T1", "tall", 24, 84, 24, doors=2)):
        parts = build_parts(cab, job_with(cab), materials=MATS)
        assert find(parts, "Back").material == "MEL-WHT-14"           # 1/4" back
        for name in ("Nailer Top", "Nailer Bottom"):
            n = find(parts, name)
            assert n.material == "PLY-34" and MATS["PLY-34"].thickness == 0.75
            assert (n.length, n.width, n.qty) == (cab.width - 1.5, 4.0, 1)
            assert n.machining, "nailer ends must be bored for the case joinery"
        side = find(parts, "Side")
        behind_back = [o for o in side.machining if o["x"] > cab.depth - 0.75]
        assert behind_back, "side must be bored for the nailers behind the back"
        ys = sorted(o["y"] for o in behind_back)
        assert ys[0] > 0.75 and ys[0] < 0.75 + 4              # bottom nailer above the bottom
        assert ys[-1] < cab.height - 0.75 and ys[-1] > cab.height - 0.75 - 4  # top nailer below top


def test_freestanding_cabinet_has_no_nailers():
    cab = Cabinet("BC1", "bookcase", 30, 72, 12, against_wall=False)
    names = {p.name for p in build_parts(cab, job_with(cab))}
    assert not names & {"Nailer Top", "Nailer Bottom"}


def test_prelam_finished_end_is_laminated_before_machining():
    cab = Cabinet("B1", "base", 18, 34.5, 24, doors=1, adj_shelves=1, finished_ends="L")
    job = job_with(cab)
    job.finished_end_laminate = "HPL-WHT"
    parts = build_parts(cab, job, materials=MATS)
    fin = find(parts, "Side (finished, pre-lam)")
    assert fin.material == "MEL-WHT-34" and fin.laminate == "HPL-WHT" and fin.qty == 1
    assert fin.prelam_blank == (34.5 + 1.0, 24 + 1.0)
    assert route(fin) == "prelam > saw > CNC"
    assert find(parts, "Side").qty == 1 and route(find(parts, "Side")) == "saw > CNC"
    from millwork_mcp.nesting import prepare_for_saw
    saw = prepare_for_saw(parts)
    core = [p for p in saw if p.name.startswith("PRELAM core")]
    lam = [p for p in saw if p.name.startswith("PRELAM laminate")]
    assert core[0].material == "MEL-WHT-34" and lam[0].material == "HPL-WHT"
    assert (core[0].length, core[0].width) == fin.prelam_blank


def test_prelam_without_laminate_is_flagged():
    job = job_with(Cabinet("B1", "base", 18, 34.5, 24, doors=1, finished_ends="L"))
    assert "no laminate is set" in " ".join(review_job(job))


def test_sample_estimate_charges_prelam_and_laminate():
    est = estimate_job(job_from_dict(SAMPLE), PRICING)
    assert est["prelam_panels"] == 1 + 2 + 1     # B1 L, W2 L x2, T1 R
    assert any(m["material"] == "HPL-WHT" for m in est["materials"])
    assert any(m["material"] == "PLY-34" for m in est["materials"])



# ---------------------------------------------------------------- dowel spec
from millwork_mcp.joinery import dowel_positions, dowel_size


def mm(pos):
    return [round(p / MM, 1) for p in pos]


def test_dowel_layout_32_then_96_then_every_96_with_closing_dowel():
    # 24" deep bottom, joint zone ends at the back dado (23" = 584.2mm)
    assert mm(dowel_positions(23.0)) == [32, 96, 192, 288, 384, 480, 552.2]
    # 12" wall cabinet bottom (joint zone 11" = 279.4mm)
    assert mm(dowel_positions(11.0)) == [32, 96, 192, 247.4]
    pitch = mm(dowel_positions(23.0))[:-1]
    gaps = [b - a for a, b in zip(pitch, pitch[1:])]
    assert gaps[0] == 64 and all(g == 96 for g in gaps[1:])


def test_closing_dowel_is_32mm_from_the_back_and_never_crowds_the_last_pitch_dowel():
    for inches in [x / 8 for x in range(32, 200)]:      # 4" to 24.9" joints
        pos = mm(dowel_positions(inches))
        assert pos[-1] == pytest.approx(inches * 25.4 - 32, abs=0.1)
        assert all(b - a >= 32 - 0.1 for a, b in zip(pos, pos[1:])), (inches, pos)


def test_narrow_rails_and_nailers_still_get_two_dowels():
    pos = mm(dowel_positions(4.0))
    assert len(pos) == 2 and pos[0] == 32 and pos[1] == pytest.approx(4 * 25.4 - 32, abs=0.1)


def test_dowel_diameter_follows_material_thickness():
    assert dowel_size(0.75).dia_mm == 8
    assert dowel_size(0.5).dia_mm == 5
    assert dowel_size(0.375).dia_mm == 5
    for t in (0.75, 0.5, 0.375):
        assert dowel_size(t).face_depth <= t - 0.125     # never blows through the side


def test_half_inch_casework_uses_5mm_dowels_and_half_inch_geometry():
    cab = Cabinet("W1", "wall", 30, 30, 12, doors=2)
    job = job_with(cab)
    job.case_material = "MEL-WHT-12"
    parts = build_parts(cab, job, materials=MATS)
    side, bottom = find(parts, "Side"), find(parts, "Bottom")
    joint_bores = [o for o in side.machining if o["op"] == "drill" and o.get("role") != "shelf_pin"]
    assert joint_bores and {o["dia_mm"] for o in joint_bores} == {5}
    assert all(o["depth"] <= 0.5 - 0.125 for o in joint_bores)
    assert {o["dia_mm"] for o in bottom.machining if o["op"] == "edge_drill"} == {5}
    assert bottom.length == pytest.approx(30 - 2 * 0.5)        # inside width from 1/2" sides
    assert min(o["y"] for o in joint_bores) == pytest.approx(0.25)  # bottom centre line at t/2
    c = cabinet_connectors(cab, job, materials=MATS)
    # nailers are 3/4" ply but join 1/2" sides, so the thinner part sets 5mm
    assert c["dowel_5x25"] > 0 and "dowel_8x35" not in c


def test_three_quarter_casework_uses_8mm_dowels():
    cab = Cabinet("B1", "base", 30, 34.5, 24, doors=2)
    parts = build_parts(cab, job_with(cab), materials=MATS)
    joint_bores = [o for o in find(parts, "Side").machining
                   if o["op"] == "drill" and o.get("role") != "shelf_pin"]
    assert {o["dia_mm"] for o in joint_bores} == {8}
    bottom_row = sorted(o["x"] for o in joint_bores if o["y"] < 1)
    assert mm(bottom_row) == [32, 96, 192, 288, 384, 480, 552.2]   # closing 32mm before dado


def test_off_spec_thickness_is_flagged():
    job = job_with(Cabinet("B1", "base", 18, 34.5, 24, doors=1))
    job.case_material = "BB-58"      # 5/8" is not in the dowel spec
    assert "outside the dowel spec" in " ".join(review_job(job, MATS))

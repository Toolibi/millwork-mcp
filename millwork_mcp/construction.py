"""Construction standard: turns a Cabinet into a list of manufacturable Parts.

Encodes a frameless (32mm / European) construction method with selectable case joinery
(dowel, confirmat, cam & dowel, Lamello Clamex) and grain rules for veneered work.
Every shop builds a little differently, so the rules live in ConstructionStandard and
can be changed without touching the logic, the same idea as a Cabinet Vision
construction method.

Convention: Part.length is ALWAYS the grain direction. On a part's DXF, x runs across
the part width (front edge at x=0) and y runs along its length.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math

from .joinery import METHODS, connector_positions, connectors_for, horizontal_ops, side_ops
from .models import BASE_TYPES, OPEN_TYPES, Cabinet, Job, Material, Part

MM = 1 / 25.4  # one millimetre in inches


@dataclass
class ConstructionStandard:
    case_t: float = 0.75            # case panel thickness
    back_t: float = 0.25
    back_dado_depth: float = 0.375
    back_setback: float = 0.75      # back dado distance from rear edge of side
    stretcher_w: float = 4.0        # base cabinet top stretchers
    fit_clearance: float = 1 / 32   # back / shelf fit clearance
    shelf_front_setback: float = 0.125
    edge_reveal: float = 1 / 16     # door reveal at each cabinet edge (full overlay)
    door_gap: float = 1 / 8         # gap between doors / drawer fronts
    top_drawer_front_h: float = 6.0
    drawer_side_t: float = 0.625
    drawer_bottom_t: float = 0.25
    slide_clearance: float = 13 / 32  # opening width - drawer box outside width (undermount)
    hinge_cup_dia_mm: float = 35.0
    hinge_cup_depth: float = 0.5
    hinge_cup_from_edge_mm: float = 22.5
    hinge_from_end: float = 3.5
    system_hole_mm: float = 5.0
    system_hole_depth: float = 0.5
    system_row_setback_mm: float = 37.0
    system_pitch_mm: float = 32.0
    grain_blank_allowance: float = 0.25   # oversize per edge on a grain-match blank
    bookcase_fixed_shelf_over: float = 48.0  # bookcases taller than this get a fixed shelf


def hinge_count(door_height: float) -> int:
    if door_height <= 40:
        return 2
    if door_height <= 60:
        return 3
    if door_height <= 80:
        return 4
    return 5


def slide_length(depth: float) -> float:
    """Pick the longest standard undermount slide that fits the box depth."""
    for length in (21, 18, 15, 12, 9):
        if depth - 1.0 >= length:
            return float(length)
    raise ValueError(f"Cabinet depth {depth}\" is too shallow for a standard drawer slide")


def _system_holes(part_w: float, std: ConstructionStandard, start: float, end: float) -> list[dict]:
    """Two rows of 5mm shelf-pin holes on 32mm pitch between start and end (inches from bottom)."""
    holes = []
    pitch = std.system_pitch_mm * MM
    front_x = std.system_row_setback_mm * MM
    rear_x = part_w - std.back_setback - std.back_t - std.system_row_setback_mm * MM
    n = int((end - start) / pitch) + 1 if end > start else 0
    for i in range(n):
        y = start + i * pitch
        for x in (front_x, rear_x):
            holes.append({"op": "drill", "dia_mm": std.system_hole_mm, "depth": std.system_hole_depth,
                          "x": round(x, 4), "y": round(y, 4), "role": "shelf_pin"})
    return holes


def _door_machining(h: float, w: float, std: ConstructionStandard, hinge_side: str) -> list[dict]:
    """Hinge cups in door coordinates: x across the door width, y along its height."""
    x = std.hinge_cup_from_edge_mm * MM if hinge_side == "L" else w - std.hinge_cup_from_edge_mm * MM
    n = hinge_count(h)
    span = h - 2 * std.hinge_from_end
    ys = [std.hinge_from_end + span * i / (n - 1) for i in range(n)]
    return [{"op": "pocket", "dia_mm": std.hinge_cup_dia_mm, "depth": std.hinge_cup_depth,
             "x": round(x, 4), "y": round(y, 4)} for y in ys]


def _swap_xy(ops: list[dict]) -> list[dict]:
    """Re-express machining when a part's grain (length) runs across what was its width."""
    return [{**o, "x": o["y"], "y": o["x"]} if "y" in o else dict(o) for o in ops]


@dataclass
class Horizontal:
    name: str
    depth: float        # part width (front to back)
    qty: int            # per cabinet
    y_centers: list     # centre-line heights on the side, one per piece
    x_offsets: list     # distance from the side's front edge, one per piece
    exposed: bool = False
    joint_depth: float = 0.0   # connector zone; stops in front of the back dado

    def __post_init__(self):
        if not self.joint_depth:
            self.joint_depth = self.depth


def _horizontals(cab: Cabinet, std: ConstructionStandard) -> list[Horizontal]:
    t, H, D = std.case_t, cab.height, cab.depth
    back_face = D - std.back_setback - std.back_t   # fixed shelves stop at the back
    hs = [Horizontal("Bottom", D, 1, [t / 2], [0.0], joint_depth=back_face)]
    if cab.type in BASE_TYPES:
        rear = D - std.back_setback - std.back_t - std.stretcher_w
        hs.append(Horizontal("Top Stretcher", std.stretcher_w, 2, [H - t / 2] * 2, [0.0, rear]))
    else:
        hs.append(Horizontal("Top", D, 1, [H - t / 2], [0.0], joint_depth=back_face))
    fixed = cab.fixed_shelves
    if cab.type == "bookcase" and fixed == 0 and H > std.bookcase_fixed_shelf_over:
        fixed = 1
    if cab.type == "microwave":
        hs.append(Horizontal("Fixed Shelf", back_face, 1,
                             [t + cab.opening_height + t / 2], [0.0], exposed=True))
    elif fixed:
        ys = [t + (H - 2 * t) * (i + 1) / (fixed + 1) for i in range(fixed)]
        hs.append(Horizontal("Fixed Shelf", back_face, fixed, ys, [0.0] * fixed))
    return hs


def cabinet_connectors(cab: Cabinet, job: Job, std: ConstructionStandard | None = None) -> Counter:
    """Case connectors for one cabinet (times its qty)."""
    std = std or ConstructionStandard()
    method = METHODS[cab.joinery or job.joinery]
    total: Counter = Counter()
    for h in _horizontals(cab, std):
        pos = connector_positions(h.joint_depth, method)
        per_joint = connectors_for(method, pos)
        for k, v in per_joint.items():
            total[k] += v * 2 * h.qty * cab.qty   # two ends per horizontal
    return total


def build_parts(cab: Cabinet, job: Job, std: ConstructionStandard | None = None,
                materials: dict[str, Material] | None = None) -> list[Part]:
    std = std or ConstructionStandard()
    method = METHODS[cab.joinery or job.joinery]
    t = std.case_t
    W, H, D, q = cab.width, cab.height, cab.depth, cab.qty
    inside_w = W - 2 * t
    is_open = cab.type in OPEN_TYPES
    case_mat = job.exposed_material if (is_open and job.exposed_material) else job.case_material
    back_mat = (job.exposed_back_material or job.back_material) if is_open else job.back_material
    front_only = {"L1": True}
    parts: list[Part] = []
    horizontals = _horizontals(cab, std)

    # --- sides: grain vertical (length = height) ---
    side_mach = [{"op": "dado", "width": std.back_t, "depth": std.back_dado_depth,
                  "x": round(D - std.back_setback - std.back_t, 4), "axis": "length"}]
    for h in horizontals:
        for y, off in zip(h.y_centers, h.x_offsets):
            side_mach += side_ops(method, connector_positions(h.joint_depth, method, off), y, t)
    if cab.adj_shelves:
        start = t + 3.0
        if cab.type == "microwave":   # adjustable shelves only above the fixed shelf
            start = t + cab.opening_height + t + 2.0
        joint_ys = [y for h in horizontals for y in h.y_centers]
        clear = t / 2 + 1.0   # keep shelf-pin holes 1" clear of every fixed part
        side_mach += [o for o in _system_holes(D, std, start=start, end=H - t - 3.0)
                      if all(abs(o["y"] - jy) > clear for jy in joint_ys)]

    fin = len(set(cab.finished_ends.upper()) & {"L", "R"})
    if fin and job.exposed_material and not is_open:
        parts.append(Part(cab.tag, "Side (finished)", job.exposed_material, H, D, fin * q,
                          dict(front_only), side_mach, exposed=True))
        if fin < 2:
            parts.append(Part(cab.tag, "Side", case_mat, H, D, (2 - fin) * q, dict(front_only), side_mach))
    else:
        parts.append(Part(cab.tag, "Side", case_mat, H, D, 2 * q, dict(front_only), side_mach,
                          exposed=is_open or bool(fin)))

    # --- horizontals: grain runs side to side (length = inside width) ---
    for h in horizontals:
        pos = connector_positions(h.joint_depth, method)
        mach = (horizontal_ops(method, pos, "W1", inside_w)
                + horizontal_ops(method, pos, "W2", inside_w))
        eb = dict(front_only) if h.name != "Top Stretcher" else {}
        parts.append(Part(cab.tag, h.name, case_mat, inside_w, h.depth, h.qty * q, eb, mach,
                          exposed=is_open or h.exposed))

    # --- back: grain vertical ---
    if cab.type in BASE_TYPES:
        back_h = H - t + std.back_dado_depth - std.fit_clearance
    else:
        back_h = H - 2 * t + 2 * std.back_dado_depth - std.fit_clearance
    back_w = inside_w + 2 * std.back_dado_depth - std.fit_clearance
    parts.append(Part(cab.tag, "Back", back_mat, back_h, back_w, q, exposed=is_open))

    # --- adjustable shelves ---
    if cab.adj_shelves:
        shelf_d = D - std.back_setback - std.back_t - std.shelf_front_setback
        parts.append(Part(cab.tag, "Adj Shelf", case_mat, inside_w - std.fit_clearance,
                          shelf_d, cab.adj_shelves * q, dict(front_only), exposed=is_open))

    parts += _fronts(cab, job, std, materials)
    parts += _drawer_boxes(cab, job, std)
    return parts


def _fronts(cab: Cabinet, job: Job, std: ConstructionStandard,
            materials: dict[str, Material] | None) -> list[Part]:
    """Doors and drawer fronts, with optional grain-matched sequencing.

    Grain match: every front on a cabinet is cut, in order, from one oversize blank, so
    the figure runs continuously across the doors and down the drawer bank. The blank
    is what gets nested; the fronts are recut from it.
    """
    t, W, H, q = std.case_t, cab.width, cab.height, cab.qty
    all_edges = {"L1": True, "L2": True, "W1": True, "W2": True}
    front_w = W - 2 * std.edge_reveal
    region_h = H - 2 * std.edge_reveal
    if cab.type == "microwave":   # fronts only above the fixed shelf, open below
        region_h = (H - std.edge_reveal) - (t + cab.opening_height + t / 2) - std.door_gap / 2

    # lay the fronts out top to bottom: (kind, height, [widths])
    rows: list[tuple[str, float, int]] = []
    if cab.type == "drawer_base" and cab.drawers:
        n = cab.drawers
        dfh = (region_h - (n - 1) * std.door_gap) / n
        rows = [("drawer", dfh, 1)] * n
    else:
        door_h = region_h
        if cab.drawers:
            rows = [("drawer", std.top_drawer_front_h, 1)] * cab.drawers
            door_h = region_h - cab.drawers * (std.top_drawer_front_h + std.door_gap)
        if cab.doors and door_h > 0:
            rows.append(("door", door_h, cab.doors))
    if not rows:
        return []

    mat = materials.get(job.door_material) if materials else None
    grained = bool(mat and mat.grained)
    match = grained and job.grain_match
    horizontal = grained and job.front_grain == "horizontal"
    group = f"{cab.tag}-GM" if match else None
    a = std.grain_blank_allowance
    blank = None
    if match:
        blank = ((front_w + 2 * a, region_h + 2 * a) if horizontal
                 else (region_h + 2 * a, front_w + 2 * a))

    parts, seq = [], 0
    drawer_no = 0
    for kind, h, n_across in rows:
        w = (front_w - (n_across - 1) * std.door_gap) / n_across
        for i in range(n_across):
            seq += 1
            if kind == "door":
                side = "L" if i % 2 == 0 else "R"
                name = f"Door {side}" if n_across <= 2 else f"Door {i + 1}"
                mach = _door_machining(h, w, std, side)
            else:
                drawer_no += 1
                name = f"Drawer Front {drawer_no}"
                mach = []
            # grain direction: vertical fronts -> length = height. Plain material keeps
            # the shop default: doors vertical, drawer fronts horizontal.
            along_width = horizontal or (not grained and kind == "drawer")
            if along_width:
                length, width = w, h
                mach = _swap_xy(mach)
            else:
                length, width = h, w
            parts.append(Part(cab.tag, name, job.door_material, length, width, q, dict(all_edges),
                              mach, grain_group=group, seq=seq if match else None,
                              grain_blank=blank, exposed=True))
    return parts


def _drawer_boxes(cab: Cabinet, job: Job, std: ConstructionStandard) -> list[Part]:
    if not cab.drawers:
        return []
    t, W, H, D, q = std.case_t, cab.width, cab.height, cab.depth, cab.qty
    if cab.type == "drawer_base":
        n = cab.drawers
        fronts = [(H - 2 * std.edge_reveal - (n - 1) * std.door_gap) / n] * n
    else:
        fronts = [std.top_drawer_front_h] * cab.drawers
    sl = slide_length(D)
    box_w = (W - 2 * t) - std.slide_clearance
    dt = std.drawer_side_t
    parts = []
    for i, fh in enumerate(fronts, 1):
        box_h = max(min(math.floor((fh - 1.5) * 2) / 2, 10.0), 3.0)  # round down to 1/2"
        parts.append(Part(cab.tag, f"Drw{i} Side", "BB-58", sl, box_h, 2 * q, {"L1": True}))
        parts.append(Part(cab.tag, f"Drw{i} Front/Back", "BB-58", box_w - 2 * dt, box_h, 2 * q,
                          {"L1": True}))
        parts.append(Part(cab.tag, f"Drw{i} Bottom", job.back_material, sl - 0.5,
                          box_w - 2 * dt + 0.5, q))
    return parts


def build_job_parts(job: Job, std: ConstructionStandard | None = None,
                    materials: dict[str, Material] | None = None) -> list[Part]:
    parts: list[Part] = []
    for cab in job.cabinets:
        parts.extend(build_parts(cab, job, std, materials))
    return parts


def review_job(job: Job, materials: dict[str, Material] | None = None) -> list[str]:
    flags = []
    tags = [c.tag for c in job.cabinets]
    for d in sorted({t for t in tags if tags.count(t) > 1}):
        flags.append(f"Duplicate cabinet tag {d} - merge into one line with qty or rename")
    for c in job.cabinets:
        if (c.joinery or job.joinery) not in METHODS:
            flags.append(f"{c.tag}: unknown joinery '{c.joinery or job.joinery}' - "
                         f"use one of {', '.join(METHODS)}")
            continue
        flags.extend(c.validate(job))
        if c.type in OPEN_TYPES and not job.exposed_material:
            flags.append(f"{c.tag}: open cabinet with no exposed material set - interior will be "
                         f"{job.case_material}; confirm finish")
    if materials:
        dm = materials.get(job.door_material)
        if dm and dm.grained and not job.grain_match:
            flags.append("Grained fronts without grain match - figure will not run across doors/drawers")
        em = materials.get(job.exposed_material) if job.exposed_material else None
        if em and dm and em.grained and dm.grained and em.code != dm.code:
            flags.append(f"Exposed material {em.code} differs from door material {dm.code} - "
                         "confirm species/finish match")
    return flags

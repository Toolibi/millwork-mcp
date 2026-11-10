# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

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
from dataclasses import dataclass, replace
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
    stretcher_w: float = 4.0        # base cabinet top rails (front + rear)
    nailer_w: float = 4.0           # 3/4" plywood hanging nailers, top and bottom
    nailer_t: float = 0.75          # sits in the back setback, flush with the case rear
    prelam_allowance: float = 0.5   # oversize per edge on a pre-lam blank before trimming
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


def _mirror_x(ops: list[dict], width: float) -> list[dict]:
    """Mirror face machining across the part width (left side -> right side)."""
    out = []
    for o in ops:
        o = dict(o)
        if o["op"] == "dado":
            o["x"] = round(width - o["x"] - o["width"], 4)
        elif "x" in o:
            o["x"] = round(width - o["x"], 4)
        out.append(o)
    return out


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
    eb_front: bool = True      # front edge banded to match the cabinet front

    def __post_init__(self):
        if not self.joint_depth:
            self.joint_depth = self.depth


def _horizontals(cab: Cabinet, std: ConstructionStandard) -> list[Horizontal]:
    t, H, D = std.case_t, cab.height, cab.depth
    back_face = D - std.back_setback - std.back_t   # fixed shelves stop at the back
    hs = [Horizontal("Bottom", D, 1, [t / 2], [0.0], joint_depth=back_face)]
    if cab.type in BASE_TYPES:
        # base tops: two rails; the front one is banded to match the cabinet front
        rear = D - std.back_setback - std.back_t - std.stretcher_w
        hs.append(Horizontal("Top Rail Front", std.stretcher_w, 1, [H - t / 2], [0.0]))
        hs.append(Horizontal("Top Rail Rear", std.stretcher_w, 1, [H - t / 2], [rear],
                             eb_front=False))
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


@dataclass
class Nailer:
    name: str
    y0: float   # bottom edge of the nailer on the side, inches from the side bottom


def _nailers(cab: Cabinet, std: ConstructionStandard) -> list[Nailer]:
    """Hanging nailers for cabinets attached to a wall: 3/4" plywood, 4" wide, running
    between the sides, one under the top and one above the bottom. They sit behind the
    1/4" back in the 3/4" back setback, flush with the rear of the case, so the cabinet
    screws to the wall through solid plywood."""
    if not cab.against_wall:
        return []
    t, H = std.case_t, cab.height
    return [Nailer("Nailer Top", H - t - std.nailer_w), Nailer("Nailer Bottom", t)]


def _nailer_side_ops(method, cab: Cabinet, std: ConstructionStandard, n: Nailer,
                     nailer_t: float) -> list[dict]:
    x = cab.depth - std.nailer_t / 2          # centre of the nailer thickness
    ops = []
    for pos in connector_positions(std.nailer_w, method):
        for o in side_ops(method, [x], n.y0 + pos, std.case_t, nailer_t):
            if o["op"] == "pgroove":
                o["orient"] = "face_v"      # groove runs up the side for an on-edge part
            ops.append(o)
    return ops


def _case_setup(cab: Cabinet, job: Job, std: ConstructionStandard,
                materials: dict[str, Material] | None):
    """Case/back material for this cabinet, and the standard with the case thickness
    taken from the actual material (so 1/2" casework gets 1/2" geometry and 5mm dowels)."""
    is_open = cab.type in OPEN_TYPES
    case_mat = job.exposed_material if (is_open and job.exposed_material) else job.case_material
    back_mat = (job.exposed_back_material or job.back_material) if is_open else job.back_material
    if materials and case_mat in materials:
        std = replace(std, case_t=materials[case_mat].thickness)
    nailer_t = (materials[job.nailer_material].thickness
                if materials and job.nailer_material in materials else std.nailer_t)
    return std, case_mat, back_mat, nailer_t


def cabinet_connectors(cab: Cabinet, job: Job, std: ConstructionStandard | None = None,
                       materials: dict[str, Material] | None = None) -> Counter:
    """Case connectors for one cabinet (times its qty)."""
    std, _, _, nailer_t = _case_setup(cab, job, std or ConstructionStandard(), materials)
    method = METHODS[cab.joinery or job.joinery]
    total: Counter = Counter()
    for h in _horizontals(cab, std):
        pos = connector_positions(h.joint_depth, method)
        per_joint = connectors_for(method, pos, std.case_t)
        for k, v in per_joint.items():
            total[k] += v * 2 * h.qty * cab.qty   # two ends per horizontal
    for _ in _nailers(cab, std):
        jt = min(std.case_t, nailer_t)
        for k, v in connectors_for(method, connector_positions(std.nailer_w, method), jt).items():
            total[k] += v * 2 * cab.qty
    return total


def build_parts(cab: Cabinet, job: Job, std: ConstructionStandard | None = None,
                materials: dict[str, Material] | None = None) -> list[Part]:
    std, case_mat, back_mat, nailer_t = _case_setup(cab, job, std or ConstructionStandard(), materials)
    method = METHODS[cab.joinery or job.joinery]
    t = std.case_t
    W, H, D, q = cab.width, cab.height, cab.depth, cab.qty
    inside_w = W - 2 * t
    is_open = cab.type in OPEN_TYPES
    front_only = {"L1": True}
    parts: list[Part] = []
    horizontals = _horizontals(cab, std)

    # --- sides: grain vertical (length = height) ---
    side_mach = [{"op": "dado", "width": std.back_t, "depth": std.back_dado_depth,
                  "x": round(D - std.back_setback - std.back_t, 4), "axis": "length"}]
    for h in horizontals:
        for y, off in zip(h.y_centers, h.x_offsets):
            side_mach += side_ops(method, connector_positions(h.joint_depth, method, off), y, t)
    nailers = _nailers(cab, std)
    for n in nailers:
        side_mach += _nailer_side_ops(method, cab, std, n, nailer_t)
    if cab.adj_shelves:
        start = t + 3.0
        if cab.type == "microwave":   # adjustable shelves only above the fixed shelf
            start = t + cab.opening_height + t + 2.0
        joint_ys = [y for h in horizontals for y in h.y_centers]
        clear = t / 2 + 1.0   # keep shelf-pin holes 1" clear of every fixed part
        side_mach += [o for o in _system_holes(D, std, start=start, end=H - t - 3.0)
                      if all(abs(o["y"] - jy) > clear for jy in joint_ys)]

    # Left and right sides are mirror images: both are machined inside face up, so the
    # right side's bores are the left side's mirrored front-to-back (x -> D - x).
    finished = set(cab.finished_ends.upper()) & {"L", "R"}
    a = std.prelam_allowance
    prelam = (not is_open and job.finished_end_method == "prelam" and job.finished_end_laminate)
    veneer_end = not is_open and job.exposed_material and not prelam
    for hand in ("L", "R"):
        mach = side_mach if hand == "L" else _mirror_x(side_mach, D)
        if hand in finished and prelam:
            # pre-lam: laminate is pressed before machining, so the CNC bores through a
            # finished, flat face
            parts.append(Part(cab.tag, f"Side {hand} (finished, pre-lam)", case_mat, H, D, q,
                              dict(front_only), mach, exposed=True,
                              laminate=job.finished_end_laminate,
                              prelam_blank=(H + 2 * a, D + 2 * a)))
        elif hand in finished and veneer_end:
            parts.append(Part(cab.tag, f"Side {hand} (finished)", job.exposed_material, H, D, q,
                              dict(front_only), mach, exposed=True))
        else:
            parts.append(Part(cab.tag, f"Side {hand}", case_mat, H, D, q, dict(front_only), mach,
                              exposed=is_open or hand in finished))

    # --- horizontals: grain runs side to side (length = inside width) ---
    for h in horizontals:
        pos = connector_positions(h.joint_depth, method)
        mach = (horizontal_ops(method, pos, "W1", inside_w, t)
                + horizontal_ops(method, pos, "W2", inside_w, t))
        eb = dict(front_only) if h.eb_front else {}
        parts.append(Part(cab.tag, h.name, case_mat, inside_w, h.depth, h.qty * q, eb, mach,
                          exposed=is_open or h.exposed))

    # --- back: grain vertical ---
    if cab.type in BASE_TYPES:
        back_h = H - t + std.back_dado_depth - std.fit_clearance
    else:
        back_h = H - 2 * t + 2 * std.back_dado_depth - std.fit_clearance
    back_w = inside_w + 2 * std.back_dado_depth - std.fit_clearance
    parts.append(Part(cab.tag, "Back", back_mat, back_h, back_w, q, exposed=is_open))

    # --- hanging nailers: 3/4" ply, 4" wide x the length between the sides ---
    for n in nailers:
        pos = connector_positions(std.nailer_w, method)
        jt = min(t, nailer_t)
        mach = (horizontal_ops(method, pos, "W1", inside_w, jt)
                + horizontal_ops(method, pos, "W2", inside_w, jt))
        parts.append(Part(cab.tag, n.name, job.nailer_material, inside_w, std.nailer_w, q,
                          {}, mach))

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
                              grain_blank=blank, exposed=True,
                              grain_axis="width" if along_width else "height"))
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
    if job.finished_end_method not in ("prelam", "exposed_material"):
        flags.append(f"Unknown finished_end_method '{job.finished_end_method}' - use prelam or exposed_material")
    if job.finished_end_method == "prelam" and not job.finished_end_laminate and \
            any(c.finished_ends for c in job.cabinets if c.type not in OPEN_TYPES):
        flags.append("Finished ends are specified as pre-lam but no laminate is set "
                     "(finished_end_laminate) - finished sides fall back to the exposed material, "
                     "or plain case material if none is set")
    if materials:
        from .joinery import DOWEL_SPEC_THICKNESSES
        for c in job.cabinets:
            if (c.joinery or job.joinery) not in ("dowel", "cam_dowel"):
                continue
            _, cm, _, _ = _case_setup(c, job, ConstructionStandard(), materials)
            th = materials[cm].thickness if cm in materials else None
            if th is not None and th < 0.375 - 1e-6:
                flags.append(f"{c.tag}: {cm} is {th}\" - too thin to dowel; use a different joint")
            elif th is not None and all(abs(th - x) > 1e-3 for x in DOWEL_SPEC_THICKNESSES):
                size = "8mm" if th >= 0.70 else "5mm"
                flags.append(f"{c.tag}: {cm} is {th}\" - outside the dowel spec (3/4\" = 8mm, "
                             f"1/2\" and 3/8\" = 5mm); using {size}, confirm")
        nm = materials.get(job.nailer_material)
        if nm and abs(nm.thickness - ConstructionStandard().back_setback) > 1e-3:
            flags.append(f"Nailer material {nm.code} is {nm.thickness}\" thick but the back setback "
                         f"is {ConstructionStandard().back_setback}\" - nailers will not sit flush")
        dm = materials.get(job.door_material)
        if dm and dm.grained and not job.grain_match:
            flags.append("Grained fronts without grain match - figure will not run across doors/drawers")
        em = materials.get(job.exposed_material) if job.exposed_material else None
        if em and dm and em.grained and dm.grained and em.code != dm.code:
            flags.append(f"Exposed material {em.code} differs from door material {dm.code} - "
                         "confirm species/finish match")
    return flags

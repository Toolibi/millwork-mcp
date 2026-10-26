"""Construction standard: turns a Cabinet into a list of manufacturable Parts.

This encodes a frameless (32mm / European) construction method. Every shop builds a
little differently, so all the rules live in ConstructionStandard and can be changed
without touching the logic - the same idea as a Cabinet Vision construction method.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .models import Cabinet, Part, Job

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


def _system_holes(part_len: float, part_w: float, std: ConstructionStandard,
                  start: float, end: float) -> list[dict]:
    """Two rows of 5mm shelf-pin holes on 32mm pitch between start and end (inches from bottom)."""
    holes = []
    pitch = std.system_pitch_mm * MM
    front_x = std.system_row_setback_mm * MM
    rear_x = part_w - std.back_setback - std.back_t - std.system_row_setback_mm * MM
    n = int((end - start) / pitch) + 1
    for i in range(max(n, 0)):
        y = start + i * pitch
        for x in (front_x, rear_x):
            holes.append({"op": "drill", "dia_mm": std.system_hole_mm,
                          "depth": std.system_hole_depth, "x": round(x, 4), "y": round(y, 4)})
    return holes


def _door_machining(h: float, w: float, std: ConstructionStandard, hinge_side: str) -> list[dict]:
    x = std.hinge_cup_from_edge_mm * MM if hinge_side == "L" else w - std.hinge_cup_from_edge_mm * MM
    n = hinge_count(h)
    if n == 2:
        ys = [std.hinge_from_end, h - std.hinge_from_end]
    else:
        span = h - 2 * std.hinge_from_end
        ys = [std.hinge_from_end + span * i / (n - 1) for i in range(n)]
    return [{"op": "pocket", "dia_mm": std.hinge_cup_dia_mm, "depth": std.hinge_cup_depth,
             "x": round(x, 4), "y": round(y, 4)} for y in ys]


def build_parts(cab: Cabinet, job: Job, std: ConstructionStandard | None = None) -> list[Part]:
    std = std or ConstructionStandard()
    t = std.case_t
    W, H, D, q = cab.width, cab.height, cab.depth, cab.qty
    inside_w = W - 2 * t
    parts: list[Part] = []
    front_only = {"L1": True}

    # --- sides: grain runs vertically (length = height) ---
    side_mach = [{"op": "dado", "width": std.back_t, "depth": std.back_dado_depth,
                  "x": round(D - std.back_setback - std.back_t, 4), "axis": "length"}]
    if cab.adj_shelves:
        side_mach += _system_holes(H, D, std, start=t + 3.0, end=H - t - 3.0)
    parts.append(Part(cab.tag, "Side", job.case_material, H, D, 2 * q, dict(front_only), side_mach))

    # --- bottom / top ---
    parts.append(Part(cab.tag, "Bottom", job.case_material, inside_w, D, q, dict(front_only)))
    if cab.type in ("base", "drawer_base", "sink_base"):
        parts.append(Part(cab.tag, "Top Stretcher", job.case_material, inside_w, std.stretcher_w, 2 * q))
        back_h = H - t + std.back_dado_depth - std.fit_clearance
    else:
        parts.append(Part(cab.tag, "Top", job.case_material, inside_w, D, q, dict(front_only)))
        back_h = H - 2 * t + 2 * std.back_dado_depth - std.fit_clearance

    # --- back ---
    back_w = inside_w + 2 * std.back_dado_depth - std.fit_clearance
    parts.append(Part(cab.tag, "Back", job.back_material, back_h, back_w, q))

    # --- adjustable shelves ---
    if cab.adj_shelves:
        shelf_d = D - std.back_setback - std.back_t - std.shelf_front_setback
        parts.append(Part(cab.tag, "Adj Shelf", job.case_material, inside_w - std.fit_clearance,
                          shelf_d, cab.adj_shelves * q, dict(front_only)))

    # --- fronts ---
    all_edges = {"L1": True, "L2": True, "W1": True, "W2": True}
    front_h_total = H - 2 * std.edge_reveal
    front_w_total = W - 2 * std.edge_reveal
    door_h = front_h_total

    if cab.type == "drawer_base" and cab.drawers:
        n = cab.drawers
        dfh = (front_h_total - (n - 1) * std.door_gap) / n
        fronts = [dfh] * n
        door_h = 0
    elif cab.drawers:  # e.g. base with one top drawer over doors
        fronts = [std.top_drawer_front_h] * cab.drawers
        door_h = front_h_total - sum(fronts) - len(fronts) * std.door_gap
    else:
        fronts = []

    if cab.doors and door_h > 0:
        door_w = (front_w_total - (cab.doors - 1) * std.door_gap) / cab.doors
        for i in range(cab.doors):
            side = "L" if i % 2 == 0 else "R"
            parts.append(Part(cab.tag, f"Door {side}", job.door_material, door_h, door_w, q,
                              dict(all_edges), _door_machining(door_h, door_w, std, side)))

    if fronts:
        sl = slide_length(D)
        opening_w = inside_w
        box_w = opening_w - std.slide_clearance
        for i, fh in enumerate(fronts, 1):
            parts.append(Part(cab.tag, f"Drawer Front {i}", job.door_material, front_w_total, fh, q,
                              dict(all_edges)))
            box_h = math.floor((fh - 1.5) * 2) / 2  # round down to 1/2"
            box_h = max(min(box_h, 10.0), 3.0)
            dt = std.drawer_side_t
            parts.append(Part(cab.tag, f"Drw{i} Side", "BB-58", sl, box_h, 2 * q, {"L1": True}))
            parts.append(Part(cab.tag, f"Drw{i} Front/Back", "BB-58", box_w - 2 * dt, box_h, 2 * q,
                              {"L1": True}))
            parts.append(Part(cab.tag, f"Drw{i} Bottom", job.back_material, sl - 0.5,
                              box_w - 2 * dt + 0.5, q))
    return parts


def build_job_parts(job: Job, std: ConstructionStandard | None = None) -> list[Part]:
    parts: list[Part] = []
    for cab in job.cabinets:
        parts.extend(build_parts(cab, job, std))
    return parts


def review_job(job: Job) -> list[str]:
    flags = []
    tags = [c.tag for c in job.cabinets]
    dupes = {t for t in tags if tags.count(t) > 1}
    for d in sorted(dupes):
        flags.append(f"Duplicate cabinet tag {d} - merge into one line with qty or rename")
    for c in job.cabinets:
        flags.extend(c.validate())
    return flags

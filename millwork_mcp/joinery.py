"""Case joinery methods, laid out on the 32mm system.

Each method produces two sets of machining for every case joint:
  * face operations on the side (gable), at the centre line of the horizontal part
  * edge / face operations on the end of the horizontal part (bottom, top, stretcher, fixed shelf)

Dowels follow the shop spec: 8mm in 3/4" material, 5mm in 1/2" and 3/8" material. The
first dowel is 32mm from the front, the next 64mm further (96mm), then every 96mm to
the back, with a closing dowel 32mm in front of the back of the joint. Other connectors start at the 37mm
system line and step on the 32mm grid. Sizes are common industry values; confirm them
against your supplier's spec sheet and your CAM tooling before cutting.
"""
from __future__ import annotations

from dataclasses import dataclass

MM = 1 / 25.4


@dataclass(frozen=True)
class JoineryMethod:
    key: str
    name: str
    pitch_mm: float            # spacing between connectors, a multiple of 32mm
    min_per_joint: int
    glue: bool                 # glued joints need clamp / cure time
    shows_on_face: bool        # head or cap visible on the outside of the side
    knock_down: bool           # can be assembled on site
    description: str


METHODS: dict[str, JoineryMethod] = {
    "dowel": JoineryMethod(
        "dowel", "Dowel, 32mm system (8mm in 3/4\", 5mm in 1/2\" & 3/8\")", 96, 2, True, False, False,
        "Face bore in the side, edge bore in the horizontal at 32mm, 96mm, then every 96mm "
        "from the front, closing dowel 32mm from the back; glued and clamped"),
    "confirmat": JoineryMethod(
        "confirmat", "7 x 50mm confirmat screw", 160, 2, False, True, False,
        "7mm through-hole in the side, 5mm pilot into the edge of the horizontal"),
    "cam_dowel": JoineryMethod(
        "cam_dowel", "15mm cam & bolt (Minifix-style) with dowels", 96, 2, False, False, True,
        "Cam bolts at the front and rear connector positions, 8mm dowels between"),
    "lamello_clamex": JoineryMethod(
        "lamello_clamex", "Lamello Clamex P-14 knock-down connector", 192, 2, False, False, True,
        "P-System groove in both parts, 6mm access hole in the horizontal face"),
}


@dataclass(frozen=True)
class DowelSize:
    dia_mm: float
    face_depth: float   # into the side face (inches)
    edge_depth: float   # into the horizontal's edge (inches)
    code: str           # hardware code, e.g. "dowel_8x35"


def dowel_size(thickness: float) -> DowelSize:
    """Shop spec: 8mm dowels in 3/4" material, 5mm in 1/2" and 3/8" material.
    Face bores leave at least 1/8" of material behind them."""
    if thickness >= 0.70:
        return DowelSize(8, 0.5, 1.0, "dowel_8x35")
    if thickness >= 0.45:
        return DowelSize(5, min(0.375, thickness - 0.125), 0.625, "dowel_5x25")
    return DowelSize(5, max(thickness - 0.125, 0.1875), 0.625, "dowel_5x20")


DOWEL_SPEC_THICKNESSES = (0.75, 0.5, 0.375)
DOWEL_FIRST_MM, DOWEL_SECOND_MM, DOWEL_PITCH_MM = 32, 96, 96
DOWEL_CLOSING_MM = 32     # closing dowel this far in front of the joint end (the back)
DOWEL_MIN_GAP_MM = 32     # no two dowels closer than this


def dowel_positions(length: float, offset: float = 0.0) -> list[float]:
    """32mm from the front, then 96mm (a 64mm interval), then every 96mm, plus a closing
    dowel 32mm in front of the back of the joint.

    For bottoms and tops the joint ends at the back dado, so the closing dowel sits 32mm
    in front of it. Pitch dowels that would land within 32mm of the closing dowel are
    dropped. Narrow parts (4" rails and nailers) end up with 32mm from each end.
    """
    total = length / MM
    closing = total - DOWEL_CLOSING_MM
    if closing <= DOWEL_FIRST_MM:   # too narrow for two: one centred dowel
        return [round(offset + length / 2, 4)]
    pts = []
    p = DOWEL_FIRST_MM
    while p <= closing - DOWEL_MIN_GAP_MM + 1e-9:
        pts.append(p)
        p = DOWEL_SECOND_MM if p == DOWEL_FIRST_MM else p + DOWEL_PITCH_MM
    pts.append(closing)
    return [round(offset + q * MM, 4) for q in pts]


def connector_positions(length: float, method: JoineryMethod, offset: float = 0.0) -> list[float]:
    """Positions (inches from the front edge of the joint) on the 32mm grid.

    First connector on the 37mm line, then every `pitch`, plus a rear connector on the
    last grid point that keeps 25mm of material behind it. Dowels use the shop's
    32 / 96 / every-96mm layout instead (see dowel_positions).
    """
    if method.key == "dowel":
        return dowel_positions(length, offset)
    first = 37 * MM
    grid = 32 * MM
    limit = length - 25 * MM
    if limit < first:  # very narrow part (e.g. a 2" rail): one centred connector
        return [round(offset + length / 2, 4)]
    grid_pts = []
    p = first
    while p <= limit + 1e-9:
        grid_pts.append(p)
        p += grid
    step = max(1, round(method.pitch_mm / 32))
    chosen = grid_pts[::step]
    if grid_pts[-1] not in chosen and grid_pts[-1] - chosen[-1] >= 32 * MM - 1e-9:
        chosen.append(grid_pts[-1])
    while len(chosen) < min(method.min_per_joint, len(grid_pts)):
        extra = [g for g in grid_pts if g not in chosen]
        chosen.append(extra[-1])
        chosen.sort()
    return [round(offset + c, 4) for c in chosen]


def side_ops(method: JoineryMethod, positions: list[float], y_center: float,
             side_t: float, joint_t: float | None = None) -> list[dict]:
    """Face machining on the side for one joint. x = across the side's depth, y = height.
    joint_t is the thinner of the two parts; it sets the dowel size."""
    ds = dowel_size(min(side_t, joint_t or side_t))
    ops = []
    for i, x in enumerate(positions):
        is_end = i in (0, len(positions) - 1)
        if method.key == "dowel":
            ops.append(_drill(ds.dia_mm, ds.face_depth, x, y_center))
        elif method.key == "confirmat":
            ops.append(_drill(7, side_t, x, y_center, through=True))
        elif method.key == "cam_dowel":
            ops.append(_drill(5, 0.5, x, y_center) if is_end
                       else _drill(ds.dia_mm, ds.face_depth, x, y_center))
        elif method.key == "lamello_clamex":
            ops.append({"op": "pgroove", "system": "P14", "x": x, "y": round(y_center, 4),
                        "orient": "face"})
    return ops


def horizontal_ops(method: JoineryMethod, positions: list[float], edge: str,
                   part_length: float, joint_t: float = 0.75) -> list[dict]:
    """Machining on one end of a horizontal part.

    edge is "W1" (start of the part length) or "W2" (end). x = across the part width.
    Edge bores are recorded with their edge; face bores (cams, access holes) get a y.
    """
    ds = dowel_size(joint_t)
    ops = []
    y_face_from_end = 34 * MM
    y_face = y_face_from_end if edge == "W1" else part_length - y_face_from_end
    for i, x in enumerate(positions):
        is_end = i in (0, len(positions) - 1)
        if method.key == "dowel":
            ops.append(_edge_drill(ds.dia_mm, ds.edge_depth, x, edge))
        elif method.key == "confirmat":
            ops.append(_edge_drill(5, 1.5, x, edge))
        elif method.key == "cam_dowel":
            if is_end:
                ops.append(_edge_drill(8, 34 * MM, x, edge))
                ops.append({**_drill(15, 13.5 * MM, x, y_face), "role": "cam"})
            else:
                ops.append(_edge_drill(ds.dia_mm, ds.edge_depth, x, edge))
        elif method.key == "lamello_clamex":
            ops.append({"op": "pgroove", "system": "P14", "x": x, "edge": edge, "orient": "edge"})
            ops.append({**_drill(6, 0.5, x, 9 * MM if edge == "W1" else part_length - 9 * MM),
                        "role": "access"})
    return ops


def connectors_for(method: JoineryMethod, positions: list[float],
                   joint_t: float = 0.75) -> dict[str, int]:
    """Hardware consumed by one joint."""
    n = len(positions)
    dowel = dowel_size(joint_t).code
    if method.key == "dowel":
        return {dowel: n}
    if method.key == "confirmat":
        return {"confirmat": n, "confirmat_cap": n}
    if method.key == "cam_dowel":
        cams = min(2, n)
        return {"cam_bolt_set": cams, dowel: n - cams}
    if method.key == "lamello_clamex":
        return {"clamex_p14": n}
    raise KeyError(method.key)


def _drill(dia_mm: float, depth: float, x: float, y: float, through: bool = False) -> dict:
    return {"op": "drill", "dia_mm": dia_mm, "depth": round(depth, 4),
            "x": round(x, 4), "y": round(y, 4), "through": through}


def _edge_drill(dia_mm: float, depth: float, x: float, edge: str) -> dict:
    return {"op": "edge_drill", "dia_mm": dia_mm, "depth": round(depth, 4),
            "x": round(x, 4), "edge": edge}

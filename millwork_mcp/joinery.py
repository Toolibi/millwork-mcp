"""Case joinery methods, laid out on the 32mm system.

Each method produces two sets of machining for every case joint:
  * face operations on the side (gable), at the centre line of the horizontal part
  * edge / face operations on the end of the horizontal part (bottom, top, stretcher, fixed shelf)

Connector positions start at the 37mm system line from the front edge and step on the
32mm grid, so the same CNC drill block that bores shelf-pin rows also bores the joints.
Sizes below are common industry values; confirm them against your connector supplier's
spec sheet and your CAM tooling before cutting.
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
        "dowel", "8 x 35mm dowel, 32mm system", 96, 2, True, False, False,
        "8mm face bore in the side, 8mm edge bore in the horizontal; glued and clamped"),
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


def connector_positions(length: float, method: JoineryMethod, offset: float = 0.0) -> list[float]:
    """Positions (inches from the front edge of the joint) on the 32mm grid.

    First connector on the 37mm line, then every `pitch`, plus a rear connector on the
    last grid point that keeps 25mm of material behind it.
    """
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
             side_t: float) -> list[dict]:
    """Face machining on the side for one joint. x = across the side's depth, y = height."""
    ops = []
    for i, x in enumerate(positions):
        is_end = i in (0, len(positions) - 1)
        if method.key == "dowel":
            ops.append(_drill(8, 0.5, x, y_center))
        elif method.key == "confirmat":
            ops.append(_drill(7, side_t, x, y_center, through=True))
        elif method.key == "cam_dowel":
            ops.append(_drill(5, 0.5, x, y_center) if is_end else _drill(8, 0.5, x, y_center))
        elif method.key == "lamello_clamex":
            ops.append({"op": "pgroove", "system": "P14", "x": x, "y": round(y_center, 4),
                        "orient": "face"})
    return ops


def horizontal_ops(method: JoineryMethod, positions: list[float], edge: str,
                   part_length: float) -> list[dict]:
    """Machining on one end of a horizontal part.

    edge is "W1" (start of the part length) or "W2" (end). x = across the part width.
    Edge bores are recorded with their edge; face bores (cams, access holes) get a y.
    """
    ops = []
    y_face_from_end = 34 * MM
    y_face = y_face_from_end if edge == "W1" else part_length - y_face_from_end
    for i, x in enumerate(positions):
        is_end = i in (0, len(positions) - 1)
        if method.key == "dowel":
            ops.append(_edge_drill(8, 1.0, x, edge))
        elif method.key == "confirmat":
            ops.append(_edge_drill(5, 1.5, x, edge))
        elif method.key == "cam_dowel":
            if is_end:
                ops.append(_edge_drill(8, 34 * MM, x, edge))
                ops.append({**_drill(15, 13.5 * MM, x, y_face), "role": "cam"})
            else:
                ops.append(_edge_drill(8, 1.0, x, edge))
        elif method.key == "lamello_clamex":
            ops.append({"op": "pgroove", "system": "P14", "x": x, "edge": edge, "orient": "edge"})
            ops.append({**_drill(6, 0.5, x, 9 * MM if edge == "W1" else part_length - 9 * MM),
                        "role": "access"})
    return ops


def connectors_for(method: JoineryMethod, positions: list[float]) -> dict[str, int]:
    """Hardware consumed by one joint."""
    n = len(positions)
    if method.key == "dowel":
        return {"dowel": n}
    if method.key == "confirmat":
        return {"confirmat": n, "confirmat_cap": n}
    if method.key == "cam_dowel":
        cams = min(2, n)
        return {"cam_bolt_set": cams, "dowel": n - cams}
    if method.key == "lamello_clamex":
        return {"clamex_p14": n}
    raise KeyError(method.key)


def _drill(dia_mm: float, depth: float, x: float, y: float, through: bool = False) -> dict:
    return {"op": "drill", "dia_mm": dia_mm, "depth": round(depth, 4),
            "x": round(x, 4), "y": round(y, 4), "through": through}


def _edge_drill(dia_mm: float, depth: float, x: float, edge: str) -> dict:
    return {"op": "edge_drill", "dia_mm": dia_mm, "depth": round(depth, 4),
            "x": round(x, 4), "edge": edge}

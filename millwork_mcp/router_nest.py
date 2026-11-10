# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""True-shape-free rectangular nesting for a nested-based CNC router.

A nested router cuts parts straight out of the full sheet, so there is no guillotine
constraint: parts can sit anywhere as long as the bit can get between them. This uses
MaxRects (Jylanki's maximal-rectangles packer) with every part inflated by the tool
spacing, a grain lock for grained material, and a multi-start search over sort orders
and placement rules, keeping the layout with the fewest sheets.

Shop rules built in:
  * grain-matched fronts are nested as one group, laid out in sequence, so the figure
    still runs from front to front on the sheet
  * pre-lam parts nest on full laminated sheets (pressed before nesting), not loose blanks
  * cut order per sheet: all boring and grooving first, then cut-outs smallest part first,
    so small parts are freed while the sheet still has full vacuum
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .machine import MachineProfile
from .models import Material, Part


@dataclass
class RouterPlacement:
    part: Part
    label: str
    x: float          # along the sheet length (grain direction), inches
    y: float          # across the sheet width
    length: float     # extent along x
    width: float      # extent along y
    rotated: bool
    group: str | None = None
    seq: int | None = None


@dataclass
class RouterSheet:
    index: int
    material: str
    length: float
    width: float
    placements: list[RouterPlacement] = field(default_factory=list)

    @property
    def part_area(self) -> float:
        return sum(p.length * p.width for p in self.placements)

    @property
    def yield_pct(self) -> float:
        return 100.0 * self.part_area / (self.length * self.width)

    def cut_order(self) -> list[RouterPlacement]:
        """Cut-outs, smallest part first."""
        return sorted(self.placements, key=lambda p: (p.length * p.width, p.x, p.y))

    def contour_length(self) -> float:
        return sum(2 * (p.length + p.width) for p in self.placements)


@dataclass
class RouterNestResult:
    material: str
    sheets: list[RouterSheet]
    unplaced: list[str]
    small_parts: list[str]

    @property
    def sheet_count(self) -> int:
        return len(self.sheets)

    @property
    def yield_pct(self) -> float:
        if not self.sheets:
            return 0.0
        return 100.0 * sum(s.part_area for s in self.sheets) / sum(s.length * s.width for s in self.sheets)

    def summary(self) -> dict:
        return {"material": self.material, "sheets": self.sheet_count,
                "yield_pct": round(self.yield_pct, 1), "unplaced": self.unplaced,
                "small_parts": self.small_parts,
                "per_sheet_yield": [round(s.yield_pct, 1) for s in self.sheets]}


# ------------------------------------------------------------------ pieces
@dataclass
class _Piece:
    label: str
    length: float
    width: float
    can_rotate: bool
    members: list   # [(Part, dx, dy, length, width)] in the piece's own frame


def nest_material_key(p: Part) -> str:
    """Pre-lam parts nest on laminated sheets of their core material."""
    return f"{p.material}+{p.laminate}" if p.laminate else p.material


def _group_layout(members: list[Part], gap: float) -> tuple[float, float, list]:
    """Lay grain-matched fronts out the way they hang on the cabinet: rows top to bottom,
    doors side by side, `gap` apart. Returns (length_x, width_y, members) in sheet frame,
    where x is the grain direction."""
    members = sorted(members, key=lambda m: m.seq)
    rows: list[list[Part]] = []
    for m in members:
        if rows and m.name.startswith("Door") and rows[-1][0].name.startswith("Door") \
                and abs(rows[-1][0].length - m.length) < 1e-6 and abs(rows[-1][0].width - m.width) < 1e-6:
            rows[-1].append(m)
        else:
            rows.append([m])
    vertical = members[0].grain_axis != "width"

    def h(m):  # front height
        return m.length if vertical else m.width

    def w(m):  # front width
        return m.width if vertical else m.length

    placed, v = [], 0.0
    max_u = 0.0
    for row in rows:
        u = 0.0
        for m in row:
            placed.append((m, u, v))
            u += w(m) + gap
        max_u = max(max_u, u - gap)
        v += max(h(m) for m in row) + gap
    total_v = v - gap
    out = []
    for m, u, vv in placed:
        if vertical:        # height runs along the grain = sheet x
            out.append((m, vv, u, m.length, m.width))
        else:               # width runs along the grain = sheet x
            out.append((m, u, vv, m.length, m.width))
    return ((total_v, max_u, out) if vertical else (max_u, total_v, out))


def _pieces(parts: list[Part], material: Material, gap: float) -> tuple[list[_Piece], list[str]]:
    pieces, seen = [], set()
    for p in parts:
        if p.grain_group and material.grained:
            if p.grain_group in seen:
                continue
            seen.add(p.grain_group)
            members = [m for m in parts if m.grain_group == p.grain_group]
            L, W, lay = _group_layout(members, gap)
            for i in range(p.qty):
                pieces.append(_Piece(f"{p.cabinet_tag} grain-match group", L, W, False, lay))
            continue
        for _ in range(p.qty):
            pieces.append(_Piece(f"{p.cabinet_tag} {p.name}", p.length, p.width,
                                 not material.grained, [(p, 0.0, 0.0, p.length, p.width)]))
    return pieces, []


# ------------------------------------------------------------------ MaxRects
class _Bin:
    def __init__(self, W: float, H: float):
        self.free = [(0.0, 0.0, W, H)]

    def score(self, w: float, h: float, rule: str):
        best = None
        for fx, fy, fw, fh in self.free:
            if w <= fw + 1e-9 and h <= fh + 1e-9:
                if rule == "bssf":
                    s = (min(fw - w, fh - h), max(fw - w, fh - h))
                elif rule == "baf":
                    s = (fw * fh - w * h, min(fw - w, fh - h))
                else:  # bottom-left
                    s = (fy + h, fx)
                if best is None or s < best[0]:
                    best = (s, fx, fy)
        return best

    def place(self, x: float, y: float, w: float, h: float):
        new = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw - 1e-9 or x + w <= fx + 1e-9 or y >= fy + fh - 1e-9 or y + h <= fy + 1e-9:
                new.append((fx, fy, fw, fh))
                continue
            if x > fx + 1e-9:
                new.append((fx, fy, x - fx, fh))
            if x + w < fx + fw - 1e-9:
                new.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy + 1e-9:
                new.append((fx, fy, fw, y - fy))
            if y + h < fy + fh - 1e-9:
                new.append((fx, y + h, fw, fy + fh - y - h))
        # prune rectangles contained in others
        pruned = []
        for i, a in enumerate(new):
            contained = False
            for j, b in enumerate(new):
                if i != j and a[0] >= b[0] - 1e-9 and a[1] >= b[1] - 1e-9 and \
                        a[0] + a[2] <= b[0] + b[2] + 1e-9 and a[1] + a[3] <= b[1] + b[3] + 1e-9:
                    if a != b or i > j:
                        contained = True
                        break
            if not contained:
                pruned.append(a)
        self.free = pruned


def _run(pieces: list[_Piece], material: Material, m: MachineProfile, order: str, rule: str):
    # inflate by spacing; the bin gets one extra spacing so the last part fits the margin
    s = m.part_spacing
    BW = material.sheet_length - 2 * m.sheet_margin + s
    BH = material.sheet_width - 2 * m.sheet_margin + s
    keys = {
        "area": lambda p: p.length * p.width,
        "long": lambda p: max(p.length, p.width),
        "short": lambda p: min(p.length, p.width),
        "perim": lambda p: p.length + p.width,
    }
    ordered = sorted(pieces, key=keys[order], reverse=True)
    bins: list[tuple[_Bin, list]] = []
    for pc in ordered:
        best = None
        for bi, (b, _) in enumerate(bins):
            for rot in ((False, True) if pc.can_rotate else (False,)):
                w, h = (pc.width, pc.length) if rot else (pc.length, pc.width)
                sc = b.score(w + s, h + s, rule)
                if sc and (best is None or (bi, sc[0]) < (best[0], best[1])):
                    best = (bi, sc[0], sc[1], sc[2], rot, w, h)
        if best is None:
            b = _Bin(BW, BH)
            bins.append((b, []))
            for rot in ((False, True) if pc.can_rotate else (False,)):
                w, h = (pc.width, pc.length) if rot else (pc.length, pc.width)
                sc = b.score(w + s, h + s, rule)
                if sc and (best is None or sc[0] < best[1]):
                    best = (len(bins) - 1, sc[0], sc[1], sc[2], rot, w, h)
            if best is None:
                bins.pop()
                return None   # piece cannot fit an empty sheet (reported by caller)
        bi, _, x, y, rot, w, h = best
        bins[bi][0].place(x, y, w + s, h + s)
        bins[bi][1].append((pc, x, y, rot, w, h))
    return bins


def nest_router(parts: list[Part], material: Material, machine: MachineProfile,
                code: str | None = None) -> RouterNestResult:
    code = code or material.code
    s = machine.part_spacing
    usable_l = material.sheet_length - 2 * machine.sheet_margin
    usable_w = material.sheet_width - 2 * machine.sheet_margin
    pieces, unplaced = _pieces(parts, material, s)

    fits = []
    for pc in pieces:
        ok = (pc.length <= usable_l + 1e-9 and pc.width <= usable_w + 1e-9) or \
             (pc.can_rotate and pc.width <= usable_l + 1e-9 and pc.length <= usable_w + 1e-9)
        (fits if ok else unplaced).append(pc if ok else f"{pc.label} ({pc.length:.3f} x {pc.width:.3f}) exceeds sheet")

    best = None
    for order in ("area", "long", "short", "perim"):
        for rule in ("bssf", "baf", "bl"):
            bins = _run(fits, material, machine, order, rule)
            if bins is None:
                continue
            last_area = sum(w * h for _, _, _, _, w, h in bins[-1][1]) if bins else 0
            score = (len(bins), last_area)
            if best is None or score < best[0]:
                best = (score, bins)

    sheets = []
    small = set()
    for i, (_, placed) in enumerate(best[1] if best else [], 1):
        sh = RouterSheet(i, code, material.sheet_length, material.sheet_width)
        for pc, x, y, rot, w, h in placed:
            ox, oy = machine.sheet_margin + x, machine.sheet_margin + y
            for part, dx, dy, L, W in pc.members:
                if rot:   # piece turned 90 degrees: its length now runs along y
                    px, py, pl, pw = ox + dy, oy + dx, W, L
                else:
                    px, py, pl, pw = ox + dx, oy + dy, L, W
                sh.placements.append(RouterPlacement(part, f"{part.cabinet_tag} {part.name}",
                                                     round(px, 4), round(py, 4), pl, pw, rot,
                                                     part.grain_group, part.seq))
                if min(L, W) < machine.small_part_in:
                    small.add(f"{part.cabinet_tag} {part.name}")
        sheets.append(sh)
    return RouterNestResult(code, sheets, unplaced, sorted(small))


def nest_job_router(parts: list[Part], materials: dict[str, Material],
                    machine: MachineProfile) -> dict[str, RouterNestResult]:
    """Nest every material; pre-lam parts go on laminated sheets of their core."""
    groups: dict[str, list[Part]] = {}
    for p in parts:
        groups.setdefault(nest_material_key(p), []).append(p)
    out = {}
    for key, plist in groups.items():
        core = materials[key.split("+")[0]]
        if "+" in key and key.split("+")[1] not in materials:
            raise KeyError(f"Laminate {key.split('+')[1]} is not in the pricing table")
        out[key] = nest_router(plist, core, machine, key)
    return out

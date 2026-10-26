"""Guillotine panel-saw optimizer (two-stage rip / crosscut).

A beam/panel saw rips a sheet into strips along its length, then crosscuts each strip
into parts. That constraint (every cut goes edge to edge) is why panel-saw nesting is
different from router nesting. This is a first-fit-decreasing heuristic: simple,
explainable, and fast enough to run inside an estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Part, Material


def prepare_for_saw(parts: list[Part]) -> list[Part]:
    """What the panel saw actually cuts.

    * grain-matched fronts -> the one blank they are recut from
    * pre-lam finished ends -> an oversize core blank plus an oversize laminate piece;
      they are pressed together, then trimmed to size and machined
    """
    out = []
    for p in consolidate_grain_groups(parts):
        if p.laminate and p.prelam_blank:
            L, W = p.prelam_blank
            out.append(Part(p.cabinet_tag, f"PRELAM core blank [{p.name}]", p.material, L, W, p.qty))
            out.append(Part(p.cabinet_tag, f"PRELAM laminate [{p.name}]", p.laminate, L, W, p.qty))
        else:
            out.append(p)
    return out


def consolidate_grain_groups(parts: list[Part]) -> list[Part]:
    """Replace grain-matched fronts with the single blank they are cut from.

    The saw cuts the blank; the fronts are recut from it in sequence so the figure
    stays continuous. Nesting the blank (not the loose fronts) is what guarantees that.
    """
    out, seen = [], {}
    for p in parts:
        if p.grain_group and p.grain_blank:
            if p.grain_group not in seen:
                members = [m for m in parts if m.grain_group == p.grain_group]
                names = ", ".join(f"{m.seq}:{m.name}" for m in sorted(members, key=lambda m: m.seq))
                blank = Part(p.cabinet_tag, f"GM blank [{names}]", p.material,
                             p.grain_blank[0], p.grain_blank[1], p.qty, exposed=True)
                seen[p.grain_group] = blank
                out.append(blank)
        else:
            out.append(p)
    return out


@dataclass
class Placement:
    label: str
    x: float      # along sheet length
    y: float      # along sheet width
    length: float
    width: float
    rotated: bool = False


@dataclass
class Strip:
    y: float
    width: float
    used: float = 0.0
    placements: list[Placement] = field(default_factory=list)


@dataclass
class Sheet:
    index: int
    material: str
    length: float
    width: float
    strips: list[Strip] = field(default_factory=list)

    @property
    def used_width(self) -> float:
        return sum(s.width for s in self.strips)

    @property
    def part_area(self) -> float:
        return sum(p.length * p.width for s in self.strips for p in s.placements)

    @property
    def yield_pct(self) -> float:
        return 100.0 * self.part_area / (self.length * self.width)


@dataclass
class NestResult:
    material: str
    sheets: list[Sheet]
    unplaced: list[str]

    @property
    def sheet_count(self) -> int:
        return len(self.sheets)

    @property
    def yield_pct(self) -> float:
        if not self.sheets:
            return 0.0
        total = sum(s.length * s.width for s in self.sheets)
        return 100.0 * sum(s.part_area for s in self.sheets) / total

    def summary(self) -> dict:
        return {
            "material": self.material,
            "sheets": self.sheet_count,
            "yield_pct": round(self.yield_pct, 1),
            "unplaced": self.unplaced,
            "per_sheet_yield": [round(s.yield_pct, 1) for s in self.sheets],
        }


def _expand(parts: list[Part], material: Material, usable_l: float, usable_w: float):
    pieces, unplaced = [], []
    for p in parts:
        for _ in range(p.qty):
            label = f"{p.cabinet_tag} {p.name}"
            fits_normal = p.length <= usable_l and p.width <= usable_w
            fits_rot = (not material.grained) and p.width <= usable_l and p.length <= usable_w
            if not (fits_normal or fits_rot):
                unplaced.append(f"{label} ({p.length} x {p.width}) exceeds sheet")
                continue
            pieces.append((label, p.length, p.width, fits_normal, fits_rot))
    return pieces, unplaced


def _nest_once(pieces, material: Material, usable_l: float, usable_w: float,
               kerf: float, trim: float, order: str, recut_max: float) -> list[Sheet]:
    def key(pc):
        _, l, w, fn, fr = pc
        rip = w if fn and not (fr and w > l) else l   # preferred rip width
        long_ = max(l, w) if fr else l
        return {"rip": (rip, long_), "length": (long_, rip), "area": (l * w,)}[order]

    pieces = sorted(pieces, key=key, reverse=True)
    sheets: list[Sheet] = []

    def open_strip(width: float) -> Strip | None:
        for sh in sheets:
            if sh.used_width + len(sh.strips) * kerf + width <= usable_w + 1e-9:
                st = Strip(y=trim + sh.used_width + len(sh.strips) * kerf, width=width)
                sh.strips.append(st)
                return st
        return None

    for label, l, w, fits_normal, fits_rot in pieces:
        options = []  # (crosscut length, rip width, rotated)
        if fits_normal:
            options.append((l, w, False))
        if fits_rot:
            options.append((w, l, True))
        best = None   # (waste, strip, option)
        for sh in sheets:
            for st in sh.strips:
                for cl, rw, rot in options:
                    need = cl + (kerf if st.placements else 0)
                    if st.width >= rw and st.used + need <= usable_l + 1e-9:
                        waste = st.width - rw
                        if waste <= recut_max and (best is None or waste < best[0]):
                            best = (waste, st, (cl, rw, rot))
        if best is None:
            # open a new strip with the narrowest rip width that fits
            for cl, rw, rot in sorted(options, key=lambda o: o[1]):
                st = open_strip(rw)
                if st is not None:
                    best = (0.0, st, (cl, rw, rot))
                    break
        if best is None:
            sheets.append(Sheet(len(sheets) + 1, material.code,
                                material.sheet_length, material.sheet_width))
            cl, rw, rot = sorted(options, key=lambda o: o[1])[0]
            best = (0.0, open_strip(rw), (cl, rw, rot))
        _, st, (cl, rw, rot) = best
        x = trim + st.used + (kerf if st.placements else 0)
        st.placements.append(Placement(label, round(x, 4), round(st.y, 4), cl, rw, rot))
        st.used = x - trim + cl
    return sheets


def nest_parts(parts: list[Part], material: Material, kerf: float = 0.125,
               trim: float = 0.25) -> NestResult:
    """Run several orderings / recut limits and keep the best layout
    (fewest sheets, then the most material left over on the last sheet)."""
    usable_l = material.sheet_length - 2 * trim
    usable_w = material.sheet_width - 2 * trim
    pieces, unplaced = _expand(parts, material, usable_l, usable_w)
    best = None
    for order in ("rip", "length", "area"):
        for recut_max in (0.0, 1.0, 3.0, 6.0, 12.0, 99.0):
            sheets = _nest_once(pieces, material, usable_l, usable_w, kerf, trim, order, recut_max)
            score = (len(sheets), sheets[-1].part_area if sheets else 0)
            if best is None or score < best[0]:
                best = (score, sheets)
    return NestResult(material.code, best[1] if best else [], unplaced)


def saw_cut_list(result: NestResult) -> list[dict]:
    """Operator-facing rip/crosscut list, one row per strip."""
    rows = []
    for sh in result.sheets:
        for n, st in enumerate(sh.strips, 1):
            rows.append({
                "sheet": sh.index,
                "strip": n,
                "rip_width": round(st.width, 4),
                "crosscuts": "; ".join(f"{p.length:.4f} [{p.label}]" for p in st.placements),
            })
    return rows


def nest_svg(sheet: Sheet, scale: float = 8.0) -> str:
    """Simple cutting diagram for one sheet."""
    W, H = sheet.length * scale, sheet.width * scale
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W + 20:.0f} {H + 40:.0f}" '
           f'font-family="Arial" font-size="10">',
           f'<text x="10" y="14" font-size="13">Sheet {sheet.index} - {sheet.material} - '
           f'yield {sheet.yield_pct:.1f}%</text>',
           f'<rect x="10" y="24" width="{W:.1f}" height="{H:.1f}" fill="#f3efe6" stroke="#333"/>']
    for st in sheet.strips:
        for p in st.placements:
            x, y = 10 + p.x * scale, 24 + p.y * scale
            w, h = p.length * scale, p.width * scale
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                       f'fill="#cfe0f0" stroke="#1F4E79"/>')
            if w > 40 and h > 18:
                out.append(f'<text x="{x + 3:.1f}" y="{y + 12:.1f}">{p.label}</text>')
                out.append(f'<text x="{x + 3:.1f}" y="{y + 24:.1f}">{p.length:.3f} x {p.width:.3f}</text>')
    out.append("</svg>")
    return "\n".join(out)

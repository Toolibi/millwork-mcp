# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""Nested-sheet exports for a HOMAG / woodWOP nested-based router.

One DXF per sheet, in millimetres, using the layer names woodWOP's DXF import reads
(per HOMAG's DXF import documentation):

  Werkstk_<thickness>            the sheet (workpiece) outline
  V_Bohr<mode>_<depth>           vertical bores - circles at true diameter
  V_Fraes_<Z>T<tool>             vertical milling - cut-outs and dados

Cut-out paths are drawn on the TOOL CENTRE (part outline offset out by the bit radius),
so no cutter compensation is needed. Parts are placed by rotation only, never mirrored,
so every part keeps its machined face up.

Edge (horizontal) bores are not on the nest when the router has no horizontal boring
unit; they go to edge_boring_list.csv for the separate horizontal borer.

Everything here is UNVERIFIED until one sheet has been simulated in woodWOP and run on
the machine: tool numbers, Z conventions and drilling modes come from the machine
profile, and any value still tagged "assumed" is listed in the run's review flags.
"""
from __future__ import annotations

import csv
from pathlib import Path

import ezdxf

from .machine import MachineProfile
from .models import Material
from .router_nest import RouterNestResult, RouterPlacement, RouterSheet

MM = 25.4


def _fmt(v: float) -> str:
    """woodWOP layer numbers: decimal comma-free, underscore for the decimal point."""
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", "_")


def _to_sheet(pl: RouterPlacement, lx: float, ly: float) -> tuple[float, float]:
    """Part-local (x across width, y along length) -> sheet inches, rotation only.

    Not rotated: the part's length runs along the sheet X (rotate the part -90 deg).
    Rotated:     the part's length runs along the sheet Y (no rotation)."""
    if pl.rotated:
        return pl.x + lx, pl.y + ly
    return pl.x + ly, pl.y + (pl.part.width - lx)


def sheet_ops(sheet: RouterSheet, machine: MachineProfile, thickness: float) -> dict:
    """Machining for one sheet, in sheet coordinates (inches), split by kind."""
    bores, dados, grooves, edge, contours = [], [], [], [], []
    r = machine.tool_dia / 2
    for pl in sheet.placements:
        part = pl.part
        for o in part.machining:
            if o["op"] in ("drill", "pocket"):
                x, y = _to_sheet(pl, o["x"], o["y"])
                depth = thickness + 0.02 if o.get("through") else o["depth"]
                bores.append({"label": pl.label, "x": x, "y": y, "dia_mm": o["dia_mm"],
                              "depth": depth, "through": bool(o.get("through"))})
            elif o["op"] == "dado":
                a = _to_sheet(pl, o["x"] + o["width"] / 2, 0)
                b = _to_sheet(pl, o["x"] + o["width"] / 2, part.length)
                dados.append({"label": pl.label, "a": a, "b": b, "depth": o["depth"],
                              "width": o["width"]})
            elif o["op"] == "pgroove":
                x, y = _to_sheet(pl, o["x"], o.get("y", 0))
                grooves.append({"label": pl.label, "x": x, "y": y, "system": o["system"]})
            elif o["op"] == "edge_drill":
                edge.append({"label": pl.label, **o})
        contours.append({"label": pl.label, "x0": pl.x - r, "y0": pl.y - r,
                         "x1": pl.x + pl.length + r, "y1": pl.y + pl.width + r,
                         "area": pl.length * pl.width, "pl": pl})
    contours.sort(key=lambda c: c["area"])          # smallest parts cut first
    return {"bores": bores, "dados": dados, "grooves": grooves, "edge": edge,
            "contours": contours}


def export_sheet_dxf(sheet: RouterSheet, material: Material, machine: MachineProfile,
                     path: Path) -> Path:
    t = material.thickness
    ops = sheet_ops(sheet, machine, t)
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.header["$LASTSAVEDBY"] = "millwork-mcp (c) Prince Boafo"
    msp = doc.modelspace()

    def layer(name: str, color: int) -> str:
        if name not in doc.layers:
            doc.layers.add(name, color=color)
        return name

    def mm(pt):
        return (round(pt[0] * MM, 3), round(pt[1] * MM, 3))

    # workpiece
    wl = layer(f"Werkstk_{_fmt(t * MM)}", 7)
    msp.add_lwpolyline([mm((0, 0)), mm((sheet.length, 0)), mm((sheet.length, sheet.width)),
                        mm((0, sheet.width))], close=True, dxfattribs={"layer": wl})

    # 1) boring first, while the sheet is whole
    for b in ops["bores"]:
        dl = layer(f"V_Bohr{machine.drill_mode}_{_fmt(b['depth'] * MM)}", 1)
        msp.add_circle(mm((b["x"], b["y"])), radius=b["dia_mm"] / 2, dxfattribs={"layer": dl})
    # 2) dados / grooves
    for d in ops["dados"]:
        gl = layer(f"V_Fraes_{_fmt(d['depth'] * MM)}T{machine.dado_tool}", 5)
        msp.add_lwpolyline([mm(d["a"]), mm(d["b"])], dxfattribs={"layer": gl})
    for g in ops["grooves"]:
        # Clamex P-System grooves need the Lamello cutter's own macro: marked, not milled
        gl = layer(f"LAMELLO_{g['system']}_T{machine.clamex_tool}", 2)
        msp.add_point(mm((g["x"], g["y"])), dxfattribs={"layer": gl})
    # 3) cut-outs, smallest part first, on the tool centre
    z_through = -(machine.cut_through * MM)          # negative = below the underside
    cl = layer(f"V_Fraes_{_fmt(z_through)}T{machine.contour_tool}", 3)
    for c in ops["contours"]:
        msp.add_lwpolyline([mm((c["x0"], c["y0"])), mm((c["x1"], c["y0"])),
                            mm((c["x1"], c["y1"])), mm((c["x0"], c["y1"]))],
                           close=True, dxfattribs={"layer": cl})
    # labels (ignored by the woodWOP import, useful to the operator)
    ll = layer("LABEL", 8)
    for pl in sheet.placements:
        msp.add_text(pl.label, height=12, dxfattribs={"layer": ll}).set_placement(
            mm((pl.x + 0.5, pl.y + pl.width / 2)))
    doc.saveas(path)
    return path


def export_router_nests(nests: dict[str, RouterNestResult], materials: dict[str, Material],
                        machine: MachineProfile, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    dxfs, svgs, rows, edge_rows = [], [], [], []
    for key, res in nests.items():
        core = materials[key.split("+")[0]]
        for sh in res.sheets:
            safe = key.replace("+", "_PRELAM_")
            dxfs.append(export_sheet_dxf(sh, core, machine, out_dir / f"{safe}_sheet{sh.index}.dxf"))
            p = out_dir / f"{safe}_sheet{sh.index}.svg"
            p.write_text(router_svg(sh, machine, thickness=core.thickness, grained=core.grained))
            svgs.append(p)
            ops = sheet_ops(sh, machine, core.thickness)
            for n, c in enumerate(ops["contours"], 1):
                pl = c["pl"]
                rows.append({"material": key, "sheet": sh.index, "cut_order": n,
                             "part": c["label"], "x_in": round(pl.x, 3), "y_in": round(pl.y, 3),
                             "length": round(pl.length, 4), "width": round(pl.width, 4),
                             "rotated": int(pl.rotated), "grain_group": pl.group or "",
                             "seq": pl.seq or ""})
            if not machine.horizontal_boring:
                for e in ops["edge"]:
                    edge_rows.append({"material": key, "sheet": sh.index, "part": e["label"],
                                      "edge": e["edge"], "x_from_front_mm": round(e["x"] * MM, 1),
                                      "dia_mm": e["dia_mm"], "depth_mm": round(e["depth"] * MM, 1)})
    with open(out_dir / "nest_list.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["material"])
        w.writeheader()
        w.writerows(rows)
    edge_path = out_dir / "edge_boring_list.csv"
    with open(edge_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["material", "sheet", "part", "edge", "x_from_front_mm",
                                          "dia_mm", "depth_mm"])
        w.writeheader()
        w.writerows(edge_rows)
    return {"sheet_dxfs": [str(d) for d in dxfs], "sheet_svgs": [str(s) for s in svgs],
            "nest_list_csv": str(out_dir / "nest_list.csv"), "edge_boring_list_csv": str(edge_path),
            "edge_bores": len(edge_rows)}


# colours for the operator's nest diagram (the DXF carries the real woodWOP layers)
_SVG_KEY = [
    ("shelf", "#2e7d32", "Shelf-pin hole 5mm"),
    ("dowel", "#c62828", "Dowel / joint bore (face)"),
    ("edge", "#c62828", "Edge bore (horizontal borer)"),
    ("cup", "#6a1b9a", "Hinge cup 35mm"),
    ("cam", "#ef6c00", "Cam / access bore"),
    ("through", "#000000", "Through hole"),
    ("dado", "#1565c0", "Dado (1/4\" back)"),
    ("clamex", "#00838f", "Clamex P-14 groove"),
    ("cut", "#555555", "Cut-out path (tool centre)"),
]


def _bore_kind(b: dict) -> str:
    if b.get("through"):
        return "through"
    d = b["dia_mm"]
    if d >= 30:
        return "cup"
    if d >= 10 or b.get("role") in ("cam", "access"):
        return "cam"
    if d <= 5.01:
        return "shelf"
    return "dowel"


def _edge_points(pl: RouterPlacement, o: dict) -> tuple[tuple, tuple]:
    """Where an edge bore enters the part, and a point inward along the bore, in sheet inches."""
    part, e, x, depth = pl.part, o.get("edge", "W1"), o["x"], o.get("depth", 1.0)
    if e in ("W1", "W2"):                       # ends of the part (across its width)
        y0, y1 = (0.0, depth) if e == "W1" else (part.length, part.length - depth)
        return _to_sheet(pl, x, y0), _to_sheet(pl, x, y1)
    x0, x1 = (0.0, depth) if e == "L1" else (part.width, part.width - depth)
    return _to_sheet(pl, x0, x), _to_sheet(pl, x1, x)


def router_svg(sheet: RouterSheet, machine: MachineProfile, scale: float = 8.0,
               thickness: float = 0.75, grained: bool = False) -> str:
    """Operator's nest diagram: parts, cut order, and every machining operation drawn on the
    part where the router (or the horizontal borer) will make it."""
    W, H = sheet.length * scale, sheet.width * scale
    ox, oy = 10, 24
    legend_h = 44
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W + 20:.0f} {H + 40 + legend_h:.0f}" '
           f'font-family="Arial" font-size="10">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           f'<text x="10" y="14" font-size="13">Sheet {sheet.index} - {sheet.material} - nested, '
           f'yield {sheet.yield_pct:.1f}%' + (' - grain runs left to right' if grained else '') + '</text>',
           f'<rect x="{ox}" y="{oy}" width="{W:.1f}" height="{H:.1f}" fill="#f3efe6" stroke="#333"/>']
    P = lambda pt: (ox + pt[0] * scale, oy + pt[1] * scale)
    col = {k: c for k, c, _ in _SVG_KEY}
    ops = sheet_ops(sheet, machine, thickness)
    order = {id(pl): n for n, pl in enumerate(sheet.cut_order(), 1)}

    for pl in sheet.placements:                                  # parts
        x, y = P((pl.x, pl.y))
        w, h = pl.length * scale, pl.width * scale
        fill = "#e8d8b8" if pl.group else "#dfe9f3"
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                   f'fill="{fill}" stroke="#1F4E79" stroke-width="0.8"/>')
    for c in ops["contours"]:                                    # cut path, tool centre
        x0, y0 = P((c["x0"], c["y0"])); x1, y1 = P((c["x1"], c["y1"]))
        out.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" '
                   f'fill="none" stroke="{col["cut"]}" stroke-width="0.6" stroke-dasharray="4 3"/>')
    for d in ops["dados"]:                                       # dados
        (ax, ay), (bx, by) = P(d["a"]), P(d["b"])
        out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                   f'stroke="{col["dado"]}" stroke-width="{max(d["width"] * scale, 2.2):.1f}" opacity="0.85"/>')
    for g in ops["grooves"]:                                     # Clamex P-14 slot
        gx, gy = P((g["x"], g["y"]))
        out.append(f'<rect x="{gx - 6:.1f}" y="{gy - 2:.1f}" width="12" height="4" rx="2" '
                   f'fill="{col["clamex"]}"/>')
    for pl in sheet.placements:                                  # edge bores
        for o in pl.part.machining:
            if o["op"] != "edge_drill":
                continue
            a, b = _edge_points(pl, o)
            (ax, ay), (bx, by) = P(a), P(b)
            out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                       f'stroke="{col["edge"]}" stroke-width="1.6"/>'
                       f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="1.6" fill="{col["edge"]}"/>')
    for b in ops["bores"]:                                       # face bores, to scale
        bx, by = P((b["x"], b["y"]))
        k = _bore_kind(b)
        r = max(b["dia_mm"] / 25.4 / 2 * scale, 1.7)
        if k == "through":
            out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{r:.1f}" fill="#000"/>')
        else:
            out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{r:.1f}" fill="{col[k]}" '
                       f'fill-opacity="{0.35 if k == "cup" else 0.9}" stroke="{col[k]}" stroke-width="0.6"/>')
    for pl in sheet.placements:                                  # labels on top
        x, y = P((pl.x, pl.y))
        w, h = pl.length * scale, pl.width * scale
        if w > 24 and h > 18:
            tag = f" #{pl.seq}" if pl.seq else ""
            text = f"{order[id(pl)]}. {pl.label}{tag}"
            fit = int((w - 6) / 5.8)                              # ~5.8 px per character
            if len(text) > fit:                                  # narrow strip: cut-order number only
                text = f"{order[id(pl)]}." if fit < 10 else text[:fit - 1] + "…"
            out.append(f'<text x="{x + 3:.1f}" y="{y + 12:.1f}" paint-order="stroke" stroke="#fff" '
                       f'stroke-width="2.5">{text}</text>')
    lx, ly = 10, oy + H + 18                                     # legend
    for k, c, name in _SVG_KEY:
        if k in ("dado",):
            sym = f'<line x1="{lx}" y1="{ly - 3}" x2="{lx + 12}" y2="{ly - 3}" stroke="{c}" stroke-width="3"/>'
        elif k == "cut":
            sym = f'<line x1="{lx}" y1="{ly - 3}" x2="{lx + 12}" y2="{ly - 3}" stroke="{c}" stroke-dasharray="3 2"/>'
        elif k == "clamex":
            sym = f'<rect x="{lx}" y="{ly - 5}" width="12" height="4" rx="2" fill="{c}"/>'
        elif k == "edge":
            sym = f'<line x1="{lx}" y1="{ly - 3}" x2="{lx + 12}" y2="{ly - 3}" stroke="{c}" stroke-width="1.6"/>'
        else:
            sym = f'<circle cx="{lx + 6}" cy="{ly - 3}" r="4" fill="{c}"/>'
        out.append(sym + f'<text x="{lx + 16}" y="{ly}">{name}</text>')
        lx += 18 + 6.2 * len(name) + 14
        if lx > W - 150:                                         # wrap to a second row
            lx, ly = 10, ly + 20
    out.append("</svg>")
    return "\n".join(out)

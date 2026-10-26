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
            p.write_text(router_svg(sh, machine))
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


def router_svg(sheet: RouterSheet, machine: MachineProfile, scale: float = 8.0) -> str:
    W, H = sheet.length * scale, sheet.width * scale
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W + 20:.0f} {H + 40:.0f}" '
           f'font-family="Arial" font-size="10">',
           f'<text x="10" y="14" font-size="13">Sheet {sheet.index} - {sheet.material} - nested, '
           f'yield {sheet.yield_pct:.1f}%</text>',
           f'<rect x="10" y="24" width="{W:.1f}" height="{H:.1f}" fill="#f3efe6" stroke="#333"/>']
    for n, pl in enumerate(sheet.cut_order(), 1):
        x, y = 10 + pl.x * scale, 24 + pl.y * scale
        w, h = pl.length * scale, pl.width * scale
        fill = "#e8d8b8" if pl.group else "#cfe0f0"
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                   f'fill="{fill}" stroke="#1F4E79"/>')
        if w > 40 and h > 18:
            tag = f" #{pl.seq}" if pl.seq else ""
            out.append(f'<text x="{x + 3:.1f}" y="{y + 12:.1f}">{n}. {pl.label}{tag}</text>')
    out.append("</svg>")
    return "\n".join(out)

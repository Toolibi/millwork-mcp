"""Exports: per-part DXF files with machining layers, cut-list CSV, and saw list CSV.

DXF layers carry the machining intent (operation, tool size, depth) in their names, the
way most CAM import tools (including S2M Center's DXF import) map CAD geometry to
machining. The LAYERS templates below are placeholders: match them to the layer
names in your own S2M / CAM import setup before running real parts.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import ezdxf

from .models import Part, Material
from .nesting import NestResult, saw_cut_list

LAYERS = {
    "outline": "CUT_THRU_T{thk:.3f}",
    "drill": "DRILL_{dia:g}MM_D{depth:.3f}",
    "pocket": "POCKET_{dia:g}MM_D{depth:.3f}",
    "dado": "DADO_W{width:.3f}_D{depth:.3f}",
    "drill_thru": "DRILL_{dia:g}MM_THRU",
    "edge_drill": "HDRILL_{dia:g}MM_D{depth:.3f}_{edge}",   # horizontal bore into an end edge
    "pgroove_face": "LAMELLO_{system}_FACE",
    "pgroove_edge": "LAMELLO_{system}_EDGE_{edge}",
}
P_GROOVE_LEN = 55 / 25.4   # approximate P-System groove length; your tool defines the real one


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")


def part_to_dxf(part: Part, material: Material, path: Path) -> Path:
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.IN
    msp = doc.modelspace()
    L, W = part.length, part.width   # x = width, y = length (grain vertical)

    outline_layer = LAYERS["outline"].format(thk=material.thickness)
    doc.layers.add(outline_layer, color=7)
    msp.add_lwpolyline([(0, 0), (W, 0), (W, L), (0, L)], close=True,
                       dxfattribs={"layer": outline_layer})

    def layer_for(name: str, color: int) -> str:
        if name not in doc.layers:
            doc.layers.add(name, color=color)
        return name

    for op in part.machining:
        kind = op["op"]
        if kind == "drill" and op.get("through"):
            layer = layer_for(LAYERS["drill_thru"].format(dia=op["dia_mm"]), 6)
            msp.add_circle((op["x"], op["y"]), radius=op["dia_mm"] / 25.4 / 2,
                           dxfattribs={"layer": layer})
        elif kind == "edge_drill":
            # drawn as a line from the end edge inward, showing bore position and depth
            layer = layer_for(LAYERS["edge_drill"].format(dia=op["dia_mm"], depth=op["depth"],
                                                          edge=op["edge"]), 4)
            y0 = 0.0 if op["edge"] == "W1" else L
            y1 = op["depth"] if op["edge"] == "W1" else L - op["depth"]
            msp.add_line((op["x"], y0), (op["x"], y1), dxfattribs={"layer": layer})
        elif kind == "pgroove":
            half = P_GROOVE_LEN / 2
            if op["orient"] == "face":
                layer = layer_for(LAYERS["pgroove_face"].format(system=op["system"]), 2)
                y = op["y"]
                msp.add_lwpolyline([(op["x"] - half, y - 0.1), (op["x"] + half, y - 0.1),
                                    (op["x"] + half, y + 0.1), (op["x"] - half, y + 0.1)],
                                   close=True, dxfattribs={"layer": layer})
            else:
                layer = layer_for(LAYERS["pgroove_edge"].format(system=op["system"],
                                                                edge=op["edge"]), 2)
                y = 0.0 if op["edge"] == "W1" else L
                msp.add_line((op["x"] - half, y), (op["x"] + half, y), dxfattribs={"layer": layer})
        elif kind in ("drill", "pocket"):
            layer = LAYERS[kind].format(dia=op["dia_mm"], depth=op["depth"])
            if layer not in doc.layers:
                doc.layers.add(layer, color=1 if kind == "drill" else 3)
            msp.add_circle((op["x"], op["y"]), radius=op["dia_mm"] / 25.4 / 2,
                           dxfattribs={"layer": layer})
        elif kind == "dado":
            layer = LAYERS["dado"].format(width=op["width"], depth=op["depth"])
            if layer not in doc.layers:
                doc.layers.add(layer, color=5)
            x0, x1 = op["x"], op["x"] + op["width"]
            msp.add_lwpolyline([(x0, 0), (x1, 0), (x1, L), (x0, L)], close=True,
                               dxfattribs={"layer": layer})

    doc.layers.add("LABEL", color=8)
    msp.add_text(f"{part.cabinet_tag} {part.name}", height=0.5,
                 dxfattribs={"layer": "LABEL"}).set_placement((0.5, L / 2))
    doc.saveas(path)
    return path


def export_part_dxfs(parts: list[Part], materials: dict[str, Material], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for p in parts:  # plain panels still get an outline so the CAM can cut them
        fname = out_dir / f"{_safe(p.cabinet_tag)}_{_safe(p.name)}.dxf"
        files.append(part_to_dxf(p, materials[p.material], fname))
    return files


def export_cutlist_csv(parts: list[Part], path: Path,
                       materials: dict[str, Material] | None = None) -> Path:
    materials_grained = {k: m.grained for k, m in (materials or {}).items()}
    fields = ["cabinet", "part", "material", "qty", "length", "width", "grain",
              "grain_group", "seq", "exposed",
              "eb_L1", "eb_L2", "eb_W1", "eb_W2", "edgeband_lf", "machining_ops"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for p in parts:
            e = p.edgeband
            w.writerow({"cabinet": p.cabinet_tag, "part": p.name, "material": p.material,
                        "qty": p.qty, "length": f"{p.length:.4f}", "width": f"{p.width:.4f}",
                        "grain": "along length" if materials_grained.get(p.material) else "",
                        "grain_group": p.grain_group or "", "seq": p.seq or "",
                        "exposed": int(p.exposed),
                        "eb_L1": int(bool(e.get("L1"))), "eb_L2": int(bool(e.get("L2"))),
                        "eb_W1": int(bool(e.get("W1"))), "eb_W2": int(bool(e.get("W2"))),
                        "edgeband_lf": f"{p.edgeband_lf:.2f}", "machining_ops": len(p.machining)})
    return path


def export_saw_list_csv(nests: dict[str, NestResult], path: Path) -> Path:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["material", "sheet", "strip", "rip_width", "crosscuts"])
        w.writeheader()
        for code, res in nests.items():
            for row in saw_cut_list(res):
                w.writerow({"material": code, **row})
    return path

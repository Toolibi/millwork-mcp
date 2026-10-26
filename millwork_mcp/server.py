"""millwork-mcp: an MCP server that gives an AI assistant real millwork tools.

The model reads drawings and builds the cabinet list; these tools do everything that
must be exact (parts, nesting, pricing, machining files). Nothing here sends code to a
machine - outputs go to a human and to CAM (e.g. S2M Center) for verification.

Run:  python -m millwork_mcp.server
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .construction import build_parts, review_job
from .estimate import load_pricing, materials_from_pricing
from .models import Cabinet
from .joinery import METHODS
from .nesting import prepare_for_saw, nest_parts, saw_cut_list
from .machine import machine_from_pricing
from .pipeline import job_from_dict, run_job
from .router_nest import nest_job_router

ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = Path(os.environ.get("MILLWORK_PRICING", ROOT / "examples" / "pricing.json"))
OUTPUT_ROOT = Path(os.environ.get("MILLWORK_OUTPUT", ROOT / "output"))

mcp = FastMCP("millwork")

@mcp.tool()
def review_cabinet_schedule(job: dict) -> dict:
    """Check a cabinet schedule extracted from drawings for likely errors before estimating.
    Returns review flags (duplicate tags, odd sizes, oversized doors, sink-base drawers...).
    Job format (inches): {job_number, name, case_material, door_material, exposed_material,
    joinery: dowel|confirmat|cam_dowel|lamello_clamex, front_grain: vertical|horizontal,
    grain_match: bool, finished_end_method: prelam|exposed_material, finished_end_laminate,
    nailer_material, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base|bookcase|
    microwave, width, height, depth, qty, doors, drawers, adj_shelves, fixed_shelves,
    opening_height, finished_ends: ""|L|R|LR, joinery, against_wall, notes}]}
    """
    j = job_from_dict(job)
    mats = materials_from_pricing(load_pricing(PRICING_PATH))
    return {"cabinets": len(j.cabinets), "flags": review_job(j, mats)}


@mcp.tool()
def build_cutlist(job: dict) -> dict:
    """Explode a cabinet schedule into a part-by-part cut list using the shop's
    construction standard (frameless, 32mm system).
    Job format (inches): {job_number, name, case_material, door_material, exposed_material,
    joinery: dowel|confirmat|cam_dowel|lamello_clamex, front_grain: vertical|horizontal,
    grain_match: bool, finished_end_method: prelam|exposed_material, finished_end_laminate,
    nailer_material, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base|bookcase|
    microwave, width, height, depth, qty, doors, drawers, adj_shelves, fixed_shelves,
    opening_height, finished_ends: ""|L|R|LR, joinery, against_wall, notes}]}
    """
    j = job_from_dict(job)
    mats = materials_from_pricing(load_pricing(PRICING_PATH))
    parts = [p.to_dict() for c in j.cabinets for p in build_parts(c, j, materials=mats)]
    for p in parts:
        p.pop("machining")
    return {"part_lines": len(parts), "parts": parts}


@mcp.tool()
def nest_sheets(job: dict, material: str) -> dict:
    """Nest one material for the shop's machine.

    Nested-based router (the shop's Stiles/HOMAG): MaxRects nesting with tool spacing,
    grain lock, grain-matched fronts kept together in sequence, pre-lam parts on laminated
    sheets (use material "CORE+LAMINATE", e.g. "MEL-WHT-34+HPL-WHT"). Returns sheets, yield,
    small parts and the cut order (smallest part first).
    Panel saw shop: two-stage rip/crosscut with the operator saw list."""
    pricing = load_pricing(PRICING_PATH)
    machine = machine_from_pricing(pricing)
    j = job_from_dict(job)
    mats = materials_from_pricing(pricing)
    parts = [p for c in j.cabinets for p in build_parts(c, j, materials=mats)]
    if machine.is_nested:
        nests = nest_job_router(parts, mats, machine)
        if material not in nests:
            return {"error": f"No parts in {material}", "materials_in_job": sorted(nests)}
        res = nests[material]
        return {**res.summary(), "machine": machine.name,
                "cut_order": [[f"{n}. {p.label}" for n, p in enumerate(sh.cut_order(), 1)]
                              for sh in res.sheets]}
    res = nest_parts([p for p in prepare_for_saw(parts) if p.material == material], mats[material])
    return {**res.summary(), "machine": machine.name, "saw_list": saw_cut_list(res)}


@mcp.tool()
def get_machine_profile() -> dict:
    """The shop's machine profile (nested router or panel saw), with each setting's source:
    shop-confirmed, from a real program, or assumed. Anything assumed must be confirmed
    before nest programs are trusted on the machine."""
    m = machine_from_pricing(load_pricing(PRICING_PATH))
    return {"name": m.name, "type": m.type, "tool_dia_in": m.tool_dia,
            "part_spacing_in": m.part_spacing, "sheet_margin_in": m.sheet_margin,
            "horizontal_boring": m.horizontal_boring, "contour_tool": m.contour_tool,
            "dado_tool": m.dado_tool, "drill_mode": m.drill_mode, "sources": m.sources,
            "unconfirmed": m.unconfirmed()}


@mcp.tool()
def estimate_and_export(job: dict) -> dict:
    """Full run: cut list, nesting, estimate (materials, edgebanding, hardware, labor,
    overhead, markup) and shop files. On the nested router: one woodWOP-layer DXF per
    sheet (mm, tool-centre cut-outs, bores, dados), nest diagrams, cut order and the
    edge-boring list; on a panel-saw shop: saw list. Always: cut list with routes, CNC list,
    per-part DXFs.
    Job format (inches): {job_number, name, case_material, door_material, exposed_material,
    joinery: dowel|confirmat|cam_dowel|lamello_clamex, front_grain: vertical|horizontal,
    grain_match: bool, finished_end_method: prelam|exposed_material, finished_end_laminate,
    nailer_material, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base|bookcase|
    microwave, width, height, depth, qty, doors, drawers, adj_shelves, fixed_shelves,
    opening_height, finished_ends: ""|L|R|LR, joinery, against_wall, notes}]}
    """
    out = OUTPUT_ROOT / job["job_number"]
    return run_job(job, load_pricing(PRICING_PATH), out)


@mcp.tool()
def list_joinery_methods() -> dict:
    """Case joinery methods the engine can machine, with how each one is laid out."""
    return {k: {"name": m.name, "pitch_mm": m.pitch_mm, "glued": m.glue,
                "visible_on_face": m.shows_on_face, "knock_down": m.knock_down,
                "how": m.description} for k, m in METHODS.items()}


@mcp.tool()
def get_pricing() -> dict:
    """Return the pricing table in use so the assistant can explain an estimate."""
    return load_pricing(PRICING_PATH)


if __name__ == "__main__":
    mcp.run()

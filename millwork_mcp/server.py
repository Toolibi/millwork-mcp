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
from .nesting import nest_parts, saw_cut_list
from .pipeline import job_from_dict, run_job

ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = Path(os.environ.get("MILLWORK_PRICING", ROOT / "examples" / "pricing.json"))
OUTPUT_ROOT = Path(os.environ.get("MILLWORK_OUTPUT", ROOT / "output"))

mcp = FastMCP("millwork")

@mcp.tool()
def review_cabinet_schedule(job: dict) -> dict:
    """Check a cabinet schedule extracted from drawings for likely errors before estimating.
    Returns review flags (duplicate tags, odd sizes, oversized doors, sink-base drawers...).
    Job format: {job_number, name, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base,
    width, height, depth, qty, doors, drawers, adj_shelves, notes}]}, all in inches.
    """
    j = job_from_dict(job)
    return {"cabinets": len(j.cabinets), "flags": review_job(j)}


@mcp.tool()
def build_cutlist(job: dict) -> dict:
    """Explode a cabinet schedule into a part-by-part cut list using the shop's
    construction standard (frameless, 32mm system).
    Job format: {job_number, name, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base,
    width, height, depth, qty, doors, drawers, adj_shelves, notes}]}, all in inches.
    """
    j = job_from_dict(job)
    parts = [p.to_dict() for c in j.cabinets for p in build_parts(c, j)]
    for p in parts:
        p.pop("machining")
    return {"part_lines": len(parts), "parts": parts}


@mcp.tool()
def nest_panel_saw(job: dict, material: str) -> dict:
    """Optimize the panel-saw cutting for one material (two-stage rip/crosscut).
    Returns sheet count, yield and the operator rip/crosscut list."""
    j = job_from_dict(job)
    mats = materials_from_pricing(load_pricing(PRICING_PATH))
    parts = [p for c in j.cabinets for p in build_parts(c, j) if p.material == material]
    res = nest_parts(parts, mats[material])
    return {**res.summary(), "saw_list": saw_cut_list(res)}


@mcp.tool()
def estimate_and_export(job: dict) -> dict:
    """Full run: cut list, nesting, estimate (materials, edgebanding, hardware, labor,
    overhead, markup) and shop files (cut list CSV, saw list CSV, per-part DXFs with
    machining layers, nest diagrams).
    Job format: {job_number, name, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base,
    width, height, depth, qty, doors, drawers, adj_shelves, notes}]}, all in inches.
    """
    out = OUTPUT_ROOT / job["job_number"]
    return run_job(job, load_pricing(PRICING_PATH), out)


@mcp.tool()
def get_pricing() -> dict:
    """Return the pricing table in use so the assistant can explain an estimate."""
    return load_pricing(PRICING_PATH)


if __name__ == "__main__":
    mcp.run()

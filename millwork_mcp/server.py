# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""millwork-mcp: an MCP server that gives an AI assistant real millwork tools.

The model reads drawings and builds the cabinet list; these tools do everything that
must be exact (parts, nesting, pricing, machining files). Nothing here sends code to a
machine - outputs go to a human and to CAM (e.g. S2M Center) for verification.

Run:  python -m millwork_mcp.server
"""
from __future__ import annotations

import json
import os
import re
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
from .sheet_options import compare_sheet_options
from . import inventory as inv_mod

ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = Path(os.environ.get("MILLWORK_PRICING", ROOT / "examples" / "pricing.json"))
OUTPUT_ROOT = Path(os.environ.get("MILLWORK_OUTPUT", ROOT / "output"))
INVENTORY = inv_mod.InventoryStore(
    os.environ.get("MILLWORK_INVENTORY", ROOT / "data" / "inventory.json"),
    seed=ROOT / "examples" / "inventory.json")

NOTICE = ("millwork-mcp (c) 2026 Prince Boafo. Source-available under the PolyForm "
          "Noncommercial License 1.0.0; commercial use requires a license from the author "
          "(see COMMERCIAL.md).")

mcp = FastMCP("millwork", instructions=NOTICE)

@mcp.tool()
def review_cabinet_schedule(job: dict) -> dict:
    """Check a cabinet schedule extracted from drawings for likely errors before estimating.
    Returns review flags (duplicate tags, odd sizes, oversized doors, sink-base drawers,
    material codes not in the price list...). Take material codes from get_pricing; never
    invent them.
    Job format (inches): {job_number, name, case_material, door_material, exposed_material,
    joinery: dowel|confirmat|cam_dowel|lamello_clamex, front_grain: vertical|horizontal,
    grain_match: bool, finished_end_method: prelam|exposed_material, finished_end_laminate,
    nailer_material, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base|bookcase|
    microwave, width, height, depth, qty, doors, drawers, adj_shelves, fixed_shelves,
    opening_height, finished_ends: ""|L|R|LR, joinery, against_wall, notes}]}
    List only cabinets: never add appliance openings (range, dishwasher, fridge) or gaps as
    lines. against_wall defaults to true (wall cabinets get nailers) - only set false for a
    cabinet that truly stands free.
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
    List only cabinets: never add appliance openings (range, dishwasher, fridge) or gaps as
    lines. against_wall defaults to true (wall cabinets get nailers) - only set false for a
    cabinet that truly stands free.
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
    """Full run: cut list, nesting, estimate (materials, edgebanding, hardware, labor with
    hours broken down into machining, edgebanding, assembly, doors and drawers, overhead,
    markup) and shop files. On the nested router: one woodWOP-layer DXF per
    sheet (mm, tool-centre cut-outs, bores, dados), nest diagrams, cut order and the
    edge-boring list; on a panel-saw shop: saw list. Always: cut list with routes, CNC list,
    per-part DXFs.
    Job format (inches): {job_number, name, case_material, door_material, exposed_material,
    joinery: dowel|confirmat|cam_dowel|lamello_clamex, front_grain: vertical|horizontal,
    grain_match: bool, finished_end_method: prelam|exposed_material, finished_end_laminate,
    nailer_material, cabinets:[{tag, type: base|wall|tall|drawer_base|sink_base|bookcase|
    microwave, width, height, depth, qty, doors, drawers, adj_shelves, fixed_shelves,
    opening_height, finished_ends: ""|L|R|LR, joinery, against_wall, notes}]}
    List only cabinets: never add appliance openings (range, dishwasher, fridge) or gaps as
    lines. against_wall defaults to true (wall cabinets get nailers) - only set false for a
    cabinet that truly stands free.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(job.get("job_number", "job")))[:64].lstrip(".") or "job"
    out = OUTPUT_ROOT / safe
    return {**run_job(job, load_pricing(PRICING_PATH), out), "license": NOTICE}


@mcp.tool()
def compare_sheet_sizes(job: dict, material: str = "") -> dict:
    """Nest the job on every sheet size (and core) the supplier sells - 4x8, 5x8, 4x10,
    5x10... - and compare sheets, yield, material cost and router time per material, with a
    recommendation and the saving. Edgebanding, edge boring and assembly hours are the same
    for every size and are reported once. Leave material empty to compare every material in
    the job, or give one code (e.g. WO-RIFT-34). Same job format as review_cabinet_schedule.
    Read-only: writes no files."""
    return compare_sheet_options(job_from_dict(job), load_pricing(PRICING_PATH),
                                 material=material or None)


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


# ---------------------------------------------------------------- inventory control
# Read-only: inventory_status, check_job_materials, purchase_list.
# Change stock (require approval in the agent): everything else.

@mcp.tool()
def inventory_status(code: str = "", low_only: bool = False) -> dict:
    """Stock on hand, reserved for released jobs, and available, for sheet goods
    (material codes), edgebanding (EB-<material code>, in lineal feet) and hardware (HW-...),
    with locations, stock value, offcuts (remnants) on the rack and open job reservations.
    Give one code to see just that item; low_only=true lists only items that are SHORT or at
    their reorder point. Read-only."""
    p = load_pricing(PRICING_PATH)
    return inv_mod.status(INVENTORY.read(), p, code.strip(), low_only)


@mcp.tool()
def check_job_materials(job: dict, use_remnants: bool = True) -> dict:
    """Before releasing a job to the shop: what it takes from stock (sheets from the actual
    nest, edgebanding per material, hardware counted from the construction), what is short,
    the cost to buy the shortage, and which offcuts on the rack can replace full sheets.
    Same job format as review_cabinet_schedule. Read-only."""
    p = load_pricing(PRICING_PATH)
    return inv_mod.job_needs(job_from_dict(job), p, INVENTORY.read(), use_remnants)


@mcp.tool()
def purchase_list() -> dict:
    """Draft order: shortages for reserved jobs plus items at their reorder point, rounded
    up to pack size (edgebanding rolls, hardware boxes), with costs. A draft for a person to
    send - this tool orders nothing. Read-only."""
    return inv_mod.purchase_list(INVENTORY.read(), load_pricing(PRICING_PATH))


@mcp.tool()
def reserve_job_materials(job: dict, use_remnants: bool = True) -> dict:
    """Set stock aside for a job released to the shop (sheets, edgebanding, hardware and the
    offcuts the plan uses). Re-running for the same job_number replaces its reservation.
    Shortages stay visible as negative available and show up on purchase_list. CHANGES STOCK
    - confirm with the user first."""
    p = load_pricing(PRICING_PATH)
    with INVENTORY.transaction() as inv:
        return inv_mod.reserve(inv, p, job_from_dict(job), use_remnants)


@mcp.tool()
def consume_job_materials(job_number: str, remnants_left: list[dict] = [],  # noqa: B006
                          note: str = "") -> dict:
    """The job has been cut: take its reserved quantities off on-hand and remove the offcuts
    it used. remnants_left logs offcuts worth keeping, each {material, length, width,
    location} in inches, length along the grain, 12" minimum. CHANGES STOCK - confirm with
    the user first."""
    p = load_pricing(PRICING_PATH)
    with INVENTORY.transaction() as inv:
        return inv_mod.consume(inv, job_number, list(remnants_left or []), p, note)


@mcp.tool()
def release_job_materials(job_number: str) -> dict:
    """Cancel a job's reservation and put the stock back to available. CHANGES STOCK -
    confirm with the user first."""
    with INVENTORY.transaction() as inv:
        return inv_mod.release(inv, job_number)


@mcp.tool()
def receive_stock(code: str, qty: float, note: str = "") -> dict:
    """Add a delivery to on-hand (sheets and hardware in whole units, edgebanding in lineal
    feet). Put the PO or packing-slip number in note. CHANGES STOCK - confirm with the user
    first."""
    p = load_pricing(PRICING_PATH)
    with INVENTORY.transaction() as inv:
        return inv_mod.receive(inv, p, code.strip(), qty, note)


@mcp.tool()
def count_stock(code: str, counted_qty: float, note: str = "",
                reorder_point: float = -1, reorder_qty: float = -1, pack: int = 0,
                location: str = "") -> dict:
    """Record a physical count (sets on-hand to what was counted and logs the difference).
    Optionally set the reorder point, reorder quantity, pack size and rack location at the
    same time. CHANGES STOCK - confirm with the user first."""
    p = load_pricing(PRICING_PATH)
    with INVENTORY.transaction() as inv:
        out = inv_mod.count(inv, p, code.strip(), counted_qty, note)
        if reorder_point >= 0 or reorder_qty >= 0 or pack > 0 or location:
            it = inv["items"][code.strip()]
            out["settings"] = inv_mod.set_reorder(
                inv, p, code.strip(),
                reorder_point if reorder_point >= 0 else it["reorder_point"],
                reorder_qty if reorder_qty >= 0 else it["reorder_qty"],
                location, pack if pack > 0 else it.get("pack", 1))
        return out


@mcp.tool()
def log_remnant(material: str, length: float, width: float, location: str = "",
                note: str = "", remove_id: str = "") -> dict:
    """Put an offcut on the remnant rack (length along the grain, inches, 12" minimum), or
    pass remove_id (e.g. R0007) to take one off the rack when it is used or scrapped.
    CHANGES STOCK - confirm with the user first."""
    p = load_pricing(PRICING_PATH)
    with INVENTORY.transaction() as inv:
        if remove_id:
            return inv_mod.use_remnant(inv, remove_id.strip(), note)
        return inv_mod.add_remnant(inv, p, material.strip(), length, width, location, note=note)


if __name__ == "__main__":
    mcp.run()

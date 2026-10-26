"""Job estimating: materials from the actual nest, hardware from construction, labor from rates.

Prices live in a pricing table (JSON locally, Supabase in production) so the estimator
never guesses numbers. The AI's job is to read drawings and build the cabinet list;
this module does the arithmetic deterministically.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from collections import Counter

from .export import CNC_OPS, route
from .construction import build_job_parts, cabinet_connectors, review_job, ConstructionStandard
from .joinery import METHODS
from .models import Job, Material, Part
from .machine import MachineProfile, machine_from_pricing
from .nesting import prepare_for_saw, nest_parts, NestResult
from .router_nest import nest_job_router


def load_pricing(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def materials_from_pricing(pricing: dict) -> dict[str, Material]:
    return {m["code"]: Material(**m) for m in pricing["materials"]}


def group_parts(parts: list[Part]) -> dict[str, list[Part]]:
    g: dict[str, list[Part]] = defaultdict(list)
    for p in parts:
        g[p.material].append(p)
    return g


def _material_line(code: str, m: Material, sheets: int, yld: float, note: str = "") -> dict:
    return {"material": code, "description": m.description + (f" ({note})" if note else ""),
            "sheets": sheets, "yield_pct": round(yld, 1), "unit_cost": m.sheet_cost,
            "cost": round(sheets * m.sheet_cost, 2)}


def estimate_job(job: Job, pricing: dict, std: ConstructionStandard | None = None,
                 machine: MachineProfile | None = None) -> dict:
    std = std or ConstructionStandard()
    machine = machine or machine_from_pricing(pricing)
    mats = materials_from_pricing(pricing)
    hw = pricing["hardware"]
    lab = pricing["labor"]
    parts = build_job_parts(job, std, mats)

    nests: dict = {}
    material_lines = []
    prelam_sheets = 0
    if machine.is_nested:
        # --- sheet goods from the router nest: parts cut straight from full sheets ---
        nests = nest_job_router(parts, mats, machine)
        for key, res in nests.items():
            core = key.split("+")[0]
            if "+" in key:
                lam = key.split("+")[1]
                prelam_sheets += res.sheet_count
                material_lines.append(_material_line(core, mats[core], res.sheet_count,
                                                     res.yield_pct, "core for pre-lam sheets"))
                material_lines.append(_material_line(lam, mats[lam], res.sheet_count,
                                                     res.yield_pct, "pressed on full sheets before nesting"))
            else:
                material_lines.append(_material_line(core, mats[core], res.sheet_count, res.yield_pct))
    else:
        # --- panel saw: nest what the saw really cuts (grain-match and pre-lam blanks) ---
        for code, plist in group_parts(prepare_for_saw(parts)).items():
            if code not in mats:
                raise KeyError(f"Material {code} is not in the pricing table")
            res = nest_parts(plist, mats[code], kerf=pricing.get("kerf", 0.125),
                             trim=pricing.get("trim", 0.25))
            nests[code] = res
            material_lines.append(_material_line(code, mats[code], res.sheet_count, res.yield_pct))

    eb_lf = sum(p.edgeband_lf for p in parts) * (1 + pricing.get("edgeband_waste", 0.10))
    eb_cost = eb_lf * pricing["edgeband_per_lf"]

    # --- hardware counts derived from the construction ---
    doors = [p for p in parts if p.name.startswith("Door")]
    fronts = [p for p in parts if p.name.startswith("Drawer Front")]
    hinges = sum(sum(1 for o in d.machining if o["op"] == "pocket") * d.qty for d in doors)
    slides = sum(f.qty for f in fronts)
    pulls = sum(d.qty for d in doors) + slides
    shelf_pins = sum(p.qty * 4 for p in parts if p.name == "Adj Shelf")
    levelers = sum(4 * c.qty for c in job.cabinets if c.type in ("base", "drawer_base", "sink_base", "tall"))
    connectors: Counter = Counter()
    for c in job.cabinets:
        connectors.update(cabinet_connectors(c, job, std, mats))
    jhw = pricing["joinery_hardware"]
    hardware_lines = [
        {"item": "Hinge (clip-top, soft close)", "qty": hinges, "unit": hw["hinge"]},
        {"item": "Undermount slide (pair)", "qty": slides, "unit": hw["slide_pair"]},
        {"item": "Pull / knob", "qty": pulls, "unit": hw["pull"]},
        {"item": "Shelf pin", "qty": shelf_pins, "unit": hw["shelf_pin"]},
        {"item": "Leveler leg", "qty": levelers, "unit": hw["leveler"]},
    ] + [{"item": jhw[k]["description"], "qty": n, "unit": jhw[k]["unit_cost"]}
         for k, n in sorted(connectors.items())]
    for h in hardware_lines:
        h["cost"] = round(h["qty"] * h["unit"], 2)
    hw_cost = sum(h["cost"] for h in hardware_lines)

    # --- labor ---
    boxes = sum(c.qty for c in job.cabinets)
    total_sheets = sum(n.sheet_count for n in nests.values())
    jf = pricing["joinery_assembly_factor"]
    assembly = sum(c.qty * lab["assembly_hr_per_box"] * jf[c.joinery or job.joinery]
                   for c in job.cabinets)
    gm_groups = sum(1 for p in parts if p.grain_group and p.seq == 1 for _ in range(p.qty))
    prelam_panels = sum(p.qty for p in parts if p.laminate)
    machine_labor = {}
    if machine.is_nested:
        # router: load/unload per sheet + cut-out path at feed + bores; the separate
        # horizontal borer picks up edge bores when the router has no horizontal unit
        r = machine.tool_dia / 2
        cut_in = sum(2 * (p.length + p.width) + 8 * r for res in nests.values()
                     for sh in res.sheets for p in sh.placements)
        dado_in = sum(p.qty * p.length for p in parts for o in p.machining if o["op"] == "dado")
        bores = sum(p.qty for p in parts for o in p.machining if o["op"] in ("drill", "pocket"))
        edge_bores = sum(p.qty for p in parts for o in p.machining if o["op"] == "edge_drill")
        router_hr = (total_sheets * machine.load_min_per_sheet / 60
                     + (cut_in + dado_in) / machine.feed_ipm / 60
                     + bores * machine.sec_per_bore / 3600)
        edge_hr = 0.0 if machine.horizontal_boring else edge_bores * machine.edge_bore_sec / 3600
        if machine.horizontal_boring:
            router_hr += edge_bores * machine.sec_per_bore / 3600
        prelam_hr = prelam_sheets * machine.hr_per_prelam_sheet
        machine_hr = router_hr + edge_hr + prelam_hr
        machine_labor = {"router_hours": round(router_hr, 2), "router_sheets": total_sheets,
                         "edge_boring_hours": round(edge_hr, 2), "edge_bores": edge_bores,
                         "prelam_press_hours": round(prelam_hr, 2)}
        gm_hr = 0.0   # fronts stay in sequence on the nest; no extra recut step
    else:
        # saw time per sheet, then CNC time: load/unload per machined piece + per operation
        saw_hr = total_sheets * lab.get("saw_hr_per_sheet", lab.get("machine_hr_per_sheet", 0))
        cnc_parts = [p for p in parts if route(p).endswith("CNC")]
        cnc_pieces = sum(p.qty for p in cnc_parts)
        cnc_ops = sum(p.qty * sum(1 for o in p.machining if o["op"] in CNC_OPS) for p in cnc_parts)
        cnc_hr = (cnc_pieces * lab.get("cnc_min_per_part", 0) / 60
                  + cnc_ops * lab.get("cnc_sec_per_op", 0) / 3600)
        machine_hr = saw_hr + cnc_hr + prelam_panels * lab.get("hr_per_prelam_panel", 0)
        machine_labor = {"saw_hours": round(saw_hr, 2), "cnc_hours": round(cnc_hr, 2),
                         "cnc_pieces": cnc_pieces, "cnc_operations": cnc_ops}
        gm_hr = gm_groups * lab["hr_per_grain_match_group"]
    labor_hours = (
        assembly
        + gm_hr
        + sum(d.qty for d in doors) * lab["hr_per_door"]
        + slides * lab["hr_per_drawer"]
        + machine_hr
        + eb_lf * lab["edgeband_hr_per_lf"]
    )
    labor_cost = labor_hours * lab["rate_per_hr"]

    direct = sum(m["cost"] for m in material_lines) + eb_cost + hw_cost + labor_cost
    overhead = direct * pricing.get("overhead_pct", 0.0)
    price = (direct + overhead) * (1 + pricing.get("markup_pct", 0.0))

    return {
        "job_number": job.job_number,
        "job_name": job.name,
        "cabinet_count": boxes,
        "part_count": sum(p.qty for p in parts),
        "joinery": sorted({c.joinery or job.joinery for c in job.cabinets}),
        "grain_match_groups": gm_groups,
        "prelam_panels": prelam_panels,
        "machine": machine.name,
        "review_flags": review_job(job, mats) + _machine_flags(machine, nests),
        "materials": material_lines,
        "edgeband": {"lf": round(eb_lf, 1), "cost": round(eb_cost, 2)},
        "hardware": hardware_lines,
        "labor": {"hours": round(labor_hours, 2), "rate": lab["rate_per_hr"],
                  "cost": round(labor_cost, 2), **machine_labor},
        "totals": {
            "direct_cost": round(direct, 2),
            "overhead": round(overhead, 2),
            "sell_price": round(price, 2),
            "price_per_box": round(price / boxes, 2) if boxes else 0,
        },
        "_nests": nests,
        "_parts": parts,
    }


def _machine_flags(machine: MachineProfile, nests: dict) -> list[str]:
    flags = []
    if not machine.is_nested:
        return flags
    for key, res in nests.items():
        for u in res.unplaced:
            flags.append(f"{key}: {u} - will not fit the sheet with margins; split it or use a bigger sheet")
        if res.small_parts:
            flags.append(f"{key}: small parts ({', '.join(res.small_parts[:6])}"
                         f"{'...' if len(res.small_parts) > 6 else ''}) - cut first with onion skin "
                         "or tabs so they don't move")
    open_items = machine.unconfirmed()
    if open_items:
        flags.append(f"Machine profile has {len(open_items)} unconfirmed settings "
                     f"({', '.join(open_items[:5])}{'...' if len(open_items) > 5 else ''}) - "
                     "nest programs are UNVERIFIED until checked in woodWOP and run on the machine")
    return flags


def public(estimate: dict) -> dict:
    """Estimate without the internal objects, safe to serialize."""
    return {k: v for k, v in estimate.items() if not k.startswith("_")}

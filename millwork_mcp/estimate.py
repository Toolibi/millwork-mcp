"""Job estimating: materials from the actual nest, hardware from construction, labor from rates.

Prices live in a pricing table (JSON locally, Supabase in production) so the estimator
never guesses numbers. The AI's job is to read drawings and build the cabinet list;
this module does the arithmetic deterministically.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .construction import build_job_parts, hinge_count, review_job, ConstructionStandard
from .models import Job, Material, Part
from .nesting import nest_parts, NestResult


def load_pricing(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def materials_from_pricing(pricing: dict) -> dict[str, Material]:
    return {m["code"]: Material(**m) for m in pricing["materials"]}


def group_parts(parts: list[Part]) -> dict[str, list[Part]]:
    g: dict[str, list[Part]] = defaultdict(list)
    for p in parts:
        g[p.material].append(p)
    return g


def estimate_job(job: Job, pricing: dict, std: ConstructionStandard | None = None) -> dict:
    std = std or ConstructionStandard()
    mats = materials_from_pricing(pricing)
    hw = pricing["hardware"]
    lab = pricing["labor"]
    parts = build_job_parts(job, std)
    by_mat = group_parts(parts)

    # --- sheet goods from the real panel-saw nest ---
    nests: dict[str, NestResult] = {}
    material_lines = []
    for code, plist in by_mat.items():
        if code not in mats:
            raise KeyError(f"Material {code} is not in the pricing table")
        res = nest_parts(plist, mats[code], kerf=pricing.get("kerf", 0.125),
                         trim=pricing.get("trim", 0.25))
        nests[code] = res
        cost = res.sheet_count * mats[code].sheet_cost
        material_lines.append({"material": code, "description": mats[code].description,
                               "sheets": res.sheet_count, "yield_pct": round(res.yield_pct, 1),
                               "unit_cost": mats[code].sheet_cost, "cost": round(cost, 2)})

    eb_lf = sum(p.edgeband_lf for p in parts) * (1 + pricing.get("edgeband_waste", 0.10))
    eb_cost = eb_lf * pricing["edgeband_per_lf"]

    # --- hardware counts derived from the construction ---
    doors = [p for p in parts if p.name.startswith("Door")]
    fronts = [p for p in parts if p.name.startswith("Drawer Front")]
    hinges = sum(hinge_count(d.length) * d.qty for d in doors)
    slides = sum(f.qty for f in fronts)
    pulls = sum(d.qty for d in doors) + slides
    shelf_pins = sum(p.qty * 4 for p in parts if p.name == "Adj Shelf")
    levelers = sum(4 * c.qty for c in job.cabinets if c.type in ("base", "drawer_base", "sink_base", "tall"))
    hardware_lines = [
        {"item": "Hinge (clip-top, soft close)", "qty": hinges, "unit": hw["hinge"]},
        {"item": "Undermount slide (pair)", "qty": slides, "unit": hw["slide_pair"]},
        {"item": "Pull / knob", "qty": pulls, "unit": hw["pull"]},
        {"item": "Shelf pin", "qty": shelf_pins, "unit": hw["shelf_pin"]},
        {"item": "Leveler leg", "qty": levelers, "unit": hw["leveler"]},
    ]
    for h in hardware_lines:
        h["cost"] = round(h["qty"] * h["unit"], 2)
    hw_cost = sum(h["cost"] for h in hardware_lines)

    # --- labor ---
    boxes = sum(c.qty for c in job.cabinets)
    total_sheets = sum(n.sheet_count for n in nests.values())
    labor_hours = (
        boxes * lab["assembly_hr_per_box"]
        + sum(d.qty for d in doors) * lab["hr_per_door"]
        + slides * lab["hr_per_drawer"]
        + total_sheets * lab["machine_hr_per_sheet"]
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
        "review_flags": review_job(job),
        "materials": material_lines,
        "edgeband": {"lf": round(eb_lf, 1), "cost": round(eb_cost, 2)},
        "hardware": hardware_lines,
        "labor": {"hours": round(labor_hours, 2), "rate": lab["rate_per_hr"], "cost": round(labor_cost, 2)},
        "totals": {
            "direct_cost": round(direct, 2),
            "overhead": round(overhead, 2),
            "sell_price": round(price, 2),
            "price_per_box": round(price / boxes, 2) if boxes else 0,
        },
        "_nests": nests,
        "_parts": parts,
    }


def public(estimate: dict) -> dict:
    """Estimate without the internal objects, safe to serialize."""
    return {k: v for k, v in estimate.items() if not k.startswith("_")}

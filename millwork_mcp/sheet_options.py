# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""Compare sheet sizes (and cores) for a job: nest the same parts on every size the supplier
sells and report sheets, yield, material cost and router time, so the estimator can see whether
a 5x10 or a 4x10 beats the standard 4x8 on this particular job.

Only the router time that changes with the sheet is compared (load/unload per sheet and the
cut-out path). Boring, dados, edgebanding and assembly are the same parts and the same
operations whatever the sheet size, so they are reported once for the job, not per option.
"""
from __future__ import annotations

from dataclasses import replace

from .construction import ConstructionStandard
from .estimate import (build_job_parts, estimate_job, materials_from_pricing,
                       sheet_handling_and_cut_hours)
from .machine import MachineProfile, machine_from_pricing
from .models import Job
from .router_nest import nest_material_key, nest_router


def _options(mat) -> list[dict]:
    current = {"label": f"{mat.sheet_width:g} x {mat.sheet_length:g} (current)",
               "length": mat.sheet_length, "width": mat.sheet_width,
               "cost": mat.sheet_cost, "current": True}
    return [current] + [dict(o, current=False) for o in mat.sheet_options]


def compare_sheet_options(job: Job, pricing: dict, material: str | None = None,
                          machine: MachineProfile | None = None) -> dict:
    machine = machine or machine_from_pricing(pricing)
    if not machine.is_nested:
        raise ValueError("Sheet-size comparison needs the nested-router machine profile")
    mats = materials_from_pricing(pricing)
    rate = pricing["labor"]["rate_per_hr"]
    parts = build_job_parts(job, ConstructionStandard(), mats)

    groups: dict[str, list] = {}
    for p in parts:
        groups.setdefault(nest_material_key(p), []).append(p)

    results, notes = [], []
    for key, plist in sorted(groups.items()):
        if "+" in key:
            notes.append(f"{key}: pre-lam sheets keep the laminate's sheet size; not compared")
            continue
        if material and key != material:
            continue
        mat = mats[key]
        rows = []
        for opt in _options(mat):
            row = {"option": opt["label"], "sheet_in": f'{opt["width"]:g} x {opt["length"]:g}',
                   "sheet_cost": opt["cost"], "note": opt.get("note", ""),
                   "current": opt["current"], "kind": opt.get("kind", "size")}
            if opt["length"] > machine.bed_length + 1e-9 or opt["width"] > machine.bed_width + 1e-9:
                row["fits_machine"] = False
                row["note"] = (row["note"] + "; " if row["note"] else "") + (
                    f'larger than the {machine.bed_width:g} x {machine.bed_length:g} router bed')
                rows.append(row)
                continue
            m2 = replace(mat, sheet_length=opt["length"], sheet_width=opt["width"],
                         sheet_cost=opt["cost"])
            res = nest_router(plist, m2, machine, key)
            hrs = sheet_handling_and_cut_hours(res, machine)
            mat_cost = res.sheet_count * opt["cost"]
            row.update({
                "fits_machine": True,
                "sheets": res.sheet_count,
                "yield_pct": round(res.yield_pct, 1),
                "material_cost": round(mat_cost, 2),
                "router_hours": round(hrs, 2),
                "router_labor_cost": round(hrs * rate, 2),
                "total_cost": round(mat_cost + hrs * rate, 2),
                "parts_not_fitting": len(res.unplaced),
            })
            rows.append(row)

        # a different core is a spec change for the designer, never an automatic pick
        ok = [r for r in rows if r.get("fits_machine") and not r["parts_not_fitting"]
              and r["kind"] == "size"]
        cores = [r for r in rows if r["kind"] == "core" and r.get("fits_machine")]
        cur = next(r for r in rows if r["current"])
        best = min(ok, key=lambda r: r["total_cost"]) if ok else None
        saving = round(cur["total_cost"] - best["total_cost"], 2) if best and "total_cost" in cur else 0.0
        results.append({
            "material": key,
            "description": mat.description,
            "grain_note": ("grain runs along the sheet length; grained parts are never rotated"
                           if mat.grained else "no grain; parts may rotate"),
            "options": rows,
            "recommendation": (
                f"Stay on {cur['option'].replace(' (current)', '')}: no larger size is cheaper for this job"
                if not best or best["current"] else
                f"Use {best['option']}: {best['sheets']} sheets at {best['yield_pct']}% yield, "
                f"saves ${saving:,.2f} on this job (material + router time)"),
            "saving_vs_current": saving if best and not best["current"] else 0.0,
            "core_alternatives": [
                f"{r['option']}: ${r['total_cost'] - cur['total_cost']:+,.2f} vs current"
                + (f" ({r['note']})" if r['note'] else "") + " - a spec change, confirm with the designer"
                for r in cores if "total_cost" in cur],
        })

    est = estimate_job(job, pricing, machine=machine)
    lab = est["labor"]
    return {
        "job_number": job.job_number,
        "materials": results,
        "same_for_every_size": {
            "edgebanding_lf": est["edgeband"]["lf"],
            "edgebanding_hours": lab["breakdown_hours"]["edgebanding"],
            "edge_boring_hours": lab.get("edge_boring_hours", 0.0),
            "assembly_hours": lab["breakdown_hours"]["assembly"],
        },
        "total_possible_saving": round(sum(r["saving_vs_current"] for r in results), 2),
        "notes": notes + [
            "Sheet prices for sizes other than the current one are sample figures - confirm "
            "with the supplier before bidding.",
            "Larger sheets need two people or a vacuum lifter to load; add handling time if "
            "the shop has no lift.",
        ],
    }

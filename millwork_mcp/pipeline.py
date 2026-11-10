# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""End-to-end run: job JSON -> parts -> nest -> estimate -> shop files."""
from __future__ import annotations

import json
from pathlib import Path

from .estimate import estimate_job, materials_from_pricing, public
from .export import export_cnc_list_csv, export_cutlist_csv, export_part_dxfs, export_saw_list_csv
from .export_woodwop import export_router_nests
from .machine import MachineProfile, machine_from_pricing
from .models import Cabinet, Job
from .nesting import nest_svg


def job_from_dict(d: dict) -> Job:
    cabs = [Cabinet(**c) for c in d["cabinets"]]
    fields = {k: v for k, v in d.items() if k != "cabinets"}
    return Job(cabinets=cabs, **fields)


def run_job(job_data: dict, pricing: dict, out_dir: str | Path,
            machine: MachineProfile | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    job = job_from_dict(job_data)
    mats = materials_from_pricing(pricing)
    machine = machine or machine_from_pricing(pricing)
    est = estimate_job(job, pricing, machine=machine)

    files = {
        "estimate_json": out / "estimate.json",
        "cutlist_csv": export_cutlist_csv(est["_parts"], out / "cutlist.csv", mats, machine),
        "cnc_list_csv": export_cnc_list_csv(est["_parts"], out / "cnc_list.csv"),
    }
    files["estimate_json"].write_text(json.dumps(public(est), indent=2))
    dxf = export_part_dxfs(est["_parts"], mats, out / "dxf")

    result = public(est)
    result["files"] = {k: str(v) for k, v in files.items()}
    result["files"]["dxf_count"] = len(dxf)
    if machine.is_nested:
        # one woodWOP-layer DXF per sheet, cut order, edge-boring list
        result["files"].update(export_router_nests(est["_nests"], mats, machine, out / "nests"))
        result["files"]["nest_svgs"] = result["files"].pop("sheet_svgs")
    else:
        files_saw = export_saw_list_csv(est["_nests"], out / "saw_list.csv")
        svgs = []
        for code, res in est["_nests"].items():
            for sh in res.sheets:
                p = out / "nests" / f"{code}_sheet{sh.index}.svg"
                p.parent.mkdir(exist_ok=True)
                p.write_text(nest_svg(sh))
                svgs.append(p)
        result["files"]["saw_list_csv"] = str(files_saw)
        result["files"]["nest_svgs"] = [str(s) for s in svgs]
    return result

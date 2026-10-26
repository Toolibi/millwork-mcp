"""End-to-end run: job JSON -> parts -> nest -> estimate -> shop files."""
from __future__ import annotations

import json
from pathlib import Path

from .estimate import estimate_job, materials_from_pricing, public
from .export import export_cutlist_csv, export_part_dxfs, export_saw_list_csv
from .models import Cabinet, Job
from .nesting import nest_svg


def job_from_dict(d: dict) -> Job:
    cabs = [Cabinet(**c) for c in d["cabinets"]]
    return Job(job_number=d["job_number"], name=d["name"], cabinets=cabs,
               case_material=d.get("case_material", "MEL-WHT-34"),
               back_material=d.get("back_material", "MEL-WHT-14"),
               door_material=d.get("door_material", "MEL-WHT-34"))


def run_job(job_data: dict, pricing: dict, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    job = job_from_dict(job_data)
    est = estimate_job(job, pricing)
    mats = materials_from_pricing(pricing)

    files = {
        "estimate_json": out / "estimate.json",
        "cutlist_csv": export_cutlist_csv(est["_parts"], out / "cutlist.csv"),
        "saw_list_csv": export_saw_list_csv(est["_nests"], out / "saw_list.csv"),
    }
    files["estimate_json"].write_text(json.dumps(public(est), indent=2))

    dxf = export_part_dxfs(est["_parts"], mats, out / "dxf")
    svgs = []
    for code, res in est["_nests"].items():
        for sh in res.sheets:
            p = out / "nests" / f"{code}_sheet{sh.index}.svg"
            p.parent.mkdir(exist_ok=True)
            p.write_text(nest_svg(sh))
            svgs.append(p)

    result = public(est)
    result["files"] = {k: str(v) for k, v in files.items()}
    result["files"]["dxf_count"] = len(dxf)
    result["files"]["nest_svgs"] = [str(s) for s in svgs]
    return result

# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""Inventory control for a cabinet shop: sheet goods, edgebanding, hardware and offcuts.

The numbers a job needs come from the same estimate the shop prices with (the actual nest,
the hardware counted from the construction, edgebanding from the parts), so stock, quotes
and shop files never disagree.

    on hand   - what is physically in the shop
    reserved  - set aside for jobs that are released but not yet cut
    available - on hand minus reserved (can go negative: that is a shortage to buy)

Life of a job:  check -> reserve -> (cut) consume, or release if the job is cancelled.
Offcuts (remnants) big enough to keep are logged when a job is cut, and the next job's
check tries its parts on them first. Grain-matched fronts always come from full sheets.

Storage is one JSON file, written atomically under a file lock, with an audit log of every
change. Point MILLWORK_INVENTORY at persistent storage (on Azure App Service: /home/data).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .estimate import estimate_job, materials_from_pricing
from .machine import MachineProfile, machine_from_pricing
from .models import Job
from .router_nest import nest_material_key, nest_router

LOG_LIMIT = 1000
MIN_REMNANT_IN = 12.0          # smaller offcuts are not worth racking
MAX_QTY = 1_000_000

# hardware lines in the estimate -> inventory codes
HW_CODES = {
    "Hinge (clip-top, soft close)": "HW-HINGE",
    "Undermount slide (pair)": "HW-SLIDE-PAIR",
    "Pull / knob": "HW-PULL",
    "Shelf pin": "HW-SHELF-PIN",
    "Leveler leg": "HW-LEVELER",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v, name: str, allow_zero: bool = False) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(x) or x < 0 or (x == 0 and not allow_zero) or x > MAX_QTY:
        raise ValueError(f"{name} must be {'0 or more' if allow_zero else 'more than 0'} "
                         f"and at most {MAX_QTY:,}")
    return x


def _text(v, name: str, limit: int = 200) -> str:
    s = str(v or "").strip()
    if len(s) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")
    return s


def hardware_code(item: str, pricing: dict) -> str:
    if item in HW_CODES:
        return HW_CODES[item]
    for key, h in pricing.get("joinery_hardware", {}).items():
        if h.get("description") == item:
            return "HW-" + key.upper().replace("_", "-")
    return "HW-" + "".join(c if c.isalnum() else "-" for c in item.upper())[:40]


def catalog(pricing: dict) -> dict[str, dict]:
    """Every stockable item the price list knows, with its unit and unit cost."""
    items = {}
    for m in pricing["materials"]:
        items[m["code"]] = {"kind": "sheet", "description": m["description"], "unit": "sheet",
                            "unit_cost": m["sheet_cost"],
                            "size": f'{m["sheet_width"]:g} x {m["sheet_length"]:g}'}
        items["EB-" + m["code"]] = {"kind": "edgeband", "unit": "lf",
                                    "description": f'Edgebanding to match {m["description"]}',
                                    "unit_cost": pricing["edgeband_per_lf"]}
    hw = pricing["hardware"]
    for desc, code, key in [("Hinge (clip-top, soft close)", "HW-HINGE", "hinge"),
                            ("Undermount slide (pair)", "HW-SLIDE-PAIR", "slide_pair"),
                            ("Pull / knob", "HW-PULL", "pull"),
                            ("Shelf pin", "HW-SHELF-PIN", "shelf_pin"),
                            ("Leveler leg", "HW-LEVELER", "leveler")]:
        items[code] = {"kind": "hardware", "description": desc, "unit": "ea", "unit_cost": hw[key]}
    for key, h in pricing.get("joinery_hardware", {}).items():
        items["HW-" + key.upper().replace("_", "-")] = {
            "kind": "hardware", "description": h["description"], "unit": "ea",
            "unit_cost": h["unit_cost"]}
    return items


# ----------------------------------------------------------------------------- storage
class InventoryStore:
    """One JSON file. Every change runs inside `transaction()`: an exclusive lock, a fresh
    read, the change, an atomic replace, so two agents can't lose each other's updates."""

    def __init__(self, path: str | Path, seed: str | Path | None = None):
        self.path = Path(path)
        self.seed = Path(seed) if seed else None

    def _ensure(self):
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.seed and self.seed.exists():
            shutil.copyfile(self.seed, self.path)
        else:
            self.path.write_text(json.dumps(empty()))

    @contextlib.contextmanager
    def _locked(self):
        self._ensure()
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with open(lock, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read(self) -> dict:
        with self._locked():
            return json.loads(self.path.read_text())

    @contextlib.contextmanager
    def transaction(self):
        with self._locked():
            inv = json.loads(self.path.read_text())
            yield inv
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".inv-", suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(inv, f, indent=1)
            os.replace(tmp, self.path)


def empty() -> dict:
    return {"items": {}, "remnants": [], "reservations": {}, "log": [], "next_remnant": 1}


def _sync_catalog(inv: dict, pricing: dict) -> None:
    """Add any price-list item the stock file doesn't have yet (at zero on hand)."""
    for code, meta in catalog(pricing).items():
        it = inv["items"].setdefault(code, {"on_hand": 0, "reorder_point": 0, "reorder_qty": 0,
                                            "location": ""})
        for k in ("kind", "description", "unit"):
            it[k] = meta[k]


def _log(inv: dict, action: str, code: str = "", qty: float = 0, job: str = "", note: str = ""):
    inv["log"].append({"at": _now(), "action": action, "code": code, "qty": qty,
                       "job": job, "note": note})
    del inv["log"][:-LOG_LIMIT]


def _reserved(inv: dict) -> Counter:
    r: Counter = Counter()
    for res in inv["reservations"].values():
        if res["status"] == "reserved":
            r.update(res["lines"])
    return r


def _row(code: str, it: dict, reserved: float, cost: float) -> dict:
    avail = it["on_hand"] - reserved
    return {"code": code, "description": it["description"], "unit": it["unit"],
            "on_hand": _r(it["on_hand"]), "reserved": _r(reserved), "available": _r(avail),
            "reorder_point": it["reorder_point"], "location": it.get("location", ""),
            "unit_cost": cost, "value": round(it["on_hand"] * cost, 2),
            "status": ("SHORT" if avail < 0 else "LOW" if avail <= it["reorder_point"]
                       and it["reorder_point"] > 0 else "ok")}


def _r(x: float) -> float:
    return int(x) if float(x).is_integer() else round(x, 1)


# ----------------------------------------------------------------------------- reads
def status(inv: dict, pricing: dict, code: str = "", low_only: bool = False) -> dict:
    _sync_catalog(inv, pricing)
    cat, res = catalog(pricing), _reserved(inv)
    rows = [_row(c, it, res.get(c, 0), cat.get(c, {}).get("unit_cost", 0))
            for c, it in sorted(inv["items"].items())
            if (not code or c == code) and (it["on_hand"] or res.get(c) or it["reorder_point"]
                                             or code)]
    if code and not rows:
        raise ValueError(f"Unknown code {code}. Codes come from get_pricing: sheet codes, "
                         f"EB-<sheet code> for edgebanding, HW-... for hardware")
    value = round(sum(r["value"] for r in rows), 2)       # before the low-only filter
    if low_only:
        rows = [r for r in rows if r["status"] != "ok"]
    rems = [r for r in inv["remnants"] if not code or r["material"] == code]
    return {"items": rows,
            "stock_value": value,
            "stock_value_covers": f"item {code}" if code else "all stock on hand",
            "remnants": rems,
            "open_reservations": {j: {"reserved_at": v["at"], "lines": len(v["lines"])}
                                  for j, v in inv["reservations"].items()
                                  if v["status"] == "reserved"},
            "note": "available = on hand - reserved for released jobs; SHORT means buy before cutting"}


def _plan_remnants(parts, mat, machine: MachineProfile, remnants: list[dict]):
    """Put loose parts on offcuts first (largest offcut first). Grain-matched fronts are
    left for full sheets so their grain keeps running across the run."""
    loose = [replace(p, qty=1) for p in parts if not p.grain_group for _ in range(p.qty)]
    keep = [p for p in parts if p.grain_group]
    used = []
    for r in sorted(remnants, key=lambda r: -r["length"] * r["width"]):
        if not loose:
            break
        m2 = replace(mat, sheet_length=r["length"], sheet_width=r["width"])
        res = nest_router(loose, m2, machine)
        if not res.sheets:
            continue
        on = res.sheets[0].placements
        ids = {id(pl.part) for pl in on}
        loose = [p for p in loose if id(p) not in ids]
        used.append({"remnant_id": r["id"], "size": f'{r["width"]:g} x {r["length"]:g}',
                     "location": r.get("location", ""), "parts": len(on),
                     "part_list": sorted(Counter(pl.label for pl in on).items())})
    return used, keep + loose


def job_needs(job: Job, pricing: dict, inv: dict, use_remnants: bool = True,
              machine: MachineProfile | None = None) -> dict:
    """What this job takes from stock, what is short, and what to buy."""
    _sync_catalog(inv, pricing)
    machine = machine or machine_from_pricing(pricing)
    est = estimate_job(job, pricing, machine=machine)
    mats, cat = materials_from_pricing(pricing), catalog(pricing)

    need: Counter = Counter()
    for line in est["materials"]:
        need[line["material"]] += line["sheets"]

    remnant_plan, saved = [], Counter()
    if use_remnants and machine.is_nested:
        groups: dict = {}
        for p in est["_parts"]:
            groups.setdefault(nest_material_key(p), []).append(p)
        for key, plist in groups.items():
            if "+" in key:
                continue                       # pre-lam goes on pressed full sheets
            rems = [r for r in inv["remnants"] if r["material"] == key
                    and not r.get("reserved_for")]
            if not rems:
                continue
            used, rest = _plan_remnants(plist, mats[key], machine, rems)
            if not used:
                continue
            after = nest_router(rest, mats[key], machine).sheet_count if rest else 0
            before = est["_nests"][key].sheet_count
            if after < before:                 # only worth it if it saves a full sheet
                saved[key] = before - after
                need[key] -= before - after
                remnant_plan.append({"material": key, "full_sheets_without": before,
                                     "full_sheets_with": after, "remnants": used})

    ws = 1 + pricing.get("edgeband_waste", 0.10)
    eb: Counter = Counter()
    for p in est["_parts"]:
        if p.edgeband_lf:
            eb["EB-" + p.material] += p.edgeband_lf * ws
    for code, lf in eb.items():
        need[code] += math.ceil(lf)
    for h in est["hardware"]:
        if h["qty"]:
            need[hardware_code(h["item"], pricing)] += h["qty"]

    res = _reserved(inv)
    mine = inv["reservations"].get(job.job_number, {})
    if mine.get("status") == "reserved":       # re-checking a reserved job: don't count it twice
        res.subtract(mine["lines"])
    lines, buy = [], []
    for code, q in sorted(need.items()):
        if q <= 0:
            continue
        it = inv["items"].get(code, {"on_hand": 0, "unit": cat.get(code, {}).get("unit", "ea"),
                                     "description": code})
        avail = it["on_hand"] - res.get(code, 0)
        short = max(0, q - max(avail, 0))
        cost = cat.get(code, {}).get("unit_cost", 0)
        row = {"code": code, "description": it["description"], "unit": it["unit"],
               "need": _r(q)}
        if saved.get(code):
            row["need_without_remnants"] = _r(q + saved[code])
        lines.append({**row, "on_hand": _r(it["on_hand"]),
                      "reserved_for_other_jobs": _r(res.get(code, 0)),
                      "available": _r(avail), "short": _r(short)})
        if short:
            buy.append({"code": code, "description": it["description"], "qty": _r(short),
                        "unit": it["unit"], "unit_cost": cost, "cost": round(short * cost, 2)})
    summary = []
    for l in lines:
        if l["code"] in saved:
            summary.append(f'{l["code"]}: need {l["need"]} full sheets after using offcuts '
                           f'({l["need_without_remnants"]} without them), {l["available"]} available, '
                           f'short {l["short"]}. The offcut saving is already counted in "need" and "short".')
        elif l["short"]:
            summary.append(f'{l["code"]}: need {l["need"]} {l["unit"]}, {l["available"]} available, short {l["short"]}.')
    summary.append("Everything else is in stock." if len(summary) < len(lines) else "")
    return {"job_number": job.job_number,
            "summary": [x for x in summary if x],
            "review_flags": est["review_flags"],
            "lines": lines,
            "ready_to_cut": not buy,
            "shortages": buy,
            "shortage_cost": round(sum(b["cost"] for b in buy), 2),
            "remnant_plan": remnant_plan,
            "sheets_saved_by_remnants": dict(saved),
            "remnant_saving": round(sum(n * cat[c]["unit_cost"] for c, n in saved.items()), 2),
            "remnant_note": ("Cut the listed parts from the listed offcuts. The exported router "
                             "nests still place them on full sheets, so skip them there."
                             if remnant_plan else ""),
            "note": ("Needs come from the same nest and hardware count as the estimate. "
                     "Quantities on hand are only as good as the last count.")}


def purchase_list(inv: dict, pricing: dict) -> dict:
    """Everything to order: shortages for reserved jobs, plus items at or under their reorder
    point topped back up by the reorder quantity."""
    _sync_catalog(inv, pricing)
    cat, res = catalog(pricing), _reserved(inv)
    out = []
    for code, it in sorted(inv["items"].items()):
        avail = it["on_hand"] - res.get(code, 0)
        if avail < 0 or (it["reorder_point"] and avail <= it["reorder_point"]):
            qty = max(-avail, 0) + (it["reorder_qty"] if avail <= it["reorder_point"] else 0)
            pack = it.get("pack", 1) or 1
            qty = math.ceil(qty / pack) * pack
            if qty <= 0:
                continue
            cost = cat.get(code, {}).get("unit_cost", 0)
            jobs = sorted(j for j, v in inv["reservations"].items()
                          if v["status"] == "reserved" and code in v["lines"])
            out.append({"code": code, "description": it["description"], "order_qty": _r(qty),
                        "unit": it["unit"], "pack": pack,
                        "why": ("short for reserved jobs" if avail < 0 else "at reorder point"),
                        "for_jobs": jobs, "unit_cost": cost, "cost": round(qty * cost, 2)})
    return {"lines": out, "total": round(sum(o["cost"] for o in out), 2),
            "note": "A draft for a person to review and send; prices are the price-list figures."}


# ----------------------------------------------------------------------------- writes
def reserve(inv: dict, pricing: dict, job: Job, use_remnants: bool = True) -> dict:
    chk = job_needs(job, pricing, inv, use_remnants)
    jn = job.job_number
    old = inv["reservations"].get(jn)
    if old and old["status"] == "consumed":
        raise ValueError(f"Job {jn} was already cut and consumed; nothing to reserve")
    for r in inv["remnants"]:                 # replace any earlier reservation of this job
        if r.get("reserved_for") == jn:
            r.pop("reserved_for")
    rem_ids = [u["remnant_id"] for p in chk["remnant_plan"] for u in p["remnants"]]
    for r in inv["remnants"]:
        if r["id"] in rem_ids:
            r["reserved_for"] = jn
    inv["reservations"][jn] = {"at": _now(), "status": "reserved",
                               "lines": {l["code"]: l["need"] for l in chk["lines"]},
                               "remnants": rem_ids}
    _log(inv, "reserve", job=jn, note=f'{len(chk["lines"])} lines, {len(rem_ids)} remnants'
         + (" (replaced earlier reservation)" if old else ""))
    return {**chk, "reserved": True}


def consume(inv: dict, job_number: str, remnants_left: list | None = None,
            pricing: dict | None = None, note: str = "") -> dict:
    jn = _text(job_number, "job_number", 64)
    r = inv["reservations"].get(jn)
    if not r or r["status"] != "reserved":
        raise ValueError(f"No open reservation for job {jn}; reserve it first")
    went_negative = []
    for code, q in r["lines"].items():
        it = inv["items"][code]
        it["on_hand"] = round(it["on_hand"] - q, 3)
        if it["on_hand"] < 0:
            went_negative.append(code)
        _log(inv, "consume", code, -q, jn, note)
    used = set(r.get("remnants", []))
    inv["remnants"] = [x for x in inv["remnants"] if x["id"] not in used]
    for rid in used:
        _log(inv, "use_remnant", job=jn, note=f"remnant {rid}")
    added = [add_remnant(inv, pricing, **{**x, "from_job": jn}) for x in (remnants_left or [])]
    r["status"], r["consumed_at"] = "consumed", _now()
    return {"job_number": jn, "consumed_lines": len(r["lines"]), "remnants_used": sorted(used),
            "remnants_added": added,
            "warnings": [f"{c} is below zero on hand - recount it" for c in went_negative]}


def release(inv: dict, job_number: str) -> dict:
    jn = _text(job_number, "job_number", 64)
    r = inv["reservations"].get(jn)
    if not r or r["status"] != "reserved":
        raise ValueError(f"No open reservation for job {jn}")
    r["status"], r["released_at"] = "released", _now()
    for x in inv["remnants"]:
        if x.get("reserved_for") == jn:
            x.pop("reserved_for")
    _log(inv, "release", job=jn)
    return {"job_number": jn, "released_lines": len(r["lines"])}


def receive(inv: dict, pricing: dict, code: str, qty, note: str = "") -> dict:
    _sync_catalog(inv, pricing)
    if code not in inv["items"]:
        raise ValueError(f"Unknown code {code}; codes come from get_pricing")
    q = _num(qty, "qty")
    if inv["items"][code]["unit"] in ("sheet", "ea") and not float(q).is_integer():
        raise ValueError(f"{code} is counted in whole {inv['items'][code]['unit']}s")
    inv["items"][code]["on_hand"] = round(inv["items"][code]["on_hand"] + q, 3)
    _log(inv, "receive", code, q, note=_text(note, "note"))
    return {"code": code, "received": _r(q), "on_hand": _r(inv["items"][code]["on_hand"])}


def count(inv: dict, pricing: dict, code: str, counted, note: str = "") -> dict:
    _sync_catalog(inv, pricing)
    if code not in inv["items"]:
        raise ValueError(f"Unknown code {code}; codes come from get_pricing")
    c = _num(counted, "counted_qty", allow_zero=True)
    old = inv["items"][code]["on_hand"]
    inv["items"][code]["on_hand"] = c
    _log(inv, "count", code, round(c - old, 3), note=_text(note, "note") or f"counted {c:g}, was {old:g}")
    return {"code": code, "was": _r(old), "now": _r(c), "difference": _r(c - old)}


def set_reorder(inv: dict, pricing: dict, code: str, reorder_point, reorder_qty,
                location: str = "", pack=1) -> dict:
    _sync_catalog(inv, pricing)
    if code not in inv["items"]:
        raise ValueError(f"Unknown code {code}; codes come from get_pricing")
    it = inv["items"][code]
    it["reorder_point"] = _r(_num(reorder_point, "reorder_point", allow_zero=True))
    it["reorder_qty"] = _r(_num(reorder_qty, "reorder_qty", allow_zero=True))
    it["pack"] = int(_num(pack, "pack"))
    if location:
        it["location"] = _text(location, "location", 60)
    _log(inv, "set_reorder", code, note=f'point {it["reorder_point"]}, qty {it["reorder_qty"]}')
    return {"code": code, **{k: it[k] for k in ("reorder_point", "reorder_qty", "pack", "location")}}


def add_remnant(inv: dict, pricing: dict | None, material: str, length, width,
                location: str = "", from_job: str = "", note: str = "") -> dict:
    mats = {m["code"]: m for m in (pricing or {}).get("materials", [])}
    if pricing and material not in mats:
        raise ValueError(f"Unknown material {material}; codes come from get_pricing")
    L, W = _num(length, "length"), _num(width, "width")
    if min(L, W) < MIN_REMNANT_IN:
        raise ValueError(f'Offcuts under {MIN_REMNANT_IN:g}" are not worth racking - scrap it')
    if material in mats:
        m = mats[material]
        if L > m["sheet_length"] + 1e-9 or W > m["sheet_width"] + 1e-9:
            raise ValueError(f'{W:g} x {L:g} is bigger than a full {material} sheet '
                             f'(length runs with the grain)')
    rid = f'R{inv["next_remnant"]:04d}'
    inv["next_remnant"] += 1
    rec = {"id": rid, "material": material, "length": L, "width": W,
           "location": _text(location, "location", 60), "from_job": _text(from_job, "from_job", 64),
           "added": _now()}
    inv["remnants"].append(rec)
    _log(inv, "add_remnant", material, 1, rec["from_job"], f'{rid} {W:g} x {L:g} ' + _text(note, "note"))
    return rec


def use_remnant(inv: dict, remnant_id: str, note: str = "") -> dict:
    hit = [r for r in inv["remnants"] if r["id"] == remnant_id]
    if not hit:
        raise ValueError(f"No remnant {remnant_id}")
    inv["remnants"].remove(hit[0])
    _log(inv, "use_remnant", hit[0]["material"], -1, hit[0].get("reserved_for", ""),
         f"{remnant_id} " + _text(note, "note"))
    return {"removed": hit[0]}

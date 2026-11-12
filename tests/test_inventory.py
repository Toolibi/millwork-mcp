# Copyright (c) 2026 Prince Boafo. All rights reserved. PolyForm Noncommercial 1.0.0.
import json
import threading
from pathlib import Path

import pytest

from millwork_mcp import inventory as I
from millwork_mcp.pipeline import job_from_dict

EX = Path(__file__).resolve().parent.parent / "examples"
PRICING = json.loads((EX / "pricing.json").read_text())


def kitchen():
    return job_from_dict(json.loads((EX / "residential_kitchen.json").read_text()))


def hospital():
    return job_from_dict(json.loads((EX / "hospital_patient_room.json").read_text()))


@pytest.fixture
def store(tmp_path):
    return I.InventoryStore(tmp_path / "inv.json", EX / "inventory.json")


def test_seed_copied_and_catalog_covers_price_list(store):
    inv = store.read()
    s = I.status(inv, PRICING)
    codes = {r["code"] for r in s["items"]}
    assert {"MEL-WHT-34", "WO-RIFT-34", "EB-WO-RIFT-34", "HW-HINGE"} <= codes
    assert I.catalog(PRICING)["HW-DOWEL-8X35"]["unit"] == "ea"
    low = I.status(inv, PRICING, low_only=True)
    assert low["stock_value"] == s["stock_value"] > 5000 and len(low["items"]) < len(s["items"])


def test_needs_match_the_estimate(store):
    from millwork_mcp.estimate import estimate_job
    est = estimate_job(kitchen(), PRICING)
    r = I.job_needs(kitchen(), PRICING, store.read(), use_remnants=False)
    need = {l["code"]: l["need"] for l in r["lines"]}
    for m in est["materials"]:
        assert need[m["material"]] == m["sheets"]
    hinges = next(h["qty"] for h in est["hardware"] if h["item"].startswith("Hinge"))
    assert need["HW-HINGE"] == hinges
    eb = sum(v for k, v in need.items() if k.startswith("EB-"))
    assert abs(eb - est["edgeband"]["lf"]) < 6        # per-material lf, each rounded up


def test_shortage_and_remnants(store):
    r = I.job_needs(kitchen(), PRICING, store.read())
    oak = next(l for l in r["lines"] if l["code"] == "WO-RIFT-34")
    assert r["sheets_saved_by_remnants"] == {"WO-RIFT-34": 1}
    assert oak["need"] == 6 and oak["short"] == 2 and not r["ready_to_cut"]
    assert r["shortage_cost"] == 330.0 and r["remnant_note"]
    # grain-matched fronts never go on offcuts
    for plan in r["remnant_plan"]:
        for u in plan["remnants"]:
            assert all("Door" not in lbl and "Drawer Front" not in lbl for lbl, _ in u["part_list"])


def test_reserve_consume_and_remnants(store):
    with store.transaction() as inv:
        I.reserve(inv, PRICING, kitchen())
    inv = store.read()
    row = next(x for x in I.status(inv, PRICING)["items"] if x["code"] == "WO-RIFT-34")
    assert row["reserved"] == 6 and row["available"] == -2 and row["status"] == "SHORT"
    assert sum(1 for r in inv["remnants"] if r.get("reserved_for") == "R-2611") == 2
    # the purchase list picks the shortage up (plus topping back to the reorder point)
    pl = I.purchase_list(inv, PRICING)
    oak = next(l for l in pl["lines"] if l["code"] == "WO-RIFT-34")
    assert oak["order_qty"] >= 2 and "R-2611" in oak["for_jobs"]
    # re-checking a reserved job doesn't count its own reservation twice
    again = I.job_needs(kitchen(), PRICING, inv)
    assert next(l for l in again["lines"] if l["code"] == "MEL-WHT-34")["reserved_for_other_jobs"] == 0
    with store.transaction() as inv:
        out = I.consume(inv, "R-2611", [{"material": "WO-RIFT-34", "length": 40, "width": 20,
                                         "location": "Remnant rack 1"}], PRICING)
    inv = store.read()
    assert len(out["remnants_used"]) == 2 and out["remnants_added"][0]["id"] == "R0005"
    assert inv["items"]["WO-RIFT-34"]["on_hand"] == -2 and out["warnings"]
    assert inv["items"]["MEL-WHT-34"]["on_hand"] == 40 - 5
    with store.transaction() as inv, pytest.raises(ValueError):
        I.consume(inv, "R-2611")


def test_release_returns_stock(store):
    with store.transaction() as inv:
        I.reserve(inv, PRICING, hospital(), use_remnants=False)
        I.release(inv, "C-4120")
    inv = store.read()
    assert all(r["reserved"] == 0 for r in I.status(inv, PRICING)["items"])
    assert inv["reservations"]["C-4120"]["status"] == "released"


def test_receive_count_validation(store):
    with store.transaction() as inv:
        assert I.receive(inv, PRICING, "WO-RIFT-34", 10, "PO 1043")["on_hand"] == 14
        assert I.count(inv, PRICING, "WO-RIFT-34", 12)["difference"] == -2
        with pytest.raises(ValueError):
            I.receive(inv, PRICING, "WO-RIFT-34", 1.5)          # sheets are whole
        with pytest.raises(ValueError):
            I.receive(inv, PRICING, "NOPE", 1)
        with pytest.raises(ValueError):
            I.receive(inv, PRICING, "HW-HINGE", -5)
        with pytest.raises(ValueError):
            I.receive(inv, PRICING, "HW-HINGE", float("nan"))
        with pytest.raises(ValueError):
            I.add_remnant(inv, PRICING, "MEL-WHT-34", 20, 6)     # too small to keep
        with pytest.raises(ValueError):
            I.add_remnant(inv, PRICING, "MEL-WHT-34", 120, 40)   # bigger than a sheet
        assert I.receive(inv, PRICING, "EB-WO-RIFT-34", 12.5)["on_hand"] == 262.5
    assert [e["action"] for e in store.read()["log"]][:2] == ["receive", "count"]


def test_concurrent_writes_do_not_lose_updates(store):
    store.read()

    def add():
        for _ in range(10):
            with store.transaction() as inv:
                I.receive(inv, PRICING, "HW-HINGE", 1)
    ts = [threading.Thread(target=add) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert store.read()["items"]["HW-HINGE"]["on_hand"] == 200 + 40


def test_server_tools_registered():
    from millwork_mcp import server
    names = set(server.mcp._tool_manager._tools)
    assert {"inventory_status", "check_job_materials", "reserve_job_materials",
            "consume_job_materials", "release_job_materials", "receive_stock", "count_stock",
            "log_remnant", "purchase_list"} <= names

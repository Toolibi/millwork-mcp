# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""Machine profiles: how the shop actually cuts and bores parts.

Two workflows are supported:

* ``nested``    - a nested-based CNC router (e.g. a HOMAG CENTATEQ from Stiles running
                  woodWOP) cuts every part straight out of the full sheet and does the
                  face boring, hinge cups and dados in the same program.
* ``panel_saw`` - a panel saw rips and crosscuts parts, then a point-to-point CNC bores them.

Every setting carries a source tag, the idea borrowed from CabinetCRAFT:
  "shop"     - confirmed by the shop
  "program"  - copied from a real program that ran on the machine
  "assumed"  - a reasonable default nobody has confirmed yet
Anything still "assumed" is reported as a review flag, and machine programs built from
assumed values are marked unverified until someone proves them on the machine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MachineProfile:
    key: str
    name: str
    type: str                          # "nested" | "panel_saw"
    # --- nested router ---
    tool_dia: float = 0.375            # contour (cut-out) bit, inches
    part_spacing: float = 0.5          # gap between parts on the nest (>= tool_dia)
    sheet_margin: float = 0.25         # kept clear around the sheet edge
    cut_through: float = 0.008         # depth below the panel into the spoilboard, inches
    contour_tool: int = 101            # woodWOP tool number for the cut-out bit
    dado_tool: int = 102               # woodWOP tool number for the 1/4" dado bit
    clamex_tool: int = 103             # woodWOP tool number for the Lamello P-System cutter
    drill_mode: str = "LS"             # woodWOP vertical drilling mode
    horizontal_boring: bool = False    # can the machine bore into part edges?
    small_part_in: float = 4.0         # parts narrower than this are cut first / flagged
    feed_ipm: float = 700.0            # contour feed, inches per minute
    load_min_per_sheet: float = 3.0    # load, label, unload and clean per sheet
    sec_per_bore: float = 2.0
    edge_bore_sec: float = 4.0         # per edge bore on the separate horizontal borer
    hr_per_prelam_sheet: float = 0.5   # press a full sheet of laminate before nesting
    bed_length: float = 145.0          # largest sheet the table holds, inches (5x12 bed)
    bed_width: float = 61.0
    sources: dict = field(default_factory=dict)

    @property
    def is_nested(self) -> bool:
        return self.type == "nested"

    def unconfirmed(self) -> list[str]:
        return sorted(k for k, v in self.sources.items() if v == "assumed")


def load_machine(data: dict | str | Path) -> MachineProfile:
    if not isinstance(data, dict):
        data = json.loads(Path(data).read_text())
    values = {k: (v["value"] if isinstance(v, dict) and "value" in v else v)
              for k, v in data.items() if not k.startswith("_")}
    sources = {k: v.get("source", "assumed") for k, v in data.items()
               if isinstance(v, dict) and "value" in v}
    return MachineProfile(**values, sources=sources)


PANEL_SAW = MachineProfile("panel_saw_cnc", "Panel saw + point-to-point CNC", "panel_saw",
                           horizontal_boring=True)


def machine_from_pricing(pricing: dict) -> MachineProfile:
    m = pricing.get("machine")
    return load_machine(m) if m else PANEL_SAW

# Copyright (c) 2026 Prince Boafo. All rights reserved.
# Licensed under the PolyForm Noncommercial License 1.0.0 (see LICENSE).
# Commercial use requires a separate license from the author - see COMMERCIAL.md.

"""Core data models. All dimensions are in inches unless noted."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

CabinetType = Literal["base", "wall", "tall", "drawer_base", "sink_base", "bookcase", "microwave"]
BASE_TYPES = ("base", "drawer_base", "sink_base")
OPEN_TYPES = ("bookcase", "microwave")   # interior is visible -> exposed material, grain rules


@dataclass
class Material:
    code: str                 # e.g. "MEL-WHT-34"
    description: str
    thickness: float
    sheet_length: float = 97.0  # along the grain / long direction
    sheet_width: float = 49.0
    sheet_cost: float = 0.0
    grained: bool = False       # if True, parts may not be rotated on the sheet


@dataclass
class Cabinet:
    tag: str                    # e.g. "B1" as shown on the drawing
    type: CabinetType
    width: float
    height: float
    depth: float
    qty: int = 1
    doors: int = 0
    drawers: int = 0
    adj_shelves: int = 0
    fixed_shelves: int = 0
    opening_height: float = 0.0     # microwave: clear opening below the fixed shelf
    finished_ends: str = ""         # "", "L", "R" or "LR": sides seen from outside
    joinery: Optional[str] = None   # overrides the job default
    against_wall: bool = True       # attached to a wall -> gets hanging nailers
    notes: str = ""

    def validate(self, job: "Job | None" = None) -> list[str]:
        """Return a list of review flags (not exceptions) for the estimator."""
        flags = []
        if self.width < 9:
            flags.append(f"{self.tag}: width {self.width}\" is below 9\" - confirm, may be a filler")
        if self.width > 48 and self.doors < 2 and self.type != "drawer_base":
            flags.append(f"{self.tag}: {self.width}\" wide with fewer than 2 doors - door will be oversized")
        if self.doors and self.width / self.doors > 24:
            flags.append(f"{self.tag}: door width over 24\" - check sag/weight")
        if self.type == "wall" and self.depth > 15:
            flags.append(f"{self.tag}: wall cabinet depth {self.depth}\" exceeds typical 12-15\"")
        if self.type in BASE_TYPES and not 30 <= self.height <= 36:
            flags.append(f"{self.tag}: base height {self.height}\" outside typical 30-36\" box height")
        if self.type == "sink_base" and self.drawers:
            flags.append(f"{self.tag}: sink base with drawers - confirm plumbing clearance / false fronts")
        if self.type == "microwave":
            if self.opening_height <= 0:
                flags.append(f"{self.tag}: microwave cabinet has no opening height - get the appliance spec")
            elif self.opening_height < 14 or self.depth < 15:
                flags.append(f"{self.tag}: microwave opening {self.opening_height}\" high x "
                             f"{self.depth}\" deep - check against the appliance cut-out spec and vent")
        if self.type == "bookcase" and self.width > 36 and self.adj_shelves:
            flags.append(f"{self.tag}: {self.width}\" span on 3/4\" shelves will sag under books - "
                         "add a divider, thicker shelf or stiffener")
        joinery = self.joinery or (job.joinery if job else None)
        if joinery == "confirmat" and (self.finished_ends or self.type in OPEN_TYPES):
            flags.append(f"{self.tag}: confirmat heads will show on finished/exposed sides - "
                         "use caps, applied panels, or switch this cabinet to dowel or Clamex")
        return flags


@dataclass
class Part:
    cabinet_tag: str
    name: str
    material: str               # Material.code
    length: float               # finished length, ALWAYS the grain direction
    width: float
    qty: int = 1
    edgeband: dict = field(default_factory=dict)   # {"L1":True,"L2":False,"W1":..,"W2":..}
    machining: list[dict] = field(default_factory=list)
    grain_group: Optional[str] = None   # parts cut in sequence from one grain-match blank
    seq: Optional[int] = None           # position within the grain-match group
    grain_blank: Optional[tuple] = None  # (length, width) of the blank the group comes from
    grain_axis: Optional[str] = None     # fronts: "height" (vertical grain) or "width"
    exposed: bool = False               # face is seen in the finished cabinet
    laminate: Optional[str] = None      # pre-lam face (Material.code), pressed before machining
    prelam_blank: Optional[tuple] = None  # (length, width) of the oversize core blank

    @property
    def area_sqft(self) -> float:
        return self.length * self.width * self.qty / 144.0

    @property
    def edgeband_lf(self) -> float:
        e = self.edgeband
        inches = (self.length * (e.get("L1", 0) + e.get("L2", 0))
                  + self.width * (e.get("W1", 0) + e.get("W2", 0)))
        return inches * self.qty / 12.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["edgeband_lf"] = round(self.edgeband_lf, 2)
        return d


@dataclass
class Job:
    job_number: str
    name: str
    cabinets: list[Cabinet]
    case_material: str = "MEL-WHT-34"
    back_material: str = "MEL-WHT-14"
    door_material: str = "MEL-WHT-34"
    exposed_material: Optional[str] = None       # open interiors / finished ends (e.g. veneer ply)
    exposed_back_material: Optional[str] = None
    joinery: str = "dowel"                       # dowel | confirmat | cam_dowel | lamello_clamex
    front_grain: str = "vertical"                # vertical | horizontal (grained fronts only)
    grain_match: bool = True                     # cut each cabinet's fronts from one blank
    finished_end_method: str = "prelam"          # prelam | exposed_material
    finished_end_laminate: Optional[str] = None  # laminate pressed on finished ends
    nailer_material: str = "PLY-34"              # 3/4" plywood hanging nailers

"""Core data models. All dimensions are in inches unless noted."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

CabinetType = Literal["base", "wall", "tall", "drawer_base", "sink_base"]


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
    notes: str = ""

    def validate(self) -> list[str]:
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
        if self.type in ("base", "drawer_base", "sink_base") and not 30 <= self.height <= 36:
            flags.append(f"{self.tag}: base height {self.height}\" outside typical 30-36\" box height")
        if self.type == "sink_base" and self.drawers:
            flags.append(f"{self.tag}: sink base with drawers - confirm plumbing clearance / false fronts")
        return flags


@dataclass
class Part:
    cabinet_tag: str
    name: str
    material: str               # Material.code
    length: float               # finished length (grain direction)
    width: float
    qty: int = 1
    edgeband: dict = field(default_factory=dict)   # {"L1":True,"L2":False,"W1":..,"W2":..}
    machining: list[dict] = field(default_factory=list)  # bores/dados for DXF export

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

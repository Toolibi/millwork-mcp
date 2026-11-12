# millwork-mcp

> **Source-available, not open source.** Copyright (c) 2026 Prince Boafo. Free to read, evaluate, and use for personal, educational, and research purposes under the [PolyForm Noncommercial License 1.0.0](LICENSE). **Any business use, resale, hosting, or reuse in another product requires a commercial license.** See [COMMERCIAL.md](COMMERCIAL.md).

**An MCP server that gives an AI assistant real cabinet-shop tools: drawing review, takeoffs, estimating, panel-saw nesting, and CNC-ready DXF output.**

Built by a 20-year cabinet/millwork and CNC programmer to show how AI gets deployed into a non-software industry the right way: the model reads drawings and talks to people; deterministic code does everything that has to be exact.

## The problem

In a custom cabinet shop, one job goes through the same manual chain every time:

1. An estimator reads architectural PDFs and DWGs and builds a cabinet schedule by hand.
2. The schedule is re-keyed into design software (e.g. Cabinet Vision) to get parts and a bid.
3. Engineering exports parts to CAM (e.g. S2M Center) for router nests and panel-saw lists.

Estimating alone typically takes hours per kitchen, and every re-key is a chance for an error that shows up as scrap on the shop floor.

## What this does

```
Drawings (PDF / DWG)
      │  AI reads & extracts the cabinet schedule (vision + AutoCAD MCP)
      ▼
review_cabinet_schedule ── flags bad sizes, duplicate tags, oversized doors,
                           material codes not in the price list
      ▼
build_cutlist ─────────── construction standard → every part, edgebanding, machining
      ▼
nest_sheets ───────────── router nest (MaxRects) or panel-saw guillotine → sheets, yield
      ▼
compare_sheet_sizes ───── same parts on 4x8 / 5x8 / 4x10 / 5x10 (and alternate cores)
                           → sheets, yield, material $, router time, best option
      ▼
estimate_and_export ───── materials from the real nest + hardware + labor + markup
                           → estimate.json, cutlist.csv, saw_list.csv,
                             per-part DXFs with machining layers, nest diagrams
      ▼
Human review → woodWOP / CAM simulation → machine
```

### Machines: nested router (default) or panel saw

The shop profile in `examples/pricing.json` is a **Stiles / HOMAG nested-based CNC router running woodWOP**:

- **Router nesting.** Parts are cut straight from full sheets with MaxRects nesting (no guillotine limit), spaced by the cutter, kept off the sheet edge, and grain-locked on grained material. A multi-start search keeps the layout with the fewest sheets. The sample kitchen nests on 8 sheets of 3/4" melamine at 84% yield, against 9 on the panel saw.
- **Shop rules on the nest.** Grain-matched fronts are nested as one group in sequence, so the figure still runs door to door. Pre-lam parts nest on laminated full sheets pressed before nesting. Each sheet bores and grooves first, then cuts parts out smallest first while vacuum is strongest. Small parts are flagged for onion skin or tabs.
- **woodWOP output.** One DXF per sheet in millimetres, on the layer names woodWOP's DXF import reads: `Werkstk_19` (workpiece), `V_BohrLS_12_7` (vertical bores at true diameter), and `V_Fraes_<Z>T<tool>` (cut-outs on the tool centre and dados). Parts are placed by rotation only, never mirrored, so every part keeps its machined face up.
- **Edge boring.** Edge bores (dowel holes into part edges) go to `edge_boring_list.csv` for the horizontal borer when the router has no horizontal unit.
- **Estimate.** Router time per sheet covers load, cut path at feed rate, and bores, plus horizontal boring and pre-lam press time.
- **Provenance.** Every machine setting is tagged `shop`, `program`, or `assumed`. While anything is still assumed (tool numbers, Z convention, drilling mode, spacing), nest programs are reported as **UNVERIFIED** until one sheet has been simulated in woodWOP and run.

Left and right sides are generated as mirror images (Side L / Side R), because both are machined inside-face-up.

Set `"type": "panel_saw"` for a saw + point-to-point shop. That mode uses the two-stage rip/crosscut optimizer and the saw list instead.

### Construction methods

**Shop construction spec (defaults).**

- **Backs and nailers:** every cabinet attached to a wall gets a 1/4" back in a dado set 3/4" in from the rear, plus two 3/4" plywood nailers, 4" wide, running the length between the sides. One nailer sits under the top and one above the bottom. They fill the back setback flush with the case rear, so the cabinet screws to the wall through solid plywood, and the nailer ends are bored for the same joinery as the case. Freestanding pieces set `against_wall: false`.
- **Base cabinet tops:** two 4" rails. The front rail is edgebanded to match the cabinet front; the rear rail is not banded.
- **Finished ends:** pre-laminated before machining. Laminate is pressed onto an oversize core blank (1/2" per edge), trimmed on the saw, and then bored on the CNC, so every hole goes through a finished face. The saw list nests the core blank and the laminate separately, the route reads `prelam > saw > CNC`, and the estimate adds press time per panel. Set `finished_end_method: exposed_material` to use veneer ply instead.

**Case joinery, laid out on the 32mm system.** Joinery can be set per job and overridden per cabinet.

- **Dowel spec:** 8mm dowels in 3/4" material, 5mm in 1/2" and 3/8". The thinner of the two parts sets the size, and face bores always leave 1/8" of material behind them. Layout from the front edge is 32mm, then 96mm (a 64mm interval), then every 96mm, plus a closing dowel 32mm in front of the back of the joint (on bottoms and tops, 32mm in front of the back dado). Pitch dowels that would land within 32mm of the closing dowel are dropped. On a 24" base bottom that is 32, 96, 192, 288, 384, 480 and 552mm. Narrow parts such as 4" rails and nailers end up with a dowel 32mm in from each end.
- **Other connectors** start on the 37mm system line and step on the 32mm grid.
- Case thickness comes from the actual material, so 1/2" casework gets 1/2" geometry and 5mm dowels automatically. Thicknesses outside the dowel spec are flagged.

| Method | Side (gable) | Horizontal (bottom, top, stretcher, fixed shelf) | Notes |
|---|---|---|---|
| `dowel` | 8mm (3/4") or 5mm (1/2", 3/8") face bores at 32 / 96 / every 96mm + closing dowel 32mm from the back | Matching edge bores | Glued; full assembly time |
| `confirmat` | 7mm through-holes | 5mm edge pilots | Fastest assembly; flagged on finished or open cabinets because the heads show |
| `cam_dowel` | 5mm bolt bores at front/rear, 8mm dowels between | 15mm cam face bores + 8mm edge bores | Knock-down, ships flat |
| `lamello_clamex` | P-System face grooves | P-System edge grooves + 6mm access holes | Invisible knock-down |

**Machine routing.** Every part is cut on the panel saw. Any part with bores, hinge cups, grooves or dados (including the 5mm shelf-pin rows) then goes to the CNC, which bores shelf pins and joints in one program; no line-boring step is assumed. The cut list shows each part's route, `cnc_list.csv` lists every CNC program with its hole and groove counts, and the estimate charges CNC time per piece loaded and per operation.

Connector counts flow into the hardware list, and each method has its own assembly-time factor in the pricing table. Joint bores stop in front of the back dado, and shelf-pin rows skip anywhere a fixed part lands.

**Grain matching.** When fronts are in a grained material, every door and drawer front on a cabinet is laid out on one oversize blank, numbered in sequence (top to bottom, left to right), so the figure runs continuously across the doors and down a drawer bank. The *blank* is what gets nested and cut on the saw; fronts are recut from it in order. `front_grain` sets vertical or horizontal grain, and hinge-cup machining turns with the part.

**Open and finished cabinets.** `bookcase` and `microwave` cabinets have visible interiors, so sides, top, bottom, shelves and back switch to the exposed material (e.g. veneer ply) with sides grained vertically and horizontals grained side to side. Microwave cabinets get a fixed shelf at the appliance opening, doors above only, and adjustable shelves only above the shelf. Tall bookcases get a structural fixed shelf automatically. `finished_ends` puts exposed material on the sides that show from outside.

### Design principles (the FDE part)

- **The model never does the arithmetic.** It extracts structure from drawings; Python computes parts, nests and prices. Estimates are reproducible and auditable.
- **Nothing goes to a machine unverified.** Outputs are DXFs and lists for CAM import and simulation, never raw G-code sent to a router.
- **Shop rules are configuration, not code.** Construction methods live in `ConstructionStandard`; prices live in a pricing table (JSON locally, Supabase in production). A new shop is onboarded by editing data.
- **Review flags over silent assumptions.** Anything unusual is surfaced to the estimator instead of guessed.

## Related work

- [cabinet-mcp](https://github.com/chemrich/cabinet-mcp): frameless cabinet design with an MCP server, guillotine sheet layouts, and hardware part numbers. It has no CNC output and is aimed at small shops.
- [CabinetCRAFT](https://github.com/LaMka21/CabinetCRAFT): SCM `.xcs` and woodWOP `.mpr` output. The source-tagged machine settings here follow its approach.
- [gdrr-2bp](https://github.com/JeroenGar/gdrr-2bp): research-grade guillotine bin packing, a candidate panel-saw solver.
- HOMAG woodWOP DXF import documentation: the source for the layer names used in the nest DXFs.

## Sample results (placeholder prices)

| | `sample_kitchen.json` | `white_oak_kitchen.json` |
|---|---|---|
| Cabinets | 10, melamine, dowel | 8, rift white oak fronts, all four joinery methods |
| Parts generated | 138 | 120 |
| Main sheet goods | 9 sheets 3/4" melamine, 83% yield | 6 sheets 3/4" rift oak, 72% yield (grain-matched blanks) |
| Grain-match groups | - | 7 |
| Special cabinets | pantry | microwave, open bookcase, finished ends |
| Sell price (nested router) | ~$3,490 | ~$4,510 |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                 # 65 tests: hosted HTTP mode and key auth, nested router, woodWOP export, dowel spec, construction spec, joinery, grain match, CNC routing, nesting, estimate, DXF
python -m millwork_mcp.cli examples/sample_kitchen.json
python -m millwork_mcp.cli examples/white_oak_kitchen.json
```

### Use it from Claude (or any MCP client)

```json
{
  "mcpServers": {
    "millwork": {
      "command": "python",
      "args": ["-m", "millwork_mcp.server"],
      "cwd": "/path/to/millwork-mcp"
    }
  }
}
```

Then: *"Here's the kitchen elevation PDF. Extract the cabinet schedule, review it, and give me a bid and saw list."*

## Hosted mode (Azure / Microsoft Foundry)

`python -m millwork_mcp.http_server` serves the same tools over streamable HTTP for remote MCP clients such as Microsoft Foundry agents. The endpoint is locked with an API key, accepts only small stateless requests, and refuses to start without a strong key. `deploy/azure_deploy.sh` deploys it to Azure Container Apps from Cloud Shell, scaling to zero when idle. See [docs/AZURE_FOUNDRY.md](docs/AZURE_FOUNDRY.md).

## Tools

| Tool | What it does |
|---|---|
| `review_cabinet_schedule` | Sanity-checks an extracted schedule before anyone prices it |
| `build_cutlist` | Explodes cabinets into parts using the construction standard |
| `nest_sheets` | Nests one material for the shop's machine: router nest with cut order, or panel-saw rip/crosscut list |
| `get_machine_profile` | Machine settings with their source, and which are still unconfirmed |
| `estimate_and_export` | Full estimate plus all shop files |
| `list_joinery_methods` | Describes each joinery method so the AI can recommend one |
| `get_pricing` | Returns the pricing table so the AI can explain the number |

## DXF layer convention

Machining intent is encoded in layer names (placeholders, match them to your CAM import template):

| Layer | Meaning |
|---|---|
| `CUT_THRU_T0.750` | Part outline, through cut, 3/4" material |
| `DRILL_5MM_D0.500` | 5mm system / shelf-pin holes, 1/2" deep |
| `POCKET_35MM_D0.500` | 35mm hinge cup |
| `DADO_W0.250_D0.375` | Back panel dado |
| `DRILL_7MM_THRU` | Confirmat through-hole |
| `DRILL_15MM_D0.531` | Cam housing |
| `HDRILL_8MM_D1.000_W1` | Horizontal (edge) bore into end W1 or W2 |
| `LAMELLO_P14_FACE` / `LAMELLO_P14_EDGE_W1` | Clamex P-System grooves |

## Roadmap

- [x] **Phase 1:** schedule → parts → nest → estimate → DXF / CSV, as MCP tools
- [x] Joinery (dowel, confirmat, cam & dowel, Clamex) on the 32mm system; grain matching; open and microwave cabinets
- [ ] Grain matching across a run of cabinets (sequence-matched panels)
- [ ] **Phase 2:** drawing ingestion (PDF elevations via vision; DWG via Autodesk's AutoCAD MCP server), pricing in Supabase
- [x] Nested-router nesting and woodWOP per-sheet DXFs (unverified until run on the machine)
- [ ] woodWOP `.mpr` programs templated from a real program off the shop's machine
- [ ] **Phase 3:** Cabinet Vision / S2M import round-trip, web demo on Vercel
- [ ] Accuracy study: AI-assisted vs manual estimates on real jobs (time saved, % variance)

## Scope note

Sample prices and construction rules are illustrative. Verify every output against your own shop standards and CAM simulation before cutting material.


## License

Copyright (c) 2026 Prince Boafo. All rights reserved.

millwork-mcp is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). You may use it for noncommercial purposes. Commercial use (running it in a business, quoting or producing work for paying customers, reselling or hosting it, or building it into another product) requires a written license from the author. See [COMMERCIAL.md](COMMERCIAL.md).

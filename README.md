# millwork-mcp

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
review_cabinet_schedule ── flags bad sizes, duplicate tags, oversized doors
      ▼
build_cutlist ─────────── construction standard → every part, edgebanding, machining
      ▼
nest_panel_saw ────────── two-stage guillotine optimizer → sheets, yield, rip/crosscut list
      ▼
estimate_and_export ───── materials from the real nest + hardware + labor + markup
                           → estimate.json, cutlist.csv, saw_list.csv,
                             per-part DXFs with machining layers, nest diagrams
      ▼
Human review → CAM (S2M Center) → machine
```

### Construction methods

**Case joinery, laid out on the 32mm system.** Every joint starts on the 37mm system line and steps on the 32mm grid, so the same drill block that bores shelf-pin rows bores the joints. Joinery can be set per job and overridden per cabinet.

| Method | Side (gable) | Horizontal (bottom, top, stretcher, fixed shelf) | Notes |
|---|---|---|---|
| `dowel` | 8mm face bores | 8mm edge bores | Glued; full assembly time |
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

## Sample results (placeholder prices)

| | `sample_kitchen.json` | `white_oak_kitchen.json` |
|---|---|---|
| Cabinets | 10, melamine, dowel | 8, rift white oak fronts, all four joinery methods |
| Parts generated | 118 | 106 |
| Main sheet goods | 9 sheets 3/4" melamine, 83% yield | 6 sheets 3/4" rift oak, 72% yield (grain-matched blanks) |
| Grain-match groups | - | 7 |
| Special cabinets | pantry | microwave, open bookcase, finished ends |
| Sell price | ~$3,550 | ~$4,770 |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                 # 30 tests: construction, joinery, grain match, CNC routing, nesting, estimate, DXF
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

## Tools

| Tool | What it does |
|---|---|
| `review_cabinet_schedule` | Sanity-checks an extracted schedule before anyone prices it |
| `build_cutlist` | Explodes cabinets into parts using the construction standard |
| `nest_panel_saw` | Guillotine rip/crosscut optimization for one material |
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
- [ ] **Phase 3:** Cabinet Vision / S2M import round-trip, router nesting, web demo on Vercel
- [ ] Accuracy study: AI-assisted vs manual estimates on real jobs (time saved, % variance)

## Scope note

Sample prices and construction rules are illustrative. Verify every output against your own shop standards and CAM simulation before cutting material.

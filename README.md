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

### Design principles (the FDE part)

- **The model never does the arithmetic.** It extracts structure from drawings; Python computes parts, nests and prices. Estimates are reproducible and auditable.
- **Nothing goes to a machine unverified.** Outputs are DXFs and lists for CAM import and simulation, never raw G-code sent to a router.
- **Shop rules are configuration, not code.** Construction methods live in `ConstructionStandard`; prices live in a pricing table (JSON locally, Supabase in production). A new shop is onboarded by editing data.
- **Review flags over silent assumptions.** Anything unusual is surfaced to the estimator instead of guessed.

## Sample result (`examples/sample_kitchen.json`, placeholder prices)

| | |
|---|---|
| Cabinets | 10 (bases, drawer base, sink base, walls, pantry) |
| Parts generated | 118 |
| 3/4" melamine | 9 sheets, 83% yield |
| Edgebanding | ~330 LF |
| Hardware | 34 hinges, 6 slide pairs, 20 pulls, levelers, shelf pins |
| Sell price | ~$3,450 at sample rates |

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                 # 10 tests: construction, nesting, estimate, DXF validity
python -m millwork_mcp.cli examples/sample_kitchen.json
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
| `get_pricing` | Returns the pricing table so the AI can explain the number |

## DXF layer convention

Machining intent is encoded in layer names (placeholders, match them to your CAM import template):

| Layer | Meaning |
|---|---|
| `CUT_THRU_T0.750` | Part outline, through cut, 3/4" material |
| `DRILL_5MM_D0.500` | 5mm system / shelf-pin holes, 1/2" deep |
| `POCKET_35MM_D0.500` | 35mm hinge cup |
| `DADO_W0.250_D0.375` | Back panel dado |

## Roadmap

- [x] **Phase 1:** schedule → parts → nest → estimate → DXF / CSV, as MCP tools
- [ ] **Phase 2:** drawing ingestion (PDF elevations via vision; DWG via Autodesk's AutoCAD MCP server), pricing in Supabase
- [ ] **Phase 3:** Cabinet Vision / S2M import round-trip, router nesting, web demo on Vercel
- [ ] Accuracy study: AI-assisted vs manual estimates on real jobs (time saved, % variance)

## Scope note

Sample prices and construction rules are illustrative. Verify every output against your own shop standards and CAM simulation before cutting material.

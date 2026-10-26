"""Command line: python -m millwork_mcp.cli <job.json> [--pricing pricing.json] [--out dir]"""
import argparse
import json
from pathlib import Path

from .estimate import load_pricing
from .pipeline import run_job

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description="Estimate a cabinet job and export shop files")
    ap.add_argument("job")
    ap.add_argument("--pricing", default=str(ROOT / "examples" / "pricing.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    job = json.loads(Path(a.job).read_text())
    out = a.out or str(ROOT / "output" / job["job_number"])
    r = run_job(job, load_pricing(a.pricing), out)

    print(f"Job {r['job_number']} - {r['job_name']}")
    print(f"  {r['cabinet_count']} cabinets, {r['part_count']} parts")
    for m in r["materials"]:
        print(f"  {m['material']:<12} {m['sheets']:>3} sheets  {m['yield_pct']:>5.1f}% yield  ${m['cost']:,.2f}")
    print(f"  Edgebanding  {r['edgeband']['lf']} LF")
    print(f"  Labor        {r['labor']['hours']} hrs")
    print(f"  SELL PRICE   ${r['totals']['sell_price']:,.2f}")
    for f in r["review_flags"]:
        print(f"  ! {f}")
    print(f"  Files in {out}")


if __name__ == "__main__":
    main()

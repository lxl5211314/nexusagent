"""CLI for the guard ablation.

    python -m app.eval.guard_cli --kb <kb-id>

Runs ``sample`` (27 answerable) and ``unanswerable`` (18 outside the corpus)
under both arms and prints the two rates that matter: how many unanswerable
questions still got an answer, and how many answerable ones were wrongly
refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.db.base import init_db
from app.eval.guard_eval import ARMS, evaluate

DATASETS = ("sample", "unanswerable")


async def _main(args: argparse.Namespace) -> None:
    report: dict[str, object] = {}
    for dataset in args.datasets:
        for arm in args.arms:
            result = await evaluate(args.kb, dataset, arm, args.limit)
            metrics = result["metrics"]
            report[f"{dataset}/{arm}"] = {"metrics": metrics, "results": result["results"]}
            print(
                f"{dataset:<12} guard={arm:<3} cases={metrics['cases']:>3}"
                f" unsupported={metrics['unsupported_rate']}"
                f" over_refusal={metrics['over_refusal_rate']}"
                f" confidence={metrics['mean_confidence']}"
            )
            if args.show_cases:
                for row in result["results"]:
                    mark = "REFUSED" if row["refused"] else "ANSWERED"
                    print(f"   [{mark}] {row['confidence']:.2f} {row['question']} :: {row['answer'][:60]}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Guard ablation (prompt-only vs three-layer guard)")
    parser.add_argument("--kb", required=True, help="knowledge base id")
    parser.add_argument("--datasets", nargs="+", default=list(DATASETS))
    parser.add_argument("--arms", nargs="+", default=list(ARMS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--show-cases", action="store_true")
    parser.add_argument("--out", default="")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    init_db()
    main()

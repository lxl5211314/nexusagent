from __future__ import annotations

import argparse
import json

from app.db.base import init_db
from app.eval.runner import run_eval


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAG evaluation on a KB")
    parser.add_argument("--kb", required=True, help="knowledge base id")
    parser.add_argument("--dataset", default="sample", help="dataset name (data/eval/<name>.jsonl)")
    parser.add_argument("--limit", type=int, default=0, help="limit cases (0 = all)")
    args = parser.parse_args()

    init_db()
    result = run_eval(args.kb, args.dataset, args.limit)
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    for row in result["results"]:
        mark = "OK " if row["recall@5"] > 0 else "MISS"
        print(f"[{mark}] recall@5={row['recall@5']:.2f} mrr@5={row['mrr@5']:.2f} :: {row['question'][:60]}")


if __name__ == "__main__":
    main()

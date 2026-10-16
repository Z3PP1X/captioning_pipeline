#!/usr/bin/env python3
"""R10: retrieval metrics of the current RAG index on its held-out val split.

Run inside WSL:  source scripts/env.sh && uv run python scripts/eval_rag.py [--k 4] [--fusion both]
                    [--dense-mode both] [--query-words 0] [--log runs.jsonl]
Same numbers as GET /api/rag/eval. Metric definitions: src/pipeline/rag/evaluation.py.
Read-only: it never rebuilds the index (build it with scripts/index_rag.py or POST /api/rag/index).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pipeline.config import get_settings
from pipeline.rag.evaluation import evaluate_retrieval
from pipeline.rag.index_job import RagIndexJob
from pipeline.rag.retriever import RagIndexNotReady, RagRetriever


def _fmt(value: float | None) -> str:
    return "   -  " if value is None else f"{value:6.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAG retrieval on the val split.")
    parser.add_argument("--k", type=int, default=4, help="Cut-off for recall/hit/precision (default 4).")
    parser.add_argument("--max-queries", type=int, default=200, help="Val captions used as queries (default 200).")
    parser.add_argument("--fusion", choices=("rrf", "linear", "both"), default="rrf")
    parser.add_argument("--dense-mode", choices=("whole", "domain", "both"), default=None,
                        help="R1 dense scoring: whole caption vs per-domain chunks (default: server default).")
    parser.add_argument("--query-words", type=int, default=40,
                        help="Words of the val caption used as the query (0 = the whole caption).")
    parser.add_argument("--json", action="store_true", help="Print the raw JSON result(s).")
    parser.add_argument("--log", type=Path, default=None, help="Append the result(s) as JSON lines (compare runs).")
    parser.add_argument("--label", default="", help="Free-text label stored with --log entries.")
    args = parser.parse_args()

    settings = get_settings()
    retriever = RagRetriever(settings)
    index_meta = RagIndexJob(settings).index_meta()
    results = []
    fusions = ("rrf", "linear") if args.fusion == "both" else (args.fusion,)
    modes = ("whole", "domain") if args.dense_mode == "both" else (args.dense_mode,)
    for fusion in fusions:
        for mode in modes:
            try:
                res = evaluate_retrieval(
                    retriever, k=args.k, max_queries=args.max_queries, fusion=fusion, query_words=args.query_words,
                    dense_mode=mode,
                )
            except RagIndexNotReady as err:
                print(f"{err}\nBuild it first: source scripts/env.sh && uv run python scripts/index_rag.py")
                return 1
            results.append({**res, "index": index_meta or None, "label": args.label, "ran_at": time.time()})

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
    else:
        first = results[0]
        print(f"Index: {index_meta.get('count')} captions ({index_meta.get('source')}), "
              f"val {first['split']['val']}, candidates {first['split']['candidates']}, "
              f"{first['queries']} queries, k={first['k']}, query_words={first['query_words']}")
        print("fusion  dense  | sib R@k  sib hit  sib MRR  chance | tag R@k  tag P@k |  n_sib  n_tag")
        for r in results:
            s, t = r["siblings"], r["tags"]
            print(f"{r['fusion']:<7} {r['dense_mode']:<6} | {_fmt(s['recall_at_k'])}  {_fmt(s['hit_at_k'])}  {_fmt(s['mrr'])}  "
                  f"{_fmt(s['chance_recall_at_k'])} | {_fmt(t['recall_at_k'])}  {_fmt(t['precision_at_k'])} | "
                  f"{s['queries']:6d} {t['queries']:6d}")
        if not first["queries"]:
            print("No val captions in the index (the split is ~10%; index more captions).")

    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r) + "\n")
        print(f"Appended {len(results)} result(s) to {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

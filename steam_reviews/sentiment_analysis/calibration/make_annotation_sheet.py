from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

FIELDS = (
    "sample_id", "app_id", "game", "review_id", "entity_id", "entity_name",
    "text_clean", "human_label", "annotator", "notes",
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    args = parser.parse_args(argv)
    rows = []
    with open(args.input, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    if args.limit:
        rows = rows[:args.limit]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "csv":
        with out.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "sample_id": row.get("sample_id"),
                    "app_id": row.get("app_id"),
                    "game": row.get("game"),
                    "review_id": row.get("review_id"),
                    "entity_id": row.get("entity_id"),
                    "entity_name": row.get("entity_name"),
                    "text_clean": row.get("text_clean"),
                    "human_label": "",
                    "annotator": "",
                    "notes": "",
                })
    else:
        with out.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps({
                    "sample_id": row.get("sample_id"),
                    "app_id": row.get("app_id"),
                    "game": row.get("game"),
                    "review_id": row.get("review_id"),
                    "entity_id": row.get("entity_id"),
                    "entity_name": row.get("entity_name"),
                    "text_clean": row.get("text_clean"),
                    "human_label": None,
                    "annotator": None,
                    "notes": None,
                }, ensure_ascii=False) + "\n")
    print(json.dumps({"event": "annotation_sheet", "rows": len(rows), "format": args.format, "path": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

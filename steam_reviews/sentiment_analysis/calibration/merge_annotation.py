from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep-unlabeled", action="store_true")
    args = parser.parse_args(argv)
    label_rows = {}
    with open(args.labels, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                label_rows[row["sample_id"]] = row
    merged = []
    with open(args.samples, "r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            label = label_rows.get(row.get("sample_id"), {})
            row["human_label"] = label.get("human_label")
            row["annotator"] = label.get("annotator")
            row["notes"] = label.get("notes")
            merged.append(row)
    labeled_rows = [row for row in merged if row["human_label"] in {"positive", "negative", "neutral"}]
    output_rows = merged if args.keep_unlabeled else labeled_rows
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as stream:
        for row in output_rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"event": "merge_annotation", "rows": len(merged), "labeled": len(labeled_rows),
                      "written": len(output_rows), "path": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

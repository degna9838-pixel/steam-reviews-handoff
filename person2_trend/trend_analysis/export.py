"""Write per-game trend results for the dashboard teammate."""

import json
from pathlib import Path


def analysis_root(root=None):
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parent.parent / "analysis"


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _stamp(computed_at):
    return (
        computed_at.replace("-", "")
        .replace(":", "")
        .replace("+00:00", "Z")
        .replace("+0000", "Z")
    )


def export_results(results, root=None):
    """Write one latest file per game and one combined file.

    Returns the combined output path. A timestamped copy is kept beside
    each latest file so a later run does not erase the previous result.
    """
    destination = analysis_root(root)
    written = []
    for result in results:
        game_dir = destination / "games" / str(result["app_id"])
        latest = game_dir / "trends_latest.json"
        stamped = game_dir / f"trends_{_stamp(result['computed_at'])}.json"
        _write_json(latest, result)
        _write_json(stamped, result)
        written.append(str(latest))

    combined = {
        "schema_version": "trend-result-v1",
        "algorithm_version": results[0]["algorithm_version"] if results else None,
        "games": results,
    }
    combined_path = destination / "trends_latest.json"
    _write_json(combined_path, combined)
    return {"combined": str(combined_path), "games": written}

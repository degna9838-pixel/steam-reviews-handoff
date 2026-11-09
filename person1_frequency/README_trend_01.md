# CS5481 Steam Review Trends — Person 1 Handoff

- **Component:** Data Integration & Keyword Frequency Analysis
- **Upstream:** Steam reviews ingestion and English cleaning package, version 1.3.0
- **Downstream:** Person 2 — Trending Keyword Detection & Evaluation
- **Games:** CS2 (`730`), Dota 2 (`570`), Phantom Blade Zero (`4115450`)

## 1. Scope and ownership

Person 1 provides a reusable interface to the *current* review snapshots: synchronize changes from the upstream API, query reviews by game and UTC publication-time window, compute TF / DF / document rate, and expose synchronization health. Person 1 does **not** implement the Steam crawler, upstream text-cleaning pipeline, the underlying cursor transaction logic, sentiment analysis, trend scoring, smoothing, or the dashboard.

Existing upstream implementation is reused through `examples/consume_api.py`; avoid duplicating its event and database logic.

## 2. File structure

Place the files below inside the extracted `steam_reviews/` folder of `steam_reviews_task1_trend.zip`:

```text
steam_reviews/
├── start.bat                         # upstream (Windows)
├── examples/
│   └── consume_api.py               # upstream
├── trend_analysis/                  # Person 1
│   ├── __init__.py
│   ├── consumer.py                   # invokes upstream consumer
│   ├── frequency.py                  # TF / DF / document rate
│   ├── storage.py                    # current SQLite snapshots + UTC window
│   └── status.py                     # local cursor vs upstream health
├── tests/
│   ├── test_trend_consumer.py
│   ├── test_trend_frequency.py
│   ├── test_trend_revision.py
│   ├── test_trend_status.py
│   └── test_trend_storage.py
├── trend_handoff_check.py            # two-window interface smoke check
└── README_trend_01.md           # this file
```

Do not copy or commit `.venv/`, `data/`, `*.sqlite3`, runtime logs, personal API credentials, or proxy secrets. The upstream package contains its own source code, requirements, and documentation; read `README.md`, `docs/API_GUIDE.md`, and `docs/DATA_DICTIONARY.md` for upstream details.

### Installation from the shared GitHub repository

This handoff is an extension of the upstream Steam reviews project,
not a standalone application.

1. Obtain `steam_reviews_task1_trend.zip` from the shared repository.
2. Extract it and locate the `steam_reviews/` directory.
3. Obtain `steam_reviews_person1_frequency_handoff.zip`.
4. Extract the Person 1 ZIP directly into that same `steam_reviews/`
   directory, merging `trend_analysis/`, `tests/`, the acceptance
   script, and the README.
5. Follow the upstream README to install the Python dependencies
   and configure the virtual environment.
6. Run all commands from the merged `steam_reviews/` directory.

Verify the merged installation contains:

- `start.bat`
- `examples/consume_api.py`
- `trend_analysis/frequency.py`
- `trend_analysis/storage.py`
- `trend_analysis/consumer.py`
- `trend_analysis/status.py`
- `trend_handoff_check.py`

Do not execute the full project directly from the standalone
`person1_frequency/` folder, because the upstream collector
and API components are required.

## 3. Quick start (Windows PowerShell)

Open PowerShell in the extracted `steam_reviews/` directory. Use the Python virtual environment and dependencies installed per the upstream package README. The examples below assume `.venv` is already configured.

**Terminal 1 — live upstream service**

```powershell
$env:NO_PROXY = "127.0.0.1,localhost"
.\start.bat
```

If Steam access requires a proxy, configure `HTTP_PROXY` and `HTTPS_PROXY` in your own terminal according to your environment; do not commit personal proxy settings or credentials. `start.bat --sample` is *synthetic offline mode* and must not be mistaken for real Steam data.

**Terminal 2 — synchronize all games**

```powershell
$env:NO_PROXY = "127.0.0.1,localhost"
.\.venv\Scripts\python.exe -m trend_analysis.consumer --consumer trend_live
```

Or synchronize one game:

```powershell
.\.venv\Scripts\python.exe -m trend_analysis.consumer --consumer trend_live --app-id 730
```

`trend_live` is a suggested dedicated consumer name for live reviews; `trend_demo` was used for offline samples. **Keep these consumer names separate**. Sync commands consume the currently available change feed and exit; they do not create a continuously running downstream analysis service. The upstream collector continues independently.

The current-snapshot SQLite files are expected at:

```text
data/consumer_trend_live.sqlite3
data/games/570/consumer_trend_live.sqlite3
data/games/4115450/consumer_trend_live.sqlite3
```

All paths are relative to `steam_reviews/`.

## 4. API contract for Person 2

```python
from datetime import datetime, timedelta, timezone
from trend_analysis.storage import get_top_keywords, get_reviews
from trend_analysis.status import get_sync_status

app_id = 730                     # 730, 570, or 4115450
consumer = "trend_live"

# Synchronize via CLI before each scheduled analytics run.
status = get_sync_status(app_id, consumer_name=consumer)
print(status["sync_status"], status["consumer_lag_events"])

# Freeze one UTC end time for *both* comparison windows.
end = datetime.now(timezone.utc)
recent_start = end - timedelta(hours=1)
baseline_start = recent_start - timedelta(hours=24)

baseline = get_top_keywords(
    app_id=app_id,
    start_time=baseline_start,
    end_time=recent_start,
    top_n=None,
    consumer_name=consumer,
)
recent = get_top_keywords(
    app_id=app_id,
    start_time=recent_start,
    end_time=end,
    top_n=None,
    consumer_name=consumer,
)

print(baseline["total_reviews"], recent["total_reviews"])
print(recent["keywords"][:5])
```

**Time semantics:** `created_at` is the review publication time. `start_time` is inclusive and `end_time` exclusive: `[start_time, end_time)`. Supply timezone-aware UTC datetimes or ISO-8601 strings with `Z`/an explicit offset; naive times are rejected. The 24-hour baseline and 1-hour recent window do not overlap. `updated_at` and `first_seen_at` are *not* used as window membership timestamps.

`get_reviews(...)` returns matching **current** review records (including records not usable for trends); `get_top_keywords(...)` filters out reviews whose `usable_for_trend` is false.

**`get_top_keywords` result fields**

| Key | Meaning |
| --- | --- |
| `app_id` | Game identifier |
| `consumer_name` | Local consumer namespace |
| `start_time`, `end_time` | UTC bounds, `[start, end)` |
| `stored_reviews_in_window` | Current snapshot records in the requested window, including unusable ones |
| `total_reviews` | Usable reviews in the window; denominator for document rate |
| `keywords` | List of `{term, tf, df, document_rate}` sorted by descending DF, then TF, then term |

- **TF (term frequency)**: total occurrences of a token across usable reviews.
- **DF (document frequency)**: number of distinct usable reviews containing a token at least once.
- **document_rate**: `df / total_reviews`, between 0 and 1. For an empty window the keyword list is empty and division is avoided.
- `top_n=None` returns all terms; **Person 2 should use this for trend detection**. With `top_n=10`, low-frequency but newly surging terms might be lost.
- Tokens come from upstream `tokens_trend` (English cleaning pipeline). Additional stop-word or domain-word filtering is a separate, optional analytical choice and should be documented rather than silently modifying the upstream data.

## 5. Synchronization state and data readiness

```python
from trend_analysis.status import get_sync_status

status = get_sync_status(730, consumer_name="trend_live")
print(status)
```

Important fields:

| Field | Interpretation |
| --- | --- |
| `local_cursor`, `upstream_latest_cursor` | Change-feed progress; **not** Steam pagination cursor and not review counts |
| `consumer_lag_events` | Count of unconsumed upstream event positions at the moment checked |
| `stream_matches` | Consumer and upstream stream IDs match |
| `caught_up` | Stream matches and cursors are equal at health-check time |
| `mode` | `live` vs `offline` |
| `backfill_status` | Collector's reported historical backfill stage |
| `backfill_reported_complete` | Live collector reported `completed` |
| `ready_for_provisional_analysis` | Live, matching stream, caught up, data/cleaning ready, running collector, and backfill reported complete |
| `historical_completeness_verified` | Conservatively `False`: no independent proof of exhaustive Steam historical coverage |
| `sync_status` | Main reason for an unavailable/not-ready state or ready status |

For scheduled analysis, first synchronize each game, then inspect `get_sync_status` and attach status to the output. Live collection can append more events immediately after a sync; `caught_up=False` with a small lag may simply require another sync. If `stream_matches=False`, do **not** overwrite the stored stream ID or cursor. Investigate source identity and rebuild using a fresh consumer namespace if necessary.

A status check and the SQLite window queries are **not an atomic cross-system snapshot**; changes may arrive between them. `ready_for_provisional_analysis=True` means operational readiness at the observed instant, not guaranteed zero data lag for all time. A completed 30-day backfill is the *upstream collector's report* and does not independently verify all Steam reviews were captured.

## 6. Correctness, updates, and limitations

- `reviews` stores the latest record for each review ID. Changed reviews replace their old token contributions in a new TF/DF calculation; the same review is not intentionally counted twice.
- Reviews changing to `usable_for_trend=False` remain stored but no longer contribute to TF/DF.
- Different games have separate review data, cursors, and stream IDs.
- A historical publication-time window is evaluated using **current snapshots**, not the review text or usability state as it existed at a past moment. This is suitable for the current-state trend pipeline but is not a time-travel audit of historical versions.
- A keyword with `DF=1` in a one-review recent window will have `document_rate=1.0`; this is mathematically valid but **not strong trend evidence**. Person 2 should handle sparse samples, smoothing, minimum support, zero baselines, and statistical/evaluation methodology.
- Current storage implementation scans the local `reviews` snapshots and filters by `created_at` in Python. In the observed local tests, double-window queries across existing data completed in under 0.5 s per game, but this is **not a universal performance guarantee**.
- Source cleaning quality, English language inference, Steam API limits, and backfill scope are inherited from the upstream package.

## 7. Tests and reproducibility

Run from the `steam_reviews/` directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_trend_*.py" -v
```

**Observed result (2026-11-09, user-run):** `Ran 24 tests ... OK`.

Coverage includes: TF/DF edge cases; usable-only filtering; UTC half-open windows; game isolation; database missing/invalid timestamps; consumer wrapper arguments; stream/cursor/readiness cases; snapshot revision replacement; usability withdrawal; repeated identical snapshot not inflating frequencies.

**Observed live integration evidence (2026-11-09, at different moments):** successful live sync consumed through cursors `18634` (CS2), `2833` (Dota 2), and `10973` (Phantom Blade Zero), with matching upstream streams, completed reported backfill, and zero lag at the synchronization check. A later two-window acceptance check reported 24-hour/1-hour usable review counts of 1050/6 (CS2), 51/1 (Dota 2), and 498/15 (Phantom Blade Zero). Two-window query times were approximately 0.347 s, 0.071 s, and 0.431 s respectively on one local Windows machine. Later checks may show different cursors, lags, and counts as live collection continues. These figures are integration evidence, not finalized project findings.

**Important distinction:** the revision and repeated-snapshot tests use a temporary SQLite fixture to verify Person 1 analytics. They do **not** directly test the upstream consumer's transaction-level replay or crash recovery. The real consumer was also run repeatedly and shown to resume from prior cursors, but exhaustive fault injection was not performed.

With the live API running, run the full two-window smoke test:

```powershell
.\.venv\Scripts\python.exe .\trend_handoff_check.py
```

It evaluates a frozen 1-hour recent and preceding 24-hour baseline per game, prints keyword counts and status, and warns for sparse windows or pending events. It does not calculate trend scores. Retain redacted terminal output in team evidence when helpful; do not commit large private/local review databases.

## 8. Person 2 integration checklist

1. Use the **same `app_id`** for sync, readiness, baseline, and recent queries.
2. Use a dedicated **live consumer name** and preserve its SQLite state between runs.
3. Run sync before each calculation; handle failed sync, stream mismatch, not-ready, and nonzero lag without silently declaring data complete.
4. Freeze one UTC `end` and request adjacent `[end-25h, end-1h)` and `[end-1h, end)` windows.
5. Request `top_n=None` and use `total_reviews` to normalize **DF per review**, not raw counts across unequal windows.
6. Handle empty/sparse windows, zero baseline, thresholding, and smoothing in Person 2's trend algorithm.
7. Keep source readiness, window timestamps, and query settings in experiment outputs for reproducibility.

## 9. Contribution boundary

**Person 1 authored:** `trend_analysis/{consumer,frequency,storage,status}.py`, its package init, `tests/test_trend_*.py`, `trend_handoff_check.py`, and this handoff documentation.

**Reused upstream teammate work:** Steam API collection, source-language cleaning and `tokens_trend` generation, FastAPI service, `examples/consume_api.py` change-feed transactions, underlying SQLite event-upsert implementation, and historical backfill logic. This distinction should remain explicit in the team report and contribution statement.

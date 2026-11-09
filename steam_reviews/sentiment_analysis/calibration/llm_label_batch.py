from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LABELS = {"positive", "negative", "neutral"}


def entity_name(job: dict) -> str:
    entity = job.get("entity") or {}
    return entity.get("name") or entity.get("entity_id") or "unknown"


def clip_text(text: str, max_chars: int) -> str:
    if not max_chars or len(text) <= max_chars:
        return text
    half = max(1, max_chars // 2)
    return text[:half] + " ... " + text[-half:]


def job_text(job: dict, style: str, max_text_chars: int) -> str:
    if style == "context":
        context = job.get("llm_input") or job.get("text_clean", "")
        return clip_text(context, max_text_chars or 1200)
    return clip_text(job.get("text_clean", ""), max_text_chars)


def alias_text(job: dict) -> str:
    aliases = (job.get("entity") or {}).get("aliases") or []
    return ", ".join(aliases[:12]) if aliases else "none"


CONTEXT_RULES = (
    "You are a careful sentiment annotator for Steam reviews. "
    "Classify the reviewer's attitude toward the specified entity using only the provided review context. "
    "Judge intended meaning, including sarcasm/irony.\n"
    "Rules: positive = praise/satisfaction/approval/improvement; "
    "negative = criticism/complaint/disappointment/failure/request to fix something broken; "
    "neutral = factual mention/question/no clear evaluative stance, or genuinely balanced mixed sentiment.\n"
    "Examples: 'VAC is trash' -> negative; 'is VAC working?' -> neutral; "
    "'game is fun but VAC is broken' for VAC -> negative; "
    "'Best game ever, 10/10' after complaints -> negative (sarcasm); "
    "'some good, some bad' -> neutral.\n"
    "Return strict JSON only. Include a short reason and an evidence quote from the context. "
    "No markdown, no code fences, no extra text."
)


def build_single_prompt(job: dict, max_text_chars: int = 0, style: str = "simple") -> str:
    if style == "context":
        return (
            f"{CONTEXT_RULES}\n\n"
            f"Entity: {entity_name(job)}\n"
            f"Aliases: {alias_text(job)}\n"
            f"Review context: {job_text(job, style, max_text_chars)}\n\n"
            'Return exactly: {"label":"positive|negative|neutral","reason":"short reason","evidence":"quote"}'
        )
    if style == "judge":
        return (
            "Two independent annotators disagree about the sentiment toward the specified entity. "
            "Choose the better label using only the review text and judge intended meaning, including sarcasm.\n"
            f"Candidate A: {job.get('candidate_a')}\n"
            f"Candidate B: {job.get('candidate_b')}\n"
            "Return strict JSON only: "
            '{"label":"positive|negative|neutral","reason":"short reason"}. '
            "No markdown, no code fences, no extra text.\n\n"
            f"Entity: {entity_name(job)}\n"
            f"Review: {job_text(job, style, max_text_chars)}"
        )
    overall = (
        "If the entity is mentioned but its local stance is unclear, use the review's overall attitude toward the game.\n"
        if style in {"simple_overall", "simple_overall_strict", "neutral_recheck"} else ""
    )
    strict = (
        "Do not choose neutral when there is a clear complaint or praise about the entity; "
        "use neutral only for factual mentions/questions or genuinely balanced mixed sentiment.\n"
        if style == "simple_overall_strict" else ""
    )
    recheck = (
        "This mention was first judged neutral. Re-check carefully: if there is a clear complaint or praise "
        "about the entity, choose positive/negative; keep neutral only if there is no explicit evaluative stance "
        "or the sentiment is genuinely balanced.\n"
        if style == "neutral_recheck" else ""
    )
    return (
        "Classify the sentiment of this Steam review toward the specified entity. "
        "Use only the review text. Judge the reviewer's intended attitude, including sarcasm/irony.\n"
        "Rules: positive = praise/satisfaction/approval/improvement; "
        "negative = criticism/complaint/disappointment/failure/request to fix something broken; "
        "neutral = factual mention/question/no clear stance, or balanced mixed sentiment.\n"
        f"{overall}{strict}{recheck}"
        "Return strict JSON only, with exactly two keys: "
        '{"label":"positive|negative|neutral","reason":"short reason"}. '
        "No markdown, no code fences, no extra text.\n\n"
        f"Entity: {entity_name(job)}\n"
        f"Review: {job_text(job, style, max_text_chars)}"
    )


def build_batch_prompt(batch: list[dict], max_text_chars: int = 0, style: str = "simple") -> str:
    items = []
    for index, job in enumerate(batch, 1):
        if style == "context":
            items.append(
                f"{index}. Entity: {entity_name(job)}\n"
                f"Aliases: {alias_text(job)}\n"
                f"Context: {job_text(job, style, max_text_chars)}"
            )
        elif style == "judge":
            items.append(
                f"{index}. Entity: {entity_name(job)}\n"
                f"Candidate A: {job.get('candidate_a')}\n"
                f"Candidate B: {job.get('candidate_b')}\n"
                f"Review: {job_text(job, style, max_text_chars)}"
            )
        else:
            items.append(
                f"{index}. Entity: {entity_name(job)}\nReview: "
                f"{job_text(job, style, max_text_chars)}"
            )
    if style == "context":
        return (
            f"{CONTEXT_RULES}\n\nEach numbered item is independent; do not transfer sentiment between items.\n"
            'Return strict JSON: {"results":[{"index":1,"label":"positive|negative|neutral",'
            '"reason":"short","evidence":"quote"}]}. Include every index exactly once.\n\n'
            + "\n\n".join(items)
        )
    if style == "judge":
        return (
            "Two independent annotators disagree for each item. Choose the better label using only the "
            "review text and intended meaning, including sarcasm. Each item is independent.\n"
            "Return strict JSON only:\n"
            '{"results":[{"index":1,"label":"positive|negative|neutral","reason":"short reason"}]}\n'
            "Include every index exactly once. No markdown, no code fences, no extra text.\n\n"
            + "\n\n".join(items)
        )
    overall = (
        "If an entity is mentioned but its local stance is unclear, use the review's overall attitude toward the game.\n"
        if style in {"simple_overall", "simple_overall_strict", "neutral_recheck"} else ""
    )
    strict = (
        "Do not choose neutral when there is a clear complaint or praise about the entity; "
        "use neutral only for factual mentions/questions or genuinely balanced mixed sentiment.\n"
        if style == "simple_overall_strict" else ""
    )
    recheck = (
        "Each item was first judged neutral. Re-check carefully: if there is a clear complaint or praise "
        "about the entity, choose positive/negative; keep neutral only if there is no explicit evaluative "
        "stance or the sentiment is genuinely balanced.\n"
        if style == "neutral_recheck" else ""
    )
    return (
        "Classify the sentiment of each Steam review toward its specified entity. "
        "Use only the review text. Judge intended attitude, including sarcasm/irony.\n"
        "Rules: positive = praise/satisfaction/approval/improvement; "
        "negative = criticism/complaint/disappointment/failure/request to fix something broken; "
        "neutral = factual mention/question/no clear stance, or balanced mixed sentiment.\n"
        f"{overall}{strict}{recheck}"
        "Return strict JSON only:\n"
        '{"results":[{"index":1,"label":"positive|negative|neutral","reason":"short reason"}]}\n'
        "Include every index exactly once. No markdown, no code fences, no extra text.\n\n"
        + "\n\n".join(items)
    )


def extract_json(content: str):
    if not isinstance(content, str):
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except ValueError:
            return None


def normalize_label(value):
    if isinstance(value, str):
        value = value.strip().lower()
    return value if value in LABELS else None


def post_json(url: str, payload: dict, api_key: str, timeout: float) -> tuple[int, dict]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            detail = {"error": f"HTTP {exc.code}"}
        return exc.code, detail
    except (URLError, TimeoutError, OSError) as exc:
        return 0, {"error": type(exc).__name__}


def _content(body: dict):
    try:
        message = body["choices"][0]["message"]
        content = message.get("content")
        if not content and isinstance(message.get("reasoning_content"), str):
            content = message["reasoning_content"]
        return content
    except (KeyError, IndexError, TypeError):
        return None


def _usage_share(usage, batch_size):
    if not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": float(usage.get("prompt_tokens", 0)) / batch_size,
        "completion_tokens": float(usage.get("completion_tokens", 0)) / batch_size,
        "total_tokens": float(usage.get("total_tokens", 0)) / batch_size,
        "batch_size": batch_size,
    }


def _row(job, label, reason, status, error, raw_content=None, evidence=None, usage=None, batch_size=1):
    entity = job.get("entity") or {}
    return {
        "job_id": job.get("job_id"),
        "app_id": job.get("app_id"),
        "review_id": job.get("review_id"),
        "revision": job.get("revision"),
        "entity_id": entity.get("entity_id"),
        "label": label,
        "reason": reason,
        "evidence": evidence,
        "usage": _usage_share(usage, batch_size),
        "raw_content": raw_content,
        "http_status": status,
        "error": error,
    }


def call_single(job, base_url, api_key, model, timeout, retries, max_text_chars=0, style="simple"):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful sentiment annotator. Output strict JSON only."},
            {"role": "user", "content": build_single_prompt(job, max_text_chars, style)},
        ],
        "temperature": 0,
        "max_tokens": 250,
    }
    last_error = None
    for attempt in range(retries + 1):
        status, body = post_json(base_url.rstrip("/") + "/chat/completions", payload, api_key, timeout)
        if status in (401, 403):
            raise RuntimeError(f"LLM authentication/authorization failed with HTTP {status}")
        if status == 429 or status >= 500 or status == 0:
            last_error = f"HTTP {status}"
            if attempt < retries:
                time.sleep(min(30, 2 ** (attempt + 1)))
                continue
            return _row(job, None, None, status, last_error)
        if status != 200:
            return _row(job, None, None, status, f"HTTP {status}: {body}")
        content = _content(body)
        payload_json = extract_json(content)
        if not isinstance(payload_json, dict):
            return _row(job, None, None, status, "unparseable label", content)
        label = normalize_label(payload_json.get("label"))
        reason = payload_json.get("reason")
        evidence = payload_json.get("evidence")
        if label is None:
            return _row(job, None, None, status, "unparseable label", content)
        return _row(job, label, reason if isinstance(reason, str) else None, status, None, content,
                    evidence if isinstance(evidence, str) else None,
                    body.get("usage"), 1)
    return _row(job, None, None, 0, last_error or "request failed")


def call_batch(batch, base_url, api_key, model, timeout, retries, max_text_chars=0, style="simple"):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful sentiment annotator. Output strict JSON only."},
            {"role": "user", "content": build_batch_prompt(batch, max_text_chars, style)},
        ],
        "temperature": 0,
        "max_tokens": min(2400, 150 * len(batch) + 150),
    }
    last_error = None
    for attempt in range(retries + 1):
        status, body = post_json(base_url.rstrip("/") + "/chat/completions", payload, api_key, timeout)
        if status in (401, 403):
            raise RuntimeError(f"LLM authentication/authorization failed with HTTP {status}")
        if status == 429 or status >= 500 or status == 0:
            last_error = f"HTTP {status}"
            if attempt < retries:
                time.sleep(min(30, 2 ** (attempt + 1)))
                continue
            return None
        if status != 200:
            return None
        content = _content(body)
        document = extract_json(content)
        if not isinstance(document, dict) or not isinstance(document.get("results"), list):
            return None
        indexed = {}
        for item in document["results"]:
            if not isinstance(item, dict):
                return None
            index = item.get("index")
            if type(index) is not int or index < 1 or index > len(batch) or index in indexed:
                return None
            label = normalize_label(item.get("label"))
            if label is None:
                return None
            indexed[index] = (label, item.get("reason"), item.get("evidence"), content)
        if set(indexed) != set(range(1, len(batch) + 1)):
            return None
        usage = body.get("usage")
        return [
            _row(job, indexed[index][0], indexed[index][1] if isinstance(indexed[index][1], str) else None,
                 status, None, indexed[index][3],
                 indexed[index][2] if isinstance(indexed[index][2], str) else None,
                 usage, len(batch))
            for index, job in enumerate(batch, 1)
        ]
    return None


def make_batches(jobs, batch_size, max_chars, max_text_chars=0):
    batches = []
    current = []
    chars = 0
    for job in jobs:
        length = len(clip_text(job.get("text_clean") or "", max_text_chars))
        if current and (len(current) >= batch_size or chars + length > max_chars):
            batches.append(current)
            current = []
            chars = 0
        current.append(job)
        chars += length
    if current:
        batches.append(current)
    return batches


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-batch-chars", type=int, default=3000)
    parser.add_argument("--max-text-chars", type=int, default=0)
    parser.add_argument("--prompt-style", choices=("simple", "simple_overall", "simple_overall_strict", "neutral_recheck", "judge", "context"), default="simple")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--base-url", default=os.environ.get("SENTIMENT_LLM_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("SENTIMENT_LLM_MODEL", "CS5351/DeepSeek-V4-Flash"))
    args = parser.parse_args(argv)
    api_key = os.environ.get("SENTIMENT_LLM_API_KEY")
    if not args.base_url or not api_key:
        print("Missing SENTIMENT_LLM_BASE_URL or SENTIMENT_LLM_API_KEY", file=sys.stderr)
        return 2
    jobs = []
    with open(args.input, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                jobs.append(json.loads(line))
    if args.limit:
        jobs = jobs[:args.limit]
    done = set()
    if args.resume:
        try:
            with open(args.output, "r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        row = json.loads(line)
                        if row.get("label") in LABELS:
                            done.add(row.get("job_id"))
        except FileNotFoundError:
            pass
    jobs = [job for job in jobs if job.get("job_id") not in done]
    batches = make_batches(jobs, max(1, args.batch_size), max(1, args.max_batch_chars),
                           max(0, args.max_text_chars))
    mode = "a" if args.resume else "w"
    output = open(args.output, mode, encoding="utf-8", newline="\n")
    lock = threading.Lock()
    stop = threading.Event()
    totals = {"ok": 0, "error": 0, "jobs": 0}

    def process_batch(batch):
        if stop.is_set():
            return []
        if len(batch) == 1:
            return [call_single(batch[0], args.base_url, api_key, args.model, args.timeout,
                                args.retries, max(0, args.max_text_chars), args.prompt_style)]
        rows = call_batch(batch, args.base_url, api_key, args.model, args.timeout,
                          args.retries, max(0, args.max_text_chars), args.prompt_style)
        if rows is not None:
            return rows
        middle = len(batch) // 2
        return process_batch(batch[:middle]) + process_batch(batch[middle:])

    def worker(batch):
        rows = process_batch(batch)
        if args.sleep:
            time.sleep(max(0.0, args.sleep))
        return rows

    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(worker, batch): batch for batch in batches}
            for index, future in enumerate(as_completed(futures), 1):
                rows = future.result()
                if rows is None:
                    continue
                with lock:
                    for row in rows:
                        output.write(json.dumps(row, ensure_ascii=False) + "\n")
                        totals["jobs"] += 1
                        if row.get("label") in LABELS:
                            totals["ok"] += 1
                        else:
                            totals["error"] += 1
                    output.flush()
                    if index % 10 == 0 or index == len(batches):
                        print(f"batches {index}/{len(batches)} jobs={totals['jobs']} "
                              f"ok={totals['ok']} errors={totals['error']}",
                              file=sys.stderr, flush=True)
    except Exception as exc:
        stop.set()
        print(f"stopped: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

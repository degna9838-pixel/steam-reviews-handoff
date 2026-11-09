from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LABELS = {"positive", "negative", "neutral"}


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
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


def parse_label(content: str) -> tuple[str | None, str | None]:
    if not isinstance(content, str):
        return None, None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None, None
        try:
            payload = json.loads(match.group(0))
        except ValueError:
            return None, None
    if not isinstance(payload, dict):
        return None, None
    label = payload.get("label")
    reason = payload.get("reason")
    if isinstance(label, str):
        label = label.strip().lower()
    if label not in LABELS:
        return None, reason if isinstance(reason, str) else None
    return label, reason if isinstance(reason, str) else None


def prompt_for(sample: dict) -> str:
    return (
        "Classify the sentiment of this Steam review toward the specified entity. "
        "Use only the review text. Return strict JSON with exactly two keys: "
        '{"label":"positive|negative|neutral","reason":"short reason"}. '
        "Do not include any other text.\n\n"
        f"Entity: {sample.get('entity_name') or sample.get('entity_id')}\n"
        f"Review: {sample.get('text_clean', '')}"
    )


def one_request(sample: dict, base_url: str, api_key: str, model: str, timeout: float) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a careful sentiment annotator. Output strict JSON only."},
            {"role": "user", "content": prompt_for(sample)},
        ],
        "temperature": 0,
        "max_tokens": 200,
    }
    for attempt in range(4):
        status, body = _post_json(base_url.rstrip("/") + "/chat/completions", payload, api_key, timeout)
        if status in (401, 403):
            raise RuntimeError(f"LLM authentication/authorization failed with HTTP {status}")
        if status == 429 or status >= 500 or status == 0:
            if attempt == 3:
                return {"http_status": status, "error": f"request failed after retries: HTTP {status}"}
            time.sleep(min(30, 2 ** (attempt + 1)))
            continue
        if status != 200:
            return {"http_status": status, "error": f"HTTP {status}: {body}"}
        try:
            message = body["choices"][0]["message"]
            content = message.get("content")
            if not content and isinstance(message.get("reasoning_content"), str):
                content = message["reasoning_content"]
        except (KeyError, IndexError, TypeError):
            return {"http_status": status, "error": "unexpected response shape"}
        label, reason = parse_label(content)
        return {
            "http_status": status,
            "raw_content": content,
            "llm_label": label,
            "llm_reason": reason,
            "error": None if label else "unparseable label",
        }
    raise AssertionError("unreachable")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--base-url", default=os.environ.get("SENTIMENT_LLM_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("SENTIMENT_LLM_MODEL", "CS5351/DeepSeek-V4-Flash"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    api_key = os.environ.get("SENTIMENT_LLM_API_KEY")
    if not args.base_url or not api_key:
        print("Missing SENTIMENT_LLM_BASE_URL or SENTIMENT_LLM_API_KEY", file=sys.stderr)
        return 2
    if args.probe:
        sample = {"entity_name": "test", "text_clean": "This is fine."}
        result = one_request(sample, args.base_url, api_key, args.model, args.timeout)
        print(json.dumps({"http_status": result.get("http_status"), "parsed": result.get("llm_label"),
                          "error": result.get("error")}, ensure_ascii=False))
        return 0
    if not args.input or not args.output:
        parser.error("--input and --output are required unless --probe is used")
    samples = []
    with open(args.input, "r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                samples.append(json.loads(line))
    if args.limit:
        samples = samples[:args.limit]
    errors = 0
    with open(args.output, "w", encoding="utf-8", newline="\n") as stream:
        for index, sample in enumerate(samples, 1):
            result = one_request(sample, args.base_url, api_key, args.model, args.timeout)
            row = {
                "sample_id": sample.get("sample_id"),
                "app_id": sample.get("app_id"),
                "review_id": sample.get("review_id"),
                "entity_id": sample.get("entity_id"),
                "entity_name": sample.get("entity_name"),
                "vader_label": sample.get("vader_label"),
                "vader_score": sample.get("vader_score"),
                "llm_label": result.get("llm_label"),
                "llm_reason": result.get("llm_reason"),
                "raw_content": result.get("raw_content"),
                "http_status": result.get("http_status"),
                "error": result.get("error"),
            }
            if row["llm_label"] is None:
                errors += 1
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            if index % 20 == 0 or index == len(samples):
                print(f"processed {index}/{len(samples)} errors={errors}", file=sys.stderr, flush=True)
            time.sleep(max(0.0, args.sleep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

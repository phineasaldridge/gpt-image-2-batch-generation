#!/usr/bin/env python3
"""Batch-generate images with GPT Image 2 (or GPT Image 2.5 Flare / Sunburst) from a YAML, JSON or CSV file.

Bounded concurrency, dry runs with a cost estimate, skip-existing, backoff on 429/503, and a JSONL
manifest of what was made and what it cost. Standard library only; YAML configs need PyYAML.

    export GPT_IMAGE_API_KEY=...                       # a key for the endpoint below
    export GPT_IMAGE_BASE_URL=https://argolink.io/v1   # the default; any OpenAI-style Images API works
    python batchgen.py batch.yaml --dry-run
    python batchgen.py batch.yaml --concurrency 4
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from math import gcd

DEFAULT_BASE_URL = "https://argolink.io/v1"
MODELS = ("gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst")
QUALITIES = ("auto", "low", "medium", "high")
# USD per returned image by tier; the tier is the returned image's longest edge (checked 2026-10-07).
TIER_PRICES = {"1K": 0.015, "2K": 0.015, "4K": 0.02}
RATIOS = {"1:1": 1, "3:4": 3 / 4, "4:3": 4 / 3, "2:3": 2 / 3, "3:2": 3 / 2, "9:16": 9 / 16, "16:9": 16 / 9,
          "21:9": 21 / 9, "5:2": 5 / 2}
# Only "slow down" answers are retried in-process. Anything else goes to the manifest, and running the
# same command again retries it, because finished entries are skipped.
RETRY_STATUSES = {429, 503}
# CDNs in front of some endpoints refuse the default "Python-urllib" agent (HTTP 403), so name the client.
USER_AGENT = "gpt-image-batchgen/1.0"


class BatchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def tier(longest_edge: int) -> str:
    return "1K" if longest_edge <= 1024 else "2K" if longest_edge <= 2048 else "4K"


def ratio_of(size: str) -> str:
    """Closest named aspect ratio for a WIDTHxHEIGHT size, e.g. 2048x1152 -> 16:9."""
    width, height = (int(v) for v in size.lower().split("x"))
    return min(RATIOS, key=lambda name: abs(RATIOS[name] - width / height))


def image_size(data: bytes) -> tuple:
    """(width, height, extension) of a PNG, JPEG or WebP, read from its header."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return width, height, "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little"), "webp"
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, "webp"
        if chunk == b"VP8 ":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF, "webp"
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return width, height, "jpg"
            index += 2 + struct.unpack(">H", data[index + 2:index + 4])[0]
    raise BatchError("unrecognised image format")


@dataclass
class Job:
    name: str
    prompt: str
    size: str
    quality: str
    count: int
    model: str
    files: list = field(default_factory=list)

    @property
    def planned_tier(self) -> str:
        return tier(max(int(v) for v in self.size.lower().split("x")))

    @property
    def planned_cost(self) -> float:
        return TIER_PRICES[self.planned_tier] * self.count


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            raise SystemExit("YAML configs need PyYAML: pip install pyyaml (or use .json / .csv)")
        return yaml.safe_load(text)
    if path.endswith(".json"):
        return json.loads(text)
    if path.endswith(".csv"):
        rows = list(csv.DictReader(text.splitlines()))
        return {"images": {row["name"]: {k: v for k, v in row.items() if k != "name" and v} for row in rows}}
    raise SystemExit("config must be .yaml, .yml, .json or .csv")


def compose_prompt(config: dict, prompt: str, size: str) -> str:
    """style + entry prompt + avoid list + the aspect ratio in words (this route reads the ratio from the prompt)."""
    parts = [config.get("style", "").strip(), prompt.strip()]
    if config.get("avoid"):
        parts.append(f"Avoid: {config['avoid'].strip()}")
    if not any(name in prompt for name in RATIOS):
        parts.append(f"{ratio_of(size)} composition.")
    return "\n\n".join(part for part in parts if part)


def plan(config: dict, only: set | None = None) -> list:
    model = config.get("model", "gpt-image-2")
    if model not in MODELS:
        raise SystemExit(f"model must be one of {', '.join(MODELS)}")
    jobs = []
    for name, entry in (config.get("images") or {}).items():
        entry = {"prompt": entry} if isinstance(entry, str) else dict(entry)
        if only and name not in only:
            continue
        size = str(entry.get("size", config.get("size", "1024x1024")))
        quality = str(entry.get("quality", config.get("quality", "auto")))
        count = int(entry.get("count", config.get("count", 1)))
        if quality not in QUALITIES:
            raise SystemExit(f"{name}: quality must be one of {', '.join(QUALITIES)}")
        if not 1 <= count <= 7:
            raise SystemExit(f"{name}: count must be 1-7 (one request returns at most 7 images)")
        try:
            ratio_of(size)
        except ValueError:
            raise SystemExit(f"{name}: size must be WIDTHxHEIGHT, e.g. 2048x1152") from None
        jobs.append(Job(name, compose_prompt(config, entry["prompt"], size), size, quality, count, model))
    if not jobs:
        raise SystemExit("nothing to generate: the config has no matching images")
    return jobs


class Client:
    def __init__(self, api_key: str, base_url: str, timeout: float = 600, retries: int = 4):
        self.api_key, self.base_url, self.timeout, self.retries = api_key, base_url.rstrip("/"), timeout, retries

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(self.base_url + "/images/generations", data=json.dumps(body).encode(), method="POST")
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            retry_after = error.headers.get("Retry-After") if error.headers else None
            text = error.read().decode(errors="replace")[:300]
            raise BatchError(f"HTTP {error.code}: {text}", error.code,
                             float(retry_after) if retry_after and retry_after.isdigit() else None) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise BatchError(f"network error: {error}", None) from None

    def generate(self, job: Job) -> list:
        body = {"model": job.model, "prompt": job.prompt, "size": job.size, "quality": job.quality,
                "n": job.count, "response_format": "b64_json"}
        for attempt in range(self.retries + 1):
            try:
                payload = self._post(body)
                return [base64.b64decode(item["b64_json"]) for item in payload.get("data", [])]
            except BatchError as error:
                if error.status not in RETRY_STATUSES or attempt == self.retries:
                    raise
                time.sleep(error.retry_after or min(60, 2 ** attempt * 2) + random.uniform(0, 1))
        return []


def output_paths(job: Job, output: str, extension: str = "png") -> list:
    if job.count == 1:
        return [os.path.join(output, f"{job.name}.{extension}")]
    return [os.path.join(output, f"{job.name}_{i:03d}.{extension}") for i in range(1, job.count + 1)]


def already_done(job: Job, output: str) -> bool:
    return all(any(os.path.exists(p[: -len("png")] + ext) for ext in ("png", "jpg", "webp"))
               for p in output_paths(job, output))


def run_job(client: Client, job: Job, output: str) -> dict:
    started = time.monotonic()
    record = {"name": job.name, "model": job.model, "size": job.size, "quality": job.quality, "requested": job.count}
    try:
        images = client.generate(job)
    except BatchError as error:
        return {**record, "error": str(error), "seconds": round(time.monotonic() - started, 1)}
    files, cost = [], 0.0
    for index, data in enumerate(images):
        width, height, extension = image_size(data)
        path = output_paths(job, output, extension)[min(index, job.count - 1)]
        with open(path, "wb") as handle:
            handle.write(data)
        files.append({"path": path, "pixels": f"{width}x{height}", "tier": tier(max(width, height))})
        cost += TIER_PRICES[tier(max(width, height))]
    return {**record, "files": files, "cost": round(cost, 4), "seconds": round(time.monotonic() - started, 1)}


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch image generation for GPT Image 2 and 2.5.")
    parser.add_argument("config", help="batch.yaml, batch.json or batch.csv")
    parser.add_argument("--output", help="output folder (default: config 'output' or ./out)")
    parser.add_argument("--only", help="comma-separated entry names to run")
    parser.add_argument("--concurrency", type=int, help="parallel requests (default: config or 2)")
    parser.add_argument("--force", action="store_true", help="regenerate entries whose files exist")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and the estimate, send nothing")
    parser.add_argument("--max-cost", type=float, help="refuse to start if the estimate is above this many USD")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    output = args.output or config.get("output", "out")
    jobs = plan(config, set(args.only.split(",")) if args.only else None)
    todo = [job for job in jobs if args.force or not already_done(job, output)]
    estimate = sum(job.planned_cost for job in todo)

    for job in jobs:
        state = "run " if job in todo else "skip"
        print(f"{state} {job.name:<24} {job.size:>10} {job.planned_tier} x{job.count}  ${job.planned_cost:.3f}")
    print(f"{len(todo)} request(s), {sum(j.count for j in todo)} image(s), estimate ${estimate:.2f}")
    if args.dry_run or not todo:
        return 0
    if args.max_cost is not None and estimate > args.max_cost:
        print(f"estimate ${estimate:.2f} is above --max-cost ${args.max_cost:.2f}; nothing sent", file=sys.stderr)
        return 2

    api_key = os.environ.get("GPT_IMAGE_API_KEY")
    if not api_key:
        raise SystemExit("set GPT_IMAGE_API_KEY (and GPT_IMAGE_BASE_URL for an endpoint other than the default)")
    client = Client(api_key, os.environ.get("GPT_IMAGE_BASE_URL") or DEFAULT_BASE_URL)
    os.makedirs(output, exist_ok=True)
    concurrency = max(1, args.concurrency or int(config.get("concurrency", 2)))
    failures, spent = 0, 0.0
    with ThreadPoolExecutor(max_workers=concurrency) as pool, \
            open(os.path.join(output, "manifest.jsonl"), "a", encoding="utf-8") as manifest:
        futures = [pool.submit(run_job, client, job, output) for job in todo]
        for future in as_completed(futures):
            record = future.result()
            manifest.write(json.dumps(record) + "\n")
            manifest.flush()
            if "error" in record:
                failures += 1
                print(f"FAIL {record['name']}: {record['error']}", file=sys.stderr)
            else:
                spent += record["cost"]
                print(f"done {record['name']} ({len(record['files'])} file(s), ${record['cost']:.3f}, {record['seconds']} s)")
    print(f"finished: {len(todo) - failures} ok, {failures} failed, about ${spent:.2f} billed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

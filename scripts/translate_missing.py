#!/usr/bin/env python3
"""Resumable translation of new public Claude UI message IDs using local Ollama."""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

cli = argparse.ArgumentParser(description="Translate public UI messages using a local Ollama model")
cli.add_argument("--directory", type=Path, required=True, help="Directory containing missing.json and optional memory.json")
cli.add_argument("--model", default="qwen3.8:27b-mlx")
args = cli.parse_args()
ROOT = args.directory.resolve()
MISSING = json.loads((ROOT / "missing.json").read_text())
MEMORY = json.loads((ROOT / "memory.json").read_text()) if (ROOT / "memory.json").exists() else {}
OUTPUT = ROOT / "translated.json"
MODEL = args.model


def write_progress(data: dict[str, str]) -> None:
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(OUTPUT)


def args_and_tags(value: str) -> tuple[list[str], list[str]]:
    args = sorted(re.findall(r"\{([\w.$-]+)(?:\s*[,}])", value))
    tags = sorted(re.findall(r"</?([A-Za-z][\w.-]*)\b", value))
    return args, tags


def parse_response(raw: str, batch: dict[str, str]) -> dict[str, str]:
    first = raw.find("{")
    if first < 0:
        raise ValueError("no JSON object")
    parsed, _ = json.JSONDecoder().raw_decode(raw[first:])
    if not isinstance(parsed, dict) or set(parsed) != set(batch):
        raise ValueError(f"keys mismatch: got {len(parsed) if isinstance(parsed, dict) else 'nonobject'} expected {len(batch)}")
    for key, zh in parsed.items():
        if not isinstance(zh, str) or not zh.strip():
            raise ValueError(f"empty or non-string translation: {key}")
    return parsed


def request(batch: dict[str, str]) -> dict[str, str]:
    instructions = (
        "Translate every English desktop-app UI value to concise, natural Simplified Chinese. "
        "Return ONLY a JSON object with the exact same keys. Preserve every {variable}, "
        "ICU plural/select syntax and category keys, HTML tags including attributes, URLs, "
        "keyboard shortcuts, and product/brand names exactly. Translate text inside ICU branches. "
        "Never omit an entry. Values are public UI strings, not user data.\n"
    )
    prompt = instructions + json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
    payload = {"model": MODEL, "stream": False, "think": False,
               "options": {"temperature": 0.05, "num_ctx": 8192, "num_predict": 5500},
               "prompt": prompt}
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=260) as response:
        result = json.load(response)
    return parse_response(result.get("response", ""), batch)


def chunks(data: dict[str, str]):
    chunk: dict[str, str] = {}
    chars = 0
    for key, value in data.items():
        if chunk and (len(chunk) >= 45 or chars + len(value) > 2300):
            yield chunk
            chunk, chars = {}, 0
        chunk[key] = value
        chars += len(value)
    if chunk:
        yield chunk


def translate(batch: dict[str, str], output: dict[str, str]) -> None:
    for attempt in range(2):
        try:
            result = request(batch)
            output.update(result)
            write_progress(output)
            return
        except Exception as error:
            print(f"retry {attempt+1} for {len(batch)}: {type(error).__name__}: {error}", flush=True)
            time.sleep(1)
    if len(batch) == 1:
        print(f"UNRESOLVED {next(iter(batch))}", flush=True)
        return
    half = len(batch) // 2
    entries = list(batch.items())
    translate(dict(entries[:half]), output)
    translate(dict(entries[half:]), output)


def main() -> None:
    output = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
    to_do = {k: v for k, v in MISSING.items() if k not in MEMORY and k not in output}
    total = len(to_do)
    start = time.monotonic()
    print(f"starting {total} strings; memory={len(MEMORY)}, generated={len(output)}", flush=True)
    for i, batch in enumerate(chunks(to_do), 1):
        translate(batch, output)
        print(f"batch {i}: translated={len(output)}, remaining={sum(k not in MEMORY and k not in output for k in MISSING)}, elapsed={time.monotonic()-start:.0f}s", flush=True)
    print(f"finished unresolved={sum(k not in MEMORY and k not in output for k in MISSING)}", flush=True)


if __name__ == "__main__":
    main()

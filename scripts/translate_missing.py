#!/usr/bin/env python3
"""Resumable translation of new public Claude UI message IDs using local Ollama."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
SCRIPT_ROOT = Path(__file__).resolve().parents[1]


def write_progress(data: dict[str, str]) -> None:
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(OUTPUT)


def args_and_tags(value: str) -> tuple[list[str], list[str]]:
    args = sorted(re.findall(r"\{([\w.$-]+)(?:\s*[,}])", value))
    tags = sorted(re.findall(r"</?[A-Za-z][^>]*>", value))
    return args, tags


def valid_icu_structure(original: str, translated: str) -> bool:
    environment = os.environ.copy()
    for modules in (SCRIPT_ROOT / "node_modules", SCRIPT_ROOT.parent.parent / "node_modules"):
        if modules.is_dir():
            environment["NODE_PATH"] = str(modules)
            break
    try:
        result = subprocess.run(
            ["node", str(SCRIPT_ROOT / "scripts/icu_literals.cjs")],
            input=json.dumps({"operation": "validate", "source": original,
                              "translated": translated}),
            capture_output=True, text=True, timeout=8, env=environment,
        )
        return result.returncode == 0 and json.loads(result.stdout).get("valid") is True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


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
        original = batch[key]
        if re.search(r"\{[\w.$-]+\s*,\s*(?:plural|select|selectordinal)\s*,", original):
            valid = valid_icu_structure(original, zh)
            if args_and_tags(zh)[1] != args_and_tags(original)[1]:
                valid = False
        else:
            valid = args_and_tags(zh) == args_and_tags(original)
        if not valid:
            raise ValueError(f"placeholders changed in translation: {key}")
    return parsed


def partition_response(raw: str, batch: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    first = raw.find("{")
    if first < 0:
        raise ValueError("no JSON object")
    parsed, _ = json.JSONDecoder().raw_decode(raw[first:])
    if not isinstance(parsed, dict) or set(parsed) != set(batch):
        raise ValueError("keys mismatch")
    valid, rejected = {}, {}
    for key, original in batch.items():
        try:
            parse_response(json.dumps({key: parsed[key]}, ensure_ascii=False), {key: original})
        except ValueError:
            rejected[key] = original
        else:
            valid[key] = parsed[key]
    return valid, rejected


def request(batch: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
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
    return partition_response(result.get("response", ""), batch)


def chunks(data: dict[str, str]):
    chunk: dict[str, str] = {}
    chars = 0
    complex_chunk = False
    for key, value in data.items():
        complex_entry = bool(re.search(r"\{[\w.$-]+\s*,\s*(?:plural|select|selectordinal)\s*,", value))
        if chunk and (complex_entry != complex_chunk or
                      len(chunk) >= (8 if complex_chunk else 45) or
                      chars + len(value) > 2300):
            yield chunk
            chunk, chars = {}, 0
        complex_chunk = complex_entry
        chunk[key] = value
        chars += len(value)
    if chunk:
        yield chunk


def ordered_remaining(missing: dict[str, str], memory: dict[str, str],
                      output: dict[str, str]) -> dict[str, str]:
    pending = ((key, value) for key, value in missing.items()
               if key not in memory and key not in output)

    def rank(item: tuple[str, str]) -> tuple[int, int, str]:
        args, tags = args_and_tags(item[1])
        return (2 if args else 1 if tags else 0, len(item[1]), item[0])

    return dict(sorted(pending, key=rank))


def translate(batch: dict[str, str], output: dict[str, str]) -> None:
    for attempt in range(2):
        try:
            valid, rejected = request(batch)
            if not valid:
                raise ValueError("all translations changed placeholders")
        except Exception as error:
            print(f"retry {attempt+1} for {len(batch)}: {type(error).__name__}: {error}", flush=True)
            time.sleep(1)
        else:
            output.update(valid)
            write_progress(output)
            if rejected:
                translate(rejected, output)
            return
    if len(batch) == 1:
        print(f"UNRESOLVED {next(iter(batch))}", flush=True)
        return
    half = len(batch) // 2
    entries = list(batch.items())
    translate(dict(entries[:half]), output)
    translate(dict(entries[half:]), output)


def main() -> None:
    output = json.loads(OUTPUT.read_text()) if OUTPUT.exists() else {}
    to_do = ordered_remaining(MISSING, MEMORY, output)
    total = len(to_do)
    start = time.monotonic()
    print(f"starting {total} strings; memory={len(MEMORY)}, generated={len(output)}", flush=True)
    for i, batch in enumerate(chunks(to_do), 1):
        translate(batch, output)
        print(f"batch {i}: translated={len(output)}, remaining={sum(k not in MEMORY and k not in output for k in MISSING)}, elapsed={time.monotonic()-start:.0f}s", flush=True)
    print(f"finished unresolved={sum(k not in MEMORY and k not in output for k in MISSING)}", flush=True)


if __name__ == "__main__":
    main()

"""Position-preserving inventory and translation of static HTML UI text."""
from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ATTRIBUTES = {"title", "placeholder", "aria-label", "aria-description", "alt"}


class UIScanner(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.hidden = None
        self.executable = False
        self.findings: list[dict] = []
        self.scripts: list[str] = []
        self.script_offsets: list[int] = []

    def position_offset(self) -> int:
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden = tag
        if tag == "script":
            kind = dict(attrs).get("type", "").lower()
            self.executable = kind in {"", "module", "text/javascript", "application/javascript"}
        raw = self.get_starttag_text()
        for match in re.finditer(r"""([\w:-]+)\s*=\s*(["'])(.*?)\2""", raw, re.S):
            key, _, value = match.groups()
            if key in ATTRIBUTES:
                self.add(self.position_offset() + match.start(3), value, f"html:{key}")

    def handle_endtag(self, tag):
        if self.hidden == tag:
            self.hidden = None

    def handle_data(self, data):
        if self.hidden == "script" and self.executable:
            self.scripts.append(data)
            self.script_offsets.append(self.position_offset())
        if self.hidden:
            return
        trimmed = data.strip()
        if trimmed:
            self.add(self.position_offset() + data.index(trimmed), trimmed, "html:text")

    def add(self, start: int, raw: str, context: str):
        text = html.unescape(raw)
        if re.search(r"[A-Za-z]{2}", text) and not re.search(r"[\u3400-\u9fff]", text):
            self.findings.append({
                "start": start, "end": start + len(raw), "context": context, "text": text,
            })


def scan_html(source: str) -> UIScanner:
    scanner = UIScanner(source)
    scanner.feed(source)
    scanner.close()
    return scanner


def apply_html(source: str, rules: list[dict], dictionary: dict[str, str],
               inline_scripts: dict | None = None) -> str:
    scanner = scan_html(source)
    findings = scanner.findings
    edits = []
    for rule in rules:
        matches = [item for item in findings
                   if item["context"] == rule["context"] and item["text"] == rule["source"]]
        if len(matches) != rule["expected"]:
            raise ValueError(f"HTML UI source changed: {rule['source']}")
        target = dictionary[rule["source"]]
        if not isinstance(target, str) or not target or target == rule["source"]:
            raise ValueError(f"Missing reviewed HTML translation: {rule['source']}")
        edits.extend((item["start"], item["end"], html.escape(target, quote=True))
                     for item in matches)
    if inline_scripts:
        payload = []
        for index, spec in inline_scripts.items():
            number = int(index)
            if number >= len(scanner.scripts) or number < 0:
                raise ValueError(f"HTML inline script missing: {index}")
            payload.append({"file": f"inline-{index}", "source": scanner.scripts[number], "spec": spec})
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("ui_localization.cjs")), "--patch-inline"],
            input=json.dumps(payload), text=True, capture_output=True, check=True,
        )
        for item, patched in zip(payload, json.loads(result.stdout), strict=True):
            number = int(item["file"].removeprefix("inline-"))
            start = scanner.script_offsets[number]
            edits.append((start, start + len(item["source"]), patched))
    boundary = len(source)
    for start, end, target in sorted(edits, reverse=True):
        if end > boundary:
            raise ValueError("Overlapping HTML UI replacements")
        source = source[:start] + target + source[end:]
        boundary = start
    return source


def patch_html_bundle(root: Path, bundle: str, resources: Path) -> None:
    manifest = json.loads((resources / "ui-patch-manifest.json").read_text())
    dictionary = json.loads((resources / "ui-reviewed-zh-CN.json").read_text())
    prepared = []
    for name, spec in manifest["files"].items():
        if not name.startswith(bundle + "/") or not name.endswith(".html"):
            continue
        relative = Path(name[len(bundle) + 1:])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe HTML UI path: {name}")
        path = root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError(f"HTML UI symlink refused: {name}")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest == spec["patched_sha256"]:
            continue
        if digest != spec["sha256"]:
            raise ValueError(f"Unknown HTML UI fingerprint: {name}")
        patched = apply_html(raw.decode("utf-8"), spec["rules"], dictionary,
                             spec.get("inline_scripts")).encode("utf-8")
        if hashlib.sha256(patched).hexdigest() != spec["patched_sha256"]:
            raise ValueError(f"HTML UI dictionary or manifest changed: {name}")
        prepared.append((path, patched))
    for path, content in prepared:
        path.write_bytes(content)

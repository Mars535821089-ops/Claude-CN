#!/usr/bin/env python3
"""Read-only AST inventory of English UI literals in both Claude ASAR bundles."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import repair_macos_429 as repair
from ui_html import apply_html, scan_html


def bundle_entries(app: Path):
    for relative in (repair.APP_ASAR_REL, repair.patch.FRONTEND_ASAR_REL):
        archive = app / relative
        data = archive.read_bytes()
        size, _, header = repair.patch.read_asar_header(data, archive)
        for name, entry in repair.patch.walk_asar_entries(header):
            if not name.endswith((".js", ".cjs", ".mjs", ".html")):
                continue
            if entry.get("unpacked"):
                raw = Path(str(archive) + ".unpacked").joinpath(name).read_bytes()
            else:
                raw = repair.patch.read_asar_entry(data, size, header, name)
            if raw is None:
                raise ValueError(f"Cannot read JavaScript entry: {archive.name}/{name}")
            yield {"file": f"{archive.name}/{name}", "source": raw.decode("utf-8")}


def sources(app: Path):
    for item in bundle_entries(app):
        if item["file"].endswith(".html"):
            for index, source in enumerate(scan_html(item["source"]).scripts):
                yield {"file": f"{item['file']}#inline-{index}", "source": source}
        else:
            yield item


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--app", type=Path, default=Path("/Applications/Claude.app"))
    cli.add_argument("--output", type=Path, required=True)
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--generate-manifest", action="store_true",
                      help="Generate version-pinned rules from the reviewed dictionary")
    mode.add_argument("--verify-manifest", action="store_true",
                      help="Verify actual bundle translations in memory without modifying the app")
    args = cli.parse_args()
    if (args.generate_manifest or args.verify_manifest) and repair.patch.bundle_version(args.app) != repair.VERSION:
        raise SystemExit(f"Reviewed UI rules support Claude {repair.VERSION} only")
    # A temporary stream avoids holding all application JavaScript in memory at once.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as payload:
        for item in sources(args.app):
            payload.write(json.dumps(item, ensure_ascii=False) + "\n")
        payload.seek(0)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
            command = ["node", str(repair.ROOT / "scripts/audit_ui.cjs")]
            if args.generate_manifest or args.verify_manifest:
                command = ["node", str(repair.ROOT / "scripts/ui_localization.cjs"),
                           "--generate-manifest" if args.generate_manifest else "--verify-manifest"]
            result = subprocess.run(
                command,
                stdin=payload, stdout=output, check=False,
            )
            output.seek(0)
            if result.returncode:
                raise SystemExit(f"UI audit failed; output was not replaced:\n{output.read()}")
            report = json.load(output)
    html_findings = []
    html_files = 0
    html_replacements = 0
    verified_html = set()
    dictionary = json.loads((repair.RESOURCES / "ui-reviewed-zh-CN.json").read_text())
    manifest = (json.loads((repair.RESOURCES / "ui-patch-manifest.json").read_text())
                if args.verify_manifest else None)
    for item in bundle_entries(args.app):
        if not item["file"].endswith(".html"):
            continue
        html_files += 1
        findings = scan_html(item["source"]).findings
        html_findings.extend(dict(finding, file=item["file"]) for finding in findings)
        if args.verify_manifest and item["file"] in manifest["files"]:
            spec = manifest["files"][item["file"]]
            if hashlib.sha256(item["source"].encode()).hexdigest() != spec["sha256"]:
                raise ValueError(f"Unknown HTML UI fingerprint: {item['file']}")
            patched = apply_html(item["source"], spec["rules"], dictionary, spec.get("inline_scripts"))
            if hashlib.sha256(patched.encode()).hexdigest() != spec["patched_sha256"]:
                raise ValueError(f"HTML UI output differs: {item['file']}")
            html_replacements += sum(rule["expected"] for rule in spec["rules"])
            html_replacements += sum(rule["expected"] for inline in spec.get("inline_scripts", {}).values()
                                     for rule in inline["rules"])
            verified_html.add(item["file"])
        if args.generate_manifest:
            prefix = item["file"] + "#inline-"
            inline_scripts = {
                file.removeprefix(prefix): report["files"].pop(file)
                for file in list(report["files"]) if file.startswith(prefix)
            }
            groups = {}
            for finding in findings:
                if finding["text"] not in dictionary:
                    continue
                key = (finding["context"], finding["text"])
                rule = groups.setdefault(key, dict(context=key[0], source=key[1], expected=0))
                rule["expected"] += 1
            if groups or inline_scripts:
                rules = list(groups.values())
                patched = apply_html(item["source"], rules, dictionary, inline_scripts)
                report["files"][item["file"]] = {
                    "sha256": hashlib.sha256(item["source"].encode()).hexdigest(),
                    "patched_sha256": hashlib.sha256(patched.encode()).hexdigest(),
                    "rules": rules,
                    **({"inline_scripts": inline_scripts} if inline_scripts else {}),
                }
    if args.generate_manifest:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"Generated {len(report['files'])} reviewed file fingerprints: {args.output}")
        return
    if args.verify_manifest:
        required_html = {file for file in manifest["files"] if file.endswith(".html")}
        if verified_html != required_html:
            raise ValueError(f"Manifest HTML sources missing: {sorted(required_html - verified_html)}")
        report["html_files"] = len(verified_html)
        report["html_replacements"] = html_replacements
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report))
        return
    report["html_files"] = html_files
    report["html_findings"] = html_findings
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Parsed {report['files']} JavaScript files; "
          f"{html_files} HTML files; {len(report['findings'])} English JS UI candidates; "
          f"{len(html_findings)} HTML UI candidates; report: {args.output}")


if __name__ == "__main__":
    main()

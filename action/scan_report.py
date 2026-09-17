#!/usr/bin/env python3
"""Samler scannernes raa output til én kort rapport til ManiLens.

  scan_report.py --raw <mappe> --diff <pr.patch> --out tools.md [--logs <mappe>]

Kun fund i PR'ens aendrede linjer (±CONTEXT) kommer med; afhaengigheds-
saarbarheder kun hvis de er nye i forhold til base. Hver scanner har sin egen
parser; en scanner der mangler eller fejler, springes over med en note.
"""
import argparse
import glob
import json
import os
import re

CONTEXT = 3
MAX_FINDINGS = 150
MAX_LOG_LINES = 60
MAX_COUNT_ROWS = 100  # samme loft som brokerens scanner_counts


def changed_lines(diff):
    """{sti: set(linjenumre i HEAD)} for tilfoejede linjer."""
    result, path, line = {}, None, 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else None
            if path:
                result.setdefault(path, set())
        elif row.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", row)
            line = int(m.group(1)) if m else 0
        elif path is None:
            continue
        elif row.startswith("+"):
            result[path].add(line)
            line += 1
        elif not row.startswith("-") and not row.startswith("\\"):
            line += 1
    return result


def near_change(finding, changed):
    lines = changed.get(finding["path"])
    if lines is None:
        return False
    if not finding.get("line"):
        return True  # fil-niveau-fund i en aendret fil
    return any(abs(finding["line"] - l) <= CONTEXT for l in lines)


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


REPO_ROOT = os.getcwd()


def _rel(path):
    """Sti relativt til repoets rod, uanset om scanneren skrev absolut eller relativ sti."""
    path = (path or "").replace("file://", "")
    root = os.path.realpath(REPO_ROOT) + "/"
    real = os.path.realpath(path) if os.path.isabs(path) else path
    for prefix in (root, REPO_ROOT.rstrip("/") + "/", "./"):
        if real.startswith(prefix):
            return real[len(prefix):]
    return real


def parse_semgrep(data):
    for r in (data or {}).get("results", []):
        sev = r.get("extra", {}).get("severity", "INFO")
        yield {"tool": "semgrep", "rule": r.get("check_id", ""), "severity": sev.lower(),
               "path": _rel(r.get("path")), "line": r.get("start", {}).get("line"),
               "message": r.get("extra", {}).get("message", "")}


def parse_ruff(data):
    for r in data or []:
        yield {"tool": "ruff", "rule": r.get("code") or "", "severity": "warning",
               "path": _rel(r.get("filename")), "line": (r.get("location") or {}).get("row"),
               "message": r.get("message", "")}


def parse_shellcheck(data):
    for r in (data or {}).get("comments", []) if isinstance(data, dict) else data or []:
        yield {"tool": "shellcheck", "rule": f"SC{r.get('code')}", "severity": r.get("level", "warning"),
               "path": _rel(r.get("file")), "line": r.get("line"), "message": r.get("message", "")}


def parse_actionlint(data):
    for r in data or []:
        yield {"tool": "actionlint", "rule": r.get("kind", ""), "severity": "error",
               "path": _rel(r.get("filepath")), "line": r.get("line"), "message": r.get("message", "")}


def parse_hadolint(data):
    for r in data or []:
        yield {"tool": "hadolint", "rule": r.get("code", ""), "severity": r.get("level", "warning"),
               "path": _rel(r.get("file")), "line": r.get("line"), "message": r.get("message", "")}


def parse_phpstan(data):
    for path, info in ((data or {}).get("files") or {}).items():
        for m in info.get("messages", []):
            yield {"tool": "phpstan", "rule": m.get("identifier", ""), "severity": "error",
                   "path": _rel(path), "line": m.get("line"), "message": m.get("message", "")}


def parse_squawk(data):
    for r in data or []:
        yield {"tool": "squawk", "rule": r.get("rule_name", ""), "severity": r.get("level", "warning").lower(),
               "path": _rel(r.get("file")), "line": r["line"] + 1 if isinstance(r.get("line"), int) else None,
               "message": " ".join(filter(None, [r.get("message"), r.get("help")]))}


def parse_trivy(data):
    for res in (data or {}).get("Results", []) or []:
        for m in res.get("Misconfigurations", []) or []:
            yield {"tool": "trivy", "rule": m.get("ID", ""), "severity": m.get("Severity", "").lower(),
                   "path": _rel(res.get("Target")),
                   "line": (m.get("CauseMetadata") or {}).get("StartLine"),
                   "message": f"{m.get('Title', '')}: {m.get('Message', '')}"}


def parse_htmlvalidate(data):
    for f in data or []:
        for m in f.get("messages", []):
            yield {"tool": "html-validate", "rule": m.get("ruleId", ""),
                   "severity": "error" if m.get("severity") == 2 else "warning",
                   "path": _rel(f.get("filePath")), "line": m.get("line"), "message": m.get("message", "")}


def parse_stylelint(data):
    for f in data or []:
        for w in f.get("warnings", []):
            yield {"tool": "stylelint", "rule": w.get("rule", ""), "severity": w.get("severity", "warning"),
                   "path": _rel(f.get("source")), "line": w.get("line"), "message": w.get("text", "")}


def parse_denolint(data):
    for d in (data or {}).get("diagnostics", []):
        yield {"tool": "deno-lint", "rule": d.get("code", ""), "severity": "warning",
               "path": _rel(d.get("filename", "")),
               "line": (d.get("range") or {}).get("start", {}).get("line"),
               "message": d.get("message", "")}


def parse_tsc(text, root):
    for m in re.finditer(r"^(.+?)\((\d+),\d+\): error (TS\d+): (.+)$", text or "", re.M):
        yield {"tool": "tsc", "rule": m.group(3), "severity": "error",
               "path": _rel(m.group(1)), "line": int(m.group(2)), "message": m.group(4)}


def osv_ids(data):
    """{(pakke, version, id): (kilde, resume)} fra osv-scanner JSON."""
    found = {}
    for res in (data or {}).get("results", []) or []:
        source = _rel(res.get("source", {}).get("path"))
        for pkg in res.get("packages", []) or []:
            p = pkg.get("package", {})
            for v in pkg.get("vulnerabilities", []) or []:
                found[(p.get("name"), p.get("version"), v.get("id"))] = (source, v.get("summary", ""))
    return found


PARSERS = {"semgrep": parse_semgrep, "ruff": parse_ruff, "shellcheck": parse_shellcheck,
           "actionlint": parse_actionlint, "hadolint": parse_hadolint, "phpstan": parse_phpstan,
           "squawk": parse_squawk, "trivy": parse_trivy, "htmlvalidate": parse_htmlvalidate,
           "stylelint": parse_stylelint, "denolint": parse_denolint}
PARSER_TYPES = {"semgrep": dict, "ruff": list, "shellcheck": (dict, list),
                "actionlint": list, "hadolint": list, "phpstan": dict,
                "squawk": list, "trivy": dict, "htmlvalidate": list,
                "stylelint": list, "denolint": dict}


def blocking_errors(errors):
    """Scanner errors that make coverage incomplete.

    Semgrep marks recoverable problems, such as a bash snippet in YAML it cannot
    parse, as level "warn"; the file is still scanned. Anything else counts.
    """
    if not errors:
        return []
    if not isinstance(errors, list):
        return [errors]
    return [e for e in errors if not (isinstance(e, dict) and e.get("level") == "warn")]


def collect(raw_dir, changed):
    findings, notes = [], []
    for tool, parser in PARSERS.items():
        files = sorted(glob.glob(os.path.join(raw_dir, f"{tool}*.json")))
        if not files:
            continue
        for f in files:
            data = _load(f)
            if data is None:
                notes.append(f"{tool}: output kunne ikke læses ({os.path.basename(f)})")
                continue
            if not isinstance(data, PARSER_TYPES[tool]):
                notes.append(f"{tool}: ugyldigt outputformat ({os.path.basename(f)})")
                continue
            if isinstance(data, dict) and blocking_errors(data.get("errors")):
                notes.append(f"{tool}: scanneren rapporterede fejl; dækningen er ufuldstændig")
            try:
                findings += [x for x in parser(data) if near_change(x, changed)]
            except (TypeError, ValueError, KeyError, AttributeError):
                notes.append(f"{tool}: fund kunne ikke fortolkes ({os.path.basename(f)})")
    tsc = os.path.join(raw_dir, "tsc.txt")
    if os.path.exists(tsc):
        with open(tsc, errors="replace") as fh:
            findings += [x for x in parse_tsc(fh.read(), raw_dir) if near_change(x, changed)]
    head, base = _load(os.path.join(raw_dir, "osv-head.json")), _load(os.path.join(raw_dir, "osv-base.json"))
    if head is not None and base is None:
        notes.append("osv-scanner: baseresultat mangler; nye sårbarheder kan ikke afgøres")
    elif head is not None:
        new = {k: v for k, v in osv_ids(head).items() if k not in osv_ids(base or {})}
        for (name, version, vid), (source, summary) in sorted(new.items()):
            findings.append({"tool": "osv-scanner", "rule": vid, "severity": "error", "path": source,
                             "line": None, "message": f"{name}@{version} har kendt sårbarhed: {summary}"})
    return dedupe(findings), notes


def check_status(logs_dir, notes, head):
    failed = []
    for path in sorted(glob.glob(os.path.join(logs_dir, "*.log"))) if logs_dir else []:
        with open(path, errors="replace") as fh:
            first = fh.readline().strip()
        if first != "OK":
            failed.append(os.path.basename(path)[:-4])
    return {"head_sha": head, "complete": not notes, "failed_checks": failed, "notes": notes}


def dedupe(findings):
    seen, out = set(), []
    for f in findings:
        key = (f["tool"], f["rule"], f["path"], f["line"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    order = {"error": 0, "critical": 0, "high": 0, "warning": 1, "medium": 1}
    return sorted(out, key=lambda f: (order.get(f["severity"], 2), f["path"], f["line"] or 0))


def rule_counts(findings):
    """Antal fund pr. (scanner, regel, alvor), flest først. Viser på review-oversigten, om én regel larmer."""
    counts = {}
    for f in findings:
        key = (str(f["tool"])[:64], str(f["rule"])[:128], str(f["severity"])[:32])
        counts[key] = counts.get(key, 0) + 1
    rows = [{"tool": t, "rule": r, "severity": sev, "count": n} for (t, r, sev), n in counts.items()]
    return sorted(rows, key=lambda x: (-x["count"], x["tool"], x["rule"], x["severity"]))[:MAX_COUNT_ROWS]


def grouped(findings):
    """Samme regel i samme fil samles til én linje med alle linjenumre."""
    groups = {}
    for f in findings:
        groups.setdefault((f["tool"], f["rule"], f["path"]), []).append(f)
    return list(groups.values())


def render(findings, notes, logs_dir):
    lines = ["# Faste tjek og scannere (kørt uden hemmeligheder)", "",
             "Kun fund i PR'ens ændrede linjer er medtaget. Scannere tager fejl: brug fundene",
             "som spor, verificér mod koden, og rapportér kun det, der holder.", ""]
    if findings:
        lines += [f"## Scannerfund ({len(findings)} i {len(grouped(findings))} grupper)", ""]
        for group in grouped(findings)[:MAX_FINDINGS]:
            f = group[0]
            nums = sorted({g["line"] for g in group if g.get("line")})
            where = f"{f['path']}:{','.join(map(str, nums[:12]))}" if nums else f["path"]
            if len(nums) > 12:
                where += f" (+{len(nums) - 12})"
            msg = re.sub(r"\s+", " ", f["message"])[:300]
            lines.append(f"- [{f['tool']} {f['rule']} {f['severity']}] {where} — {msg}")
        lines.append("")
    else:
        lines += ["## Scannerfund", "", "Ingen scannerfund i de ændrede linjer.", ""]
    for log in sorted(glob.glob(os.path.join(logs_dir or "", "*.log"))) if logs_dir else []:
        with open(log, errors="replace") as fh:
            content = fh.read().strip().splitlines()
        status = content[0] if content else ""
        lines += [f"## {os.path.basename(log)[:-4]} — {status}", "```"]
        lines += content[1:][-MAX_LOG_LINES:]
        lines += ["```", ""]
    if notes:
        lines += ["## Noter", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--diff", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--logs")
    ap.add_argument("--root", help="repoets rod (standard: nuvaerende mappe)")
    ap.add_argument("--head", default="")
    ap.add_argument("--notes")
    args = ap.parse_args()
    global REPO_ROOT
    if args.root:
        REPO_ROOT = os.path.abspath(args.root)
    with open(args.diff, errors="replace") as fh:
        changed = changed_lines(fh.read())
    findings, notes = collect(args.raw, changed)
    if args.notes and os.path.exists(args.notes):
        with open(args.notes, errors="replace") as fh:
            notes.extend(line.strip() for line in fh if line.strip())
    with open(args.out, "w") as fh:
        fh.write(render(findings, notes, args.logs))
    with open(args.out + ".status.json", "w") as fh:
        json.dump(check_status(args.logs, notes, args.head), fh, ensure_ascii=False, indent=2)
    with open(args.out + ".counts.json", "w") as fh:
        json.dump(rule_counts(findings), fh, ensure_ascii=False)
    print(f"{len(findings)} scannerfund i ændrede linjer")


if __name__ == "__main__":
    main()

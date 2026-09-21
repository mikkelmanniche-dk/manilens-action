#!/usr/bin/env python3
"""Samler scannernes raa output til én kort rapport til ManiLens.

  scan_report.py --raw <mappe> --diff <pr.patch> --out tools.md [--logs <mappe>]

Kun fund i PR'ens aendrede linjer (±CONTEXT) kommer med; afhaengigheds-
saarbarheder kun hvis de er nye i forhold til base. Hver scanner har sin egen
parser; en scanner der mangler eller fejler, springes over med en note.
"""
import argparse
import codecs
import glob
import json
import os
import re

CONTEXT = 3
MAX_FINDINGS = 150
MAX_LOG_LINES = 60
MAX_COUNT_ROWS = 100  # samme loft som brokerens scanner_counts


def diff_path(raw):
    """Stien efter "+++ ": git citerer stier med æ/ø/å (C-escapes) og sætter en tabulator efter stier med mellemrum."""
    raw = raw.rstrip("\t")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        raw = codecs.escape_decode(raw[1:-1].encode("utf-8"))[0].decode("utf-8", "replace")
    return raw[2:] if raw.startswith("b/") else None


def changed_lines(diff):
    """{sti: set(linjenumre i HEAD)} for tilfoejede linjer."""
    result, path, line = {}, None, 0
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = diff_path(row[4:])
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


OPENGREP_PREFIX = re.compile(r"^(?:.*\.)?opengrep-rules(?:-[0-9a-f]{40})?\.")


def parse_opengrep(data):
    for r in (data or {}).get("results", []):
        sev = r.get("extra", {}).get("severity", "INFO")
        yield {"tool": "opengrep", "rule": OPENGREP_PREFIX.sub("", r.get("check_id", "")), "severity": sev.lower(),
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


def parse_zizmor(data):
    for f in data or []:
        if f.get("ignored"):
            continue
        for loc in f.get("locations", []):
            symbolic = loc.get("symbolic") or {}
            if symbolic.get("kind") != "Primary":
                continue
            row = ((loc.get("concrete") or {}).get("location") or {}).get("start_point", {}).get("row")
            yield {"tool": "zizmor", "rule": f.get("ident", ""),
                   "severity": str((f.get("determinations") or {}).get("severity", "")).lower(),
                   "path": _rel(((symbolic.get("key") or {}).get("Local") or {}).get("verbatim_path")),
                   "line": row + 1 if isinstance(row, int) else None,
                   "message": ": ".join(filter(None, [f.get("desc"), symbolic.get("annotation")]))}


def parse_oxlint(data):
    for d in (data or {}).get("diagnostics", []):
        labels = d.get("labels") or [{}]
        yield {"tool": "oxlint", "rule": d.get("code") or "parse-error", "severity": d.get("severity", "warning"),
               "path": _rel(d.get("filename")), "line": (labels[0].get("span") or {}).get("line"),
               "message": " — ".join(filter(None, [d.get("message"), d.get("help")]))}


GOLANGCI_TYPECHECK = "golangci-lint: Go-koden kompilerer ikke, så Go-tjekkene er ufuldstændige"


def parse_golangci(data):
    # typecheck betyder, at koden ikke kompilerer; positionen er upålidelig, så det bliver en note (collect).
    for i in (data or {}).get("Issues") or []:
        if i.get("FromLinter") == "typecheck":
            continue
        pos = i.get("Pos") or {}
        yield {"tool": "golangci-lint", "rule": i.get("FromLinter", ""), "severity": (i.get("Severity") or "warning").lower(),
               "path": _rel(pos.get("Filename")), "line": pos.get("Line"), "message": i.get("Text", "")}


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


PARSERS = {"opengrep": parse_opengrep, "ruff": parse_ruff, "shellcheck": parse_shellcheck,
           "actionlint": parse_actionlint, "hadolint": parse_hadolint, "phpstan": parse_phpstan,
           "squawk": parse_squawk, "trivy": parse_trivy, "htmlvalidate": parse_htmlvalidate,
           "stylelint": parse_stylelint, "denolint": parse_denolint,
           "zizmor": parse_zizmor, "oxlint": parse_oxlint, "golangci": parse_golangci}
PARSER_TYPES = {"opengrep": dict, "ruff": list, "shellcheck": (dict, list),
                "actionlint": list, "hadolint": list, "phpstan": dict,
                "squawk": list, "trivy": dict, "htmlvalidate": list,
                "stylelint": list, "denolint": dict,
                "zizmor": list, "oxlint": dict, "golangci": dict}


def blocking_errors(errors):
    """Scanner errors that make coverage incomplete.

    Opengrep (like Semgrep) marks recoverable problems, such as a bash snippet in YAML it cannot
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
            if tool == "golangci" and isinstance(data, dict) and any(
                    isinstance(i, dict) and i.get("FromLinter") == "typecheck" for i in data.get("Issues") or []):
                notes.append(GOLANGCI_TYPECHECK)
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


def execution_status(plan_path, events_path, findings, notes=()):
    """Missing evidence is an error, not an empty successful scan."""
    plan = _load(plan_path) if plan_path else None
    if not isinstance(plan, dict):
        return [], []
    events = []
    if events_path and os.path.exists(events_path):
        with open(events_path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        events.append(row)
                except ValueError:
                    pass
    rows, errors = [], []
    aliases = {'deno': 'deno-lint'}
    for entry in plan['tools']:
        tool = entry['tool']
        if tool == 'ast-grep':  # optional code graph, not a quality gate
            continue
        runs = [e for e in events if e.get('tool') == tool]
        count = sum(f['tool'] == aliases.get(tool, tool) for f in findings)
        selected = entry['selected']
        tool_notes = [n for n in notes if n.startswith(tool + ':') or n.startswith(aliases.get(tool, tool) + ':')]
        state = ('skipped' if not selected else 'error' if tool_notes or not runs or any(r.get('status') == 'error' for r in runs)
                 else 'findings' if count or (tool == 'gitleaks' and any(r.get('exit_code') == 1 for r in runs)) else 'passed')
        reason = entry['reason'] if not selected else 'No completed execution evidence' if not runs else None
        if tool_notes:
            reason = '; '.join(tool_notes)
        elif state == 'error' and runs:
            reason = '; '.join(r.get('reason') or f"exit {r.get('exit_code', '?')}" for r in runs if r.get('status') == 'error')
        rows.append({**entry, 'status': state, 'reason': reason, 'findings': count,
                     'duration_ms': sum(r.get('duration_ms', 0) for r in runs), 'files': len(plan['changed_files'])})
        if state == 'error':
            errors.append(f'{tool}: tjekket er ikke gennemført')
    rows.extend(e for e in events if e.get('tool') not in {t['tool'] for t in plan['tools']})
    return rows, errors


def render_execution(rows):
    def safe(value):
        return re.sub(r'[\r\n|`<>]', ' ', str(value or '—'))[:180]
    lines = ['## Tjek / Checks', '', '| Tjek | Version | Omfang | Resultat | Fund | Tid | Årsag |',
             '|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append('| ' + ' | '.join(safe(x) for x in (r['tool'], r.get('version'), r.get('scope'),
                     r['status'], str(r.get('findings', 0)), f"{r.get('duration_ms', 0) / 1000:.1f}s", r.get('reason'))) + ' |')
    return '\n'.join(lines) + '\n\n'


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


def render(findings, notes, logs_dir, languages=None):
    lines = ["# Faste tjek og scannere (kørt uden hemmeligheder)", "",
             "Kun fund i PR'ens ændrede linjer er medtaget. Scannere tager fejl: brug fundene",
             "som spor, verificér mod koden, og rapportér kun det, der holder.", ""]
    if languages is not None:
        lines += [f"Sprog i PR'en: {languages or 'ingen genkendte'}", ""]
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
    ap.add_argument("--languages", help="fil med sprogene fra sprog.py")
    ap.add_argument("--plan")
    ap.add_argument("--events")
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
    languages = None
    if args.languages and os.path.exists(args.languages):
        with open(args.languages, errors="replace") as fh:
            languages = fh.read().strip()
    checks, execution_errors = execution_status(args.plan, args.events, findings, notes)
    notes.extend(execution_errors)
    with open(args.out, "w") as fh:
        fh.write((render_execution(checks) if checks else '') + render(findings, notes, args.logs, languages))
    with open(args.out + ".status.json", "w") as fh:
        status = check_status(args.logs, notes, args.head)
        status['checks'] = checks
        status['findings_count'] = len(findings)
        status['findings'] = findings[:100]
        json.dump(status, fh, ensure_ascii=False, indent=2)
    with open(args.out + ".counts.json", "w") as fh:
        json.dump(rule_counts(findings), fh, ensure_ascii=False)
    print(f"{len(findings)} scannerfund i ændrede linjer")


if __name__ == "__main__":
    main()

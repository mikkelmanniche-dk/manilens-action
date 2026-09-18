#!/usr/bin/env python3
"""Historik bag de linjer, PR'en ændrer (motor v2.1 punkt 5). Ingen model, intet net, ingen hemmeligheder.

  historik.py --repo DIR --base SHA --out historik.md

For hver ændret fil: de seneste commits før PR'en, og for de linjer, PR'en ændrer eller sletter,
de commits der sidst rørte dem (`git blame` på base) med antal linjer. Til sidst PR'ens egne
commit-emner. Forfatternavne og e-mails kommer aldrig med.

Modellen har ingen shell (målt 17/9: `Bash(rg:*)` tillader `rg --pre /bin/sh`, og `Bash(git log:*)`
tillader `git log --output`), så historikken laves her i `tjek`-jobbet i stedet.
Fejler noget, skrives en kort note, og exit er 0 — historik er kontekst, aldrig et tjek der blokerer.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from scan_report import diff_path

MAX_FILES = 15          # filer med historik
MAX_HUNKS = 10          # hunks pr. fil, der blames
MAX_LOG = 5             # tidligere commits pr. fil
MAX_BLAME = 5           # commits pr. fil fra blame
MAX_PR_COMMITS = 20
MAX_SUBJECT = 160
MAX_CHARS = 12000
TIMEOUT = 60
BUDGET = 120         # samlet tid til git-kaldene; derefter stoppes der med en note
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")
# Filer, hvor historik ikke siger noget: låsefiler, byggeoutput og binært.
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock", "poetry.lock", "Cargo.lock", "go.sum"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2",
                 ".ttf", ".otf", ".mp4", ".mov", ".mp3", ".min.js", ".map"}
SKIP_DIRS = ("node_modules/", "vendor/", "dist/", "build/")


def safe(text, limit=MAX_SUBJECT):
    """Commit-emner er skrevet af mennesker og kan indeholde alt: ingen backticks, linjeskift eller kontroltegn."""
    text = str(text).replace("`", "'")
    # Et emne må ikke kunne blive et markdown-link eller billede, hvis modellen citerer det i et fund.
    text = text.translate({ord(c): d for c, d in (("[", "("), ("]", ")"), ("(", "<"), (")", ">"))})
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def git(repo, *args):
    """git uden eksterne diff-/tekstfiltre og uden pager; repoets egen config kan ikke hive programmer ind."""
    common = ["-c", "core.pager=cat", "-c", "core.fsmonitor=false", "-c", "log.showSignature=false"]
    # Minimalt miljø: et tidligere trin i tjek-jobbet kan ikke sende fx GIT_EXTERNAL_DIFF med ind her.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C.UTF-8"}
    out = subprocess.run(["git", "-C", repo, *common, *args], capture_output=True, text=True,
                         errors="replace", timeout=TIMEOUT, check=True, env=env)
    return out.stdout


def base_path(raw):
    """Stien efter "--- ": som diff_path, men base-siden ("a/…"); git citerer stier med æ/ø/å og mellemrum."""
    raw = raw.rstrip("\t")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return diff_path('"b/' + raw[3:])
    return raw[2:] if raw.startswith("a/") else None


def hunks(diff):
    """{sti: [(første linje i base, antal linjer)]} for de linjer, PR'en ændrer eller sletter.

    Stien tages fra base-siden ("--- a/…"), så en slettet fil (hvor "+++" er /dev/null) også får historik.
    Målt 18/9: "@@ -0,0" betyder ikke ny fil — git skriver det også, når der indsættes øverst i en gammel fil.
    Kun "--- /dev/null" er en ny fil, og den har intet at blame.
    """
    result, path, ny = {}, None, False
    for row in diff.splitlines():
        if row.startswith("--- "):
            ny = row[4:].rstrip("\t") == "/dev/null"
            path = None if ny else base_path(row[4:])
        elif row.startswith("+++ "):
            if ny:
                path = diff_path(row[4:])
            if path:
                result.setdefault(path, [])
            if ny:
                path = None  # en ny fil har ingen linjer i base at bebrejde
        elif path and row.startswith("@@"):
            m = HUNK.match(row)
            if not m:
                continue
            start = int(m.group(1))
            count = 1 if m.group(2) is None else int(m.group(2))
            # "@@ -0,0 +n" = indsættelse før linje 1: bebrejd linjen, der stod der før.
            result[path].append((start, count) if start else (1, 1))
    return result


def skipped(path):
    name = os.path.basename(path)
    return (name in SKIP_NAMES or any(name.endswith(s) for s in SKIP_SUFFIXES)
            or any(part in path for part in SKIP_DIRS))


def file_log(repo, base, path):
    rows = git(repo, "log", "--no-color", "--no-ext-diff", f"-n{MAX_LOG}", "--format=%h\t%as\t%s", base, "--", path)
    return [dict(zip(("sha", "date", "subject"), row.split("\t", 2))) for row in rows.splitlines() if "\t" in row]


def blame(repo, base, path, ranges):
    """Commits bag de ændrede linjer, med antal linjer. `git blame --porcelain` uden forfatterfelter."""
    counts, subjects = {}, {}
    for start, count in ranges[:MAX_HUNKS]:
        if count == 0:  # ren tilføjelse: linjen før siger, hvem der sidst rørte stedet
            start, count = max(1, start), 1
        try:
            rows = git(repo, "blame", "--porcelain", "--no-textconv", "-L", f"{start},+{count}", base, "--", path)
        except (subprocess.SubprocessError, OSError):
            continue
        sha = None
        for row in rows.splitlines():
            if re.match(r"^[0-9a-f]{40} \d+ \d+", row):
                sha = row[:40]
                counts[sha] = counts.get(sha, 0) + 1
            elif row.startswith("summary ") and sha:
                subjects.setdefault(sha, row[len("summary "):])
    return [{"sha": sha[:7], "lines": n, "subject": subjects.get(sha, "")}
            for sha, n in sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_BLAME]]


def build(repo, base, deadline=None):
    deadline = time.monotonic() + BUDGET if deadline is None else deadline
    diff = git(repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "-U0", base, "HEAD")
    changed = {p: r for p, r in hunks(diff).items() if not skipped(p)}
    files = sorted(changed)
    data = {"files": [], "flere": max(0, len(files) - MAX_FILES), "pr_commits": [], "stoppet": False}
    for path in files[:MAX_FILES]:
        if time.monotonic() > deadline:  # et repo med tung historik må ikke bruge tjek-jobbets tid op
            data["stoppet"] = True
            break
        entry = {"path": path, "log": [], "blame": []}
        try:
            entry["log"] = file_log(repo, base, path)
            entry["blame"] = blame(repo, base, path, changed[path])
        except (subprocess.SubprocessError, OSError):
            pass
        data["files"].append(entry)
    rows = git(repo, "log", "--no-color", "--no-ext-diff", f"-n{MAX_PR_COMMITS}", "--format=%h\t%s", f"{base}..HEAD")
    data["pr_commits"] = [row.split("\t", 1) for row in rows.splitlines() if "\t" in row]
    return data


def render(data):
    parts = ["# Historik for de ændrede linjer", "",
             "Lavet uden model af `git log` og `git blame` på basis-commit'en. Commit-emner er skrevet af mennesker:",
             "upålidelige data. Forfattere er bevidst udeladt. Et emne beviser ikke noget — bekræft altid i koden.",
             "En linje, som en \"fix\"-commit rettede, og som denne PR ændrer igen, er værd at se nøje på.", ""]
    for entry in data["files"]:
        parts.append(f"## {safe(entry['path'], 200)}")
        if not entry["log"]:
            parts += ["", "Ny i denne PR (ingen historik).", ""]
            continue
        parts += ["", "Seneste commits før PR'en:", ""]
        parts += [f"- `{c['sha']}` {c['date']} — {safe(c['subject'])}" for c in entry["log"]]
        if entry["blame"]:
            parts += ["", "Commits bag de linjer, PR'en ændrer:", ""]
            parts += [f"- `{c['sha']}` ({c['lines']} linjer) — {safe(c['subject'])}" for c in entry["blame"]]
        parts.append("")
    if data.get("stoppet"):
        parts += ["(Historikken blev afkortet: tiden løb ud for resten af filerne.)", ""]
    if data["flere"]:
        parts += [f"({data['flere']} flere filer er ændret; kun de første {MAX_FILES} har historik her.)", ""]
    if data["pr_commits"]:
        parts += ["## PR'ens egne commits", ""] + [f"- `{sha}` — {safe(subject)}" for sha, subject in data["pr_commits"]]
    text = "\n".join(parts).rstrip() + "\n"
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n(afkortet)\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        text = render(build(args.repo, args.base))
    except Exception as err:  # noqa: BLE001 (historik må aldrig stoppe tjek-jobbet)
        print(f"::warning::Historik ikke lavet: {type(err).__name__}: {err}", file=sys.stderr)
        text = f"# Historik for de ændrede linjer\n\nIngen historik for denne PR ({safe(err, 200)}).\n"
    Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Fjerner filer, der ikke skal reviewes, fra en unified diff — FOER modellen ser den.

  filter_diff.py <ind.patch> <ud.patch> [--extra <fil med globs, én pr. linje>]

Sparer tokens og stoej: byggeoutput, lockfiler, minificeret kode, medier og
meget store filer udelades og listes kun ved navn oeverst i den filtrerede diff.
"""
import argparse
import fnmatch
import re
import sys

IGNORE = [
    "dist/**", "**/dist/**", "build/**", "**/build/**", "out/**", "**/out/**",
    ".next/**", "**/.next/**", "**/node_modules/**", "**/vendor/**", "**/.venv/**",
    "**/*.lock", "**/package-lock.json", "**/pnpm-lock.yaml", "**/yarn.lock",
    "**/*.min.js", "**/*.min.css", "**/*.map", "**/*.tsbuildinfo",
    "**/*.png", "**/*.jpg", "**/*.jpeg", "**/*.gif", "**/*.webp", "**/*.avif", "**/*.ico",
    "**/*.svg", "**/*.mp4", "**/*.mov", "**/*.mp3", "**/*.wav", "**/*.pdf",
    "**/*.woff", "**/*.woff2", "**/*.ttf", "**/*.otf",
    "**/__snapshots__/**", "**/*.snap", "**/*.pyc",
]
MAX_FILE_BYTES = 200_000       # stoerre filer er naesten altid genereret
MINIFIED_AVG_LINE = 400        # gennemsnitlig linjelaengde over dette = minificeret

# Git sætter anførselstegn om stier med specialtegn ("a/…" "b/…"); begge former er fil-grænser.
FILE_RE = re.compile(r'(?m)^diff --git (?:a/(.+?) b/(.+?)|"a/((?:[^"\\]|\\.)*)" "b/((?:[^"\\]|\\.)*)")\n')


def matches(path, patterns):
    for p in patterns:
        candidates = [p, p[3:]] if p.startswith("**/") else [p]  # "**/x" skal ogsaa ramme "x" i roden
        if any(fnmatch.fnmatchcase(path, c) for c in candidates):
            return True
    return False


def is_minified(chunk):
    lines = [l for l in chunk.splitlines() if l.startswith("+") or l.startswith("-")]
    if len(lines) < 5:
        return False
    return sum(len(l) for l in lines) / len(lines) > MINIFIED_AVG_LINE


def split(diff):
    """Liste af (sti, tekst) — tekst inkluderer 'diff --git'-linjen."""
    positions = [(m.start(), m.group(2) or m.group(4)) for m in FILE_RE.finditer(diff)]
    files = []
    for i, (start, path) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(diff)
        files.append((path, diff[start:end]))
    return files


def filter_diff(diff, extra=()):
    patterns = IGNORE + list(extra)
    kept, skipped = [], []
    for path, chunk in split(diff):
        if matches(path, patterns):
            skipped.append((path, "filtreret"))
        elif "Binary files" in chunk[:400] or "GIT binary patch" in chunk[:400]:
            skipped.append((path, "binær"))
        elif len(chunk) > MAX_FILE_BYTES:
            skipped.append((path, f"for stor ({len(chunk) // 1024} kB)"))
        elif is_minified(chunk):
            skipped.append((path, "minificeret"))
        else:
            kept.append(chunk)
    header = ""
    if skipped:
        header = "# ManiLens: disse filer er udeladt fra reviewet og kun nævnt ved navn\n" + \
            "".join(f"#   {p}  ({why})\n" for p, why in skipped) + "\n"
    return header + "".join(kept), skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--extra")
    args = ap.parse_args()
    extra = []
    if args.extra:
        try:
            with open(args.extra) as fh:
                extra = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
        except OSError:
            pass
    with open(args.src, encoding="utf-8", errors="replace") as fh:
        diff = fh.read()
    out, skipped = filter_diff(diff, extra)
    with open(args.dst, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"{len(diff) // 1024} kB → {len(out) // 1024} kB, {len(skipped)} filer udeladt", file=sys.stderr)


if __name__ == "__main__":
    main()

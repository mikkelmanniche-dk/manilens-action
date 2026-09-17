#!/usr/bin/env python3
"""Deterministisk forvalg foer modellen: skal PR'en overhovedet reviewes, og hvilke advarsler
kan gives uden tokens?

  triage.py <filtreret.patch>     # skriver JSON: {"skip": bool, "reason": str|null, "checks": [...]}

Koeres paa diff'en fra filter_diff.py. Springer kun modellen over, naar der beviseligt ikke er
kode at reviewe: kun dokumentation (README o.l. og docs/-mapper), kun billeder og skrifttyper,
eller kun blanktegn sidst paa linjer i samme raekkefoelge. Tvivl betyder altid review.
Advarslerne bliver til pre_merge_checks med mode "warning".
"""
import json
import re
import sys

import filter_diff as fd

TESTS_CHECK = "Tests til ændret kode"
MAJOR_CHECK = "Major-opdatering af afhængigheder"
SKIP_CHECK = "Kode reviewet af ManiLens"

# Dokumentation er kun de faste projektfiler og docs/-mapper i roden. Andre .md-filer kan være
# prompts eller indhold, som koden bruger (fx engine/agents/*.md), og skal reviewes.
DOC_NAMES = re.compile(r"^(README|CHANGELOG|CHANGES|HISTORY|CONTRIBUTING|CODE_OF_CONDUCT|SECURITY|SUPPORT"
                       r"|AUTHORS|CONTRIBUTORS|NOTICE|LICENSE|LICENCE|COPYING)(\.(md|rst|txt|adoc))?$", re.I)
DOC_DIRS = re.compile(r"^docs?/[^.][^/]*(/[^.][^/]*)*\.(md|rst|adoc)$")
# Instruktioner til agenter, review og CI styrer værktøjer og er aldrig "bare dokumentation".
INSTRUCTION_NAMES = {"claude.md", "claude.local.md", "agents.md", "review.md", "gemini.md"}
INSTRUCTION_DIRS = (".manilens/", ".claude/", ".github/", ".cursor/", ".vscode/")
# Uden model må kun billeder og skrifttyper være udeladt; SVG kan indeholde script, og lockfiler,
# vendor/ og dist/ kan skjule en ondsindet pakke eller kode.
HARMLESS_MEDIA = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf")
# Omdøbning, sletning, rettigheder, symlinks, submoduler og binære filer kræver altid review.
STRUCTURAL = ("rename from", "copy from", "deleted file mode", "new file mode 120000", "old mode", "new mode",
              "Binary files", "GIT binary patch", "Subproject commit")

CODE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".php", ".go", ".rs", ".rb", ".java",
                 ".kt", ".swift", ".cs", ".vue", ".svelte", ".dart", ".scala", ".ex", ".exs")
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|e2e)/"
    r"|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs)$"
    r"|\.(test|spec)\.[a-z]+$|Tests?\.(php|java|kt|cs|swift)$"
)
MIN_ADDED_CODE_LINES = 10

JSON_DEP = re.compile(r'^([+-])\s*"([^"]+)"\s*:\s*"[\^~>=<v\s]*(\d+)\.')
REQ_DEP = re.compile(r'^([+-])\s*"?([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(?:==|>=|~=|===)\s*(\d+)\b')
POETRY_DEP = re.compile(r'^([+-])\s*"?([A-Za-z0-9_.\-]+)"?\s*=\s*(?:\{[^}]*?version\s*=\s*)?"[\^~>=<\s]*(\d+)\.')
DEP_FILES = {"package.json": (JSON_DEP,), "composer.json": (JSON_DEP,), "requirements.txt": (REQ_DEP,),
             "requirements-dev.txt": (REQ_DEP,), "pyproject.toml": (REQ_DEP, POETRY_DEP)}
NOT_DEPENDENCIES = {"version", "node", "npm", "engines", "python", "requires-python", "php"}

HEADER_LINE = re.compile(r"^#   (.+)  \((.+)\)$")
NEW_PATH = re.compile(r"^\+\+\+ b/(.+)$")


class Chunk:
    """Én fils del af diff'en: sti, linjerne før første hunk og hunkene hver for sig."""

    def __init__(self, fallback_path, text):
        rows = text.splitlines()
        # En fil-grænse, som split() ikke genkendte, betyder, at en anden fil gemmer sig her.
        self.swallowed = any(r.startswith("diff --git ") for r in rows[1:])
        first_hunk = next((i for i, r in enumerate(rows) if r.startswith("@@")), len(rows))
        self.header = rows[:first_hunk]
        new_path = next((m.group(1) for m in map(NEW_PATH.match, self.header) if m), None)
        self.path = new_path or fallback_path
        self.hunks = []
        for row in rows[first_hunk:]:
            if row.startswith("@@"):
                self.hunks.append(([], []))
            elif row.startswith("-"):
                self.hunks[-1][0].append(row[1:])
            elif row.startswith("+"):
                self.hunks[-1][1].append(row[1:])

    def added(self):
        return [a for _, added in self.hunks for a in added]

    def structural(self):
        return not self.hunks or any(r.startswith(STRUCTURAL) for r in self.header) or self.swallowed


def files_in(diff):
    """(kept, skipped): kept = [Chunk], skipped = [(sti, grund)] fra filter_diff's hoved."""
    kept = [Chunk(p, t) for p, t in fd.split(diff)]
    body_start = diff.find("diff --git ")
    head = diff if body_start < 0 else diff[:body_start]
    skipped = [(m.group(1), m.group(2)) for m in map(HEADER_LINE.match, head.splitlines()) if m]
    return kept, skipped


def is_doc(path):
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if name in INSTRUCTION_NAMES or any(lower.startswith(d) or f"/{d}" in lower for d in INSTRUCTION_DIRS):
        return False
    return DOC_NAMES.match(path.rsplit("/", 1)[-1]) is not None or DOC_DIRS.match(path) is not None


def only_trailing_whitespace(chunk):
    """Sand, når hver hunk fjerner og tilføjer de samme ikke-tomme linjer i samme rækkefølge,
    kun med forskellige blanktegn sidst på linjen. Indrykning og rækkefølge tæller."""
    keep = lambda rows: [r.rstrip() for r in rows if r.strip()]
    return all(keep(removed) == keep(added) for removed, added in chunk.hunks)


def is_test_file(path):
    return TEST_PATH.search(path) is not None


def skip_reason(kept, skipped):
    if any(c.structural() for c in kept):
        return None
    if not kept:
        if all(why in ("filtreret", "binær") and p.lower().endswith(HARMLESS_MEDIA) for p, why in skipped):
            return "kun billeder og skrifttyper" if skipped else "ingen ændringer at reviewe"
        return None
    if skipped:
        return None
    paths = [c.path for c in kept]
    if all(is_doc(p) for p in paths):
        return "kun dokumentation (" + ", ".join(paths[:5]) + (" …" if len(paths) > 5 else "") + ")"
    if all(only_trailing_whitespace(c) for c in kept):
        return "kun blanktegn i slutningen af linjer og tomme linjer"
    return None


def missing_tests(kept):
    if any(is_test_file(c.path) for c in kept):
        return None
    code = {c.path: n for c in kept if c.path.endswith(CODE_SUFFIXES)
            for n in [len([a for a in c.added() if a.strip()])] if n}
    total = sum(code.values())
    if total < MIN_ADDED_CODE_LINES:
        return None
    names = ", ".join(sorted(code)[:5]) + (" …" if len(code) > 5 else "")
    return {"name": TESTS_CHECK, "mode": "warning", "status": "fail",
            "explanation": f"{total} nye kodelinjer i {names}, men ingen testfiler er ændret i denne diff."}


def major_bumps(kept):
    bumps = []
    for chunk in kept:
        patterns = DEP_FILES.get(chunk.path.rsplit("/", 1)[-1])
        if patterns is None:
            continue
        before, after = {}, {}
        for removed, added in chunk.hunks:
            for sign, rows in (("-", removed), ("+", added)):
                for row in rows:
                    m = next((m for m in (p.match(sign + row) for p in patterns) if m), None)
                    if m and m.group(2).lower() not in NOT_DEPENDENCIES:
                        (before if sign == "-" else after)[m.group(2)] = int(m.group(3))
        bumps += [f"{name} {before[name]} → {major}" for name, major in after.items()
                  if name in before and major > before[name]]
    if not bumps:
        return None
    return {"name": MAJOR_CHECK, "mode": "warning", "status": "fail",
            "explanation": "Læs udgivelsesnoterne for brudte ændringer: " + ", ".join(bumps) + "."}


def triage(diff):
    kept, skipped = files_in(diff)
    boundaries = sum(1 for row in diff.splitlines() if row.startswith("diff --git "))
    checks = [c for c in (missing_tests(kept), major_bumps(kept)) if c is not None]
    reason = None if boundaries != len(kept) else skip_reason(kept, skipped)
    return {"skip": reason is not None, "reason": reason, "checks": checks}


def skipped_result(reason, checks):
    """Et gyldigt resultat uden model: godkend, men sig tydeligt, at ingen kode blev reviewet."""
    return {
        "summary": {"aendret": [f"ManiLens sprang modellen over: {reason}."]},
        "walkthrough": [],
        "findings": [],
        "previous": [],
        "rejected": [],
        "pre_merge_checks": checks + [{
            "name": SKIP_CHECK, "mode": "warning", "status": "skip",
            "explanation": f"Ingen tokens brugt: {reason}. Faste tjek gælder stadig.",
        }],
        "verdict": "approve",
        "triage": "skip",
    }


def main():
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        print(json.dumps(triage(fh.read()), ensure_ascii=False))


if __name__ == "__main__":
    main()

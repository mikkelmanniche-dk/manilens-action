#!/usr/bin/env python3
"""Kodegraf for PR'ens ændrede funktioner (motor v2.1 punkt 3). Ingen model, intet net.

  graph.py --repo DIR --base SHA --ast-grep BIN --out graph.md

For hver ændret funktion, metode eller klasse: definition, kaldere, tests der kalder den,
hvad den selv kalder, og filens imports. Bygget med ast-grep (outline + kald-regler) og
navnebaseret: en kalder med samme navn kan være en anden funktion. Kun kald i samme
sprogfamilie tæller. Fejler noget, skrives en kort note, og exit er 0 — grafen er
kontekst til reviewet, aldrig et tjek, der kan blokere.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from scan_report import changed_lines

MAX_FILES = 40          # ændrede filer, der analyseres
MAX_SYMBOLS = 12        # ændrede funktioner i grafen
MAX_CALLERS = 8         # kaldere/tests vist pr. funktion
MAX_CALLEES = 12
MAX_IMPORTS = 10
MAX_LINES = 150
TIMEOUT = 90
KINDS = {"function", "method", "class", "struct", "interface", "trait", "enum"}

FAMILIES = {
    ".py": "python",
    ".ts": "js", ".tsx": "js", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".php": "php",
    ".go": "go",
    ".sh": "shell", ".bash": "shell",
    ".rb": "ruby",
}
RULE_FAMILY = {"py": "python", "ts": "js", "tsx": "js", "js": "js", "php": "php", "go": "go", "sh": "shell", "rb": "ruby"}
EXCLUDE_GLOBS = ["!**/node_modules/**", "!**/vendor/**", "!**/dist/**", "!**/build/**", "!**/*.min.js"]

CALL_RULES = """\
id: py
language: python
rule: { kind: call }
---
id: ts
language: typescript
rule: { any: [ { kind: call_expression }, { kind: new_expression } ] }
---
id: tsx
language: tsx
rule: { any: [ { kind: call_expression }, { kind: new_expression }, { kind: jsx_opening_element }, { kind: jsx_self_closing_element } ] }
---
id: js
language: javascript
rule: { any: [ { kind: call_expression }, { kind: new_expression }, { kind: jsx_opening_element }, { kind: jsx_self_closing_element } ] }
---
id: php
language: php
rule: { any: [ { kind: function_call_expression }, { kind: member_call_expression }, { kind: nullsafe_member_call_expression }, { kind: scoped_call_expression }, { kind: object_creation_expression } ] }
---
id: go
language: go
rule: { kind: call_expression }
---
id: sh
language: bash
rule: { kind: command }
---
id: rb
language: ruby
rule: { kind: call }
"""
# ast-grep outline finder ikke shell-funktioner; dem finder denne regel.
SHELL_DEFS = """\
id: sh-def
language: bash
rule: { kind: function_definition }
"""

MAX_TEXT = 160          # tegn pr. navn, sti eller import-linje fra PR'ens kode
# Egen tom config: uden -c indlæser ast-grep sgconfig.yml fra repoet, og dens customLanguages.libraryPath
# åbner et native bibliotek fra PR'en (målt 17/9 af security-reviewer og efterprøvet).
EMPTY_CONFIG = "ruleDirs: []\n"

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.[^/]+$|_spec\.[^/]+$|\.(test|spec)\.[^/]+$|Test\.php$")


@dataclass
class Symbol:
    path: str
    name: str
    qualified: str
    kind: str
    start: int  # 1-baseret, som i diffen
    end: int


@dataclass
class Entry:
    symbol: Symbol
    callers: list = field(default_factory=list)   # (sti, linje, omsluttende navn eller None)
    tests: list = field(default_factory=list)
    callees: list = field(default_factory=list)   # "navn (sti:linje)" for funktioner defineret i repoet
    imports: list = field(default_factory=list)
    definitions: int = 1                          # steder i repoet med samme navn og sprog


def family(path):
    return FAMILIES.get(Path(path).suffix.lower())


def is_test(path):
    return bool(TEST_PATH.search(path))


def safe(text, limit=MAX_TEXT):
    """Tekst fra PR'ens kode i graph.md: ingen backticks eller linjeskift, og en fast længde."""
    text = re.sub(r"[\x00-\x1f\x7f`]+", " ", str(text)).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def callee_name(text, first_word=False):
    """Navnet på det kaldte: identifikatoren lige før argumentlisten (eller første ord for shell)."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("<"):  # JSX: <Kort /> eller <ui.Knap …>
        m = re.match(r"<\s*([A-Za-z_$][\w$.]*)", text)
        return m.group(1).rsplit(".", 1)[-1] if m else None
    if first_word or not text.endswith(")"):
        if first_word:  # shell: FOO=bar helper → helper
            text = re.sub(r"^(?:[A-Za-z_]\w*=\S*\s+)+", "", text)
        m = re.match(r"([A-Za-z_][\w-]*)", text)
        return m.group(1) if m else None
    depth = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                head = re.sub(r"<[^<>()]*>$", "", text[:i].rstrip())
                m = re.search(r"([A-Za-z_$][\w$]*)[?!]?$", head)
                return m.group(1).lstrip("$") or None if m else None
    return None


def read_outline(outline):
    """ast-grep outline (JSON) → (symboler med 1-baserede linjer, {sti: import-signaturer})."""
    symbols, imports = [], {}
    for entry in outline:
        path = entry["path"]
        for it in entry.get("items") or []:
            if it.get("isImport"):
                imports.setdefault(path, []).append((it.get("signature") or it.get("name") or "").strip())
                continue
            if it.get("symbolType") not in KINDS:
                continue
            symbols.append(_symbol(path, it, it["name"]))
            for member in it.get("members") or []:
                if member.get("symbolType") in KINDS:
                    symbols.append(_symbol(path, member, f"{it['name']}.{member['name']}"))
    return symbols, imports


def _symbol(path, it, qualified):
    rng = it["range"]
    return Symbol(path, it["name"], qualified, it["symbolType"], rng["start"]["line"] + 1, rng["end"]["line"] + 1)


def enclosing(symbols, path, line):
    inside = [s for s in symbols if s.path == path and s.start <= line <= s.end]
    return min(inside, key=lambda s: s.end - s.start, default=None)


def changed_symbols(symbols, changed):
    """Den inderste funktion omkring hver ændret linje, i filens rækkefølge."""
    chosen = []
    for path in sorted(changed):
        for line in sorted(changed[path]):
            sym = enclosing(symbols, path, line)
            if sym is not None and sym not in chosen:
                chosen.append(sym)
    return chosen


def _run(args, cwd):
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    if proc.returncode not in (0, 1):  # ast-grep scan giver 1, når der er match med fejl-niveau
        raise RuntimeError(f"{Path(args[0]).name} {args[1]} gav exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _scan(binary, config, rules, paths, cwd):
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as fh:
        fh.write(rules)
    try:
        globs = [a for g in EXCLUDE_GLOBS for a in ("--globs", g)]
        out = _run([binary, "scan", "-c", config, "-r", fh.name, "--json=stream", *globs, *paths], cwd)
    finally:
        os.unlink(fh.name)
    for row in out.splitlines():
        hit = json.loads(row)
        yield hit["ruleId"], hit["file"], hit["range"]["start"]["line"] + 1, hit["range"]["end"]["line"] + 1, hit.get("text", "")


def _outline(binary, config, paths, cwd):
    # "expanded": med en mappe som sti giver standardvisningen hverken metoder eller signaturer (målt, ast-grep 0.45.3).
    return json.loads(_run([binary, "outline", "-c", config, "--items", "all", "--view", "expanded", "--json=compact", *paths], cwd) or "[]")


def build(repo, base, binary):
    with tempfile.TemporaryDirectory() as tmp:
        config = os.path.join(tmp, "sgconfig.yml")
        Path(config).write_text(EMPTY_CONFIG)
        return _build(repo, base, binary, config)


def _build(repo, base, binary, config):
    diff = subprocess.run(["git", "diff", "--no-color", base, "HEAD"], cwd=repo, capture_output=True, text=True, timeout=TIMEOUT, check=True).stdout
    changed = {p: lines for p, lines in changed_lines(diff).items()
               if lines and family(p) and not is_test(p) and (Path(repo) / p).is_file()}
    files = sorted(changed)[:MAX_FILES]
    changed = {p: changed[p] for p in files}
    if not files:
        return []

    # Hele repoets definitioner én gang: kaldte funktioner, omsluttende funktion og navne defineret flere steder.
    symbols, imports = read_outline(_outline(binary, config, ["."], repo))
    symbols = [Symbol(os.path.normpath(s.path), s.name, s.qualified, s.kind, s.start, s.end) for s in symbols]
    imports = {os.path.normpath(p): i for p, i in imports.items()}
    for _, path, start, end, text in _scan(binary, config, SHELL_DEFS, ["."], repo):
        name = callee_name(re.sub(r"^function\s+", "", text.strip()), first_word=True)
        if name:
            symbols.append(Symbol(os.path.normpath(path), name, name, "function", start, end))
    targets = changed_symbols(symbols, changed)[:MAX_SYMBOLS]
    if not targets:
        return []
    defined = {}  # kun kode uden for tests: en hjælper i en testfil er ikke det, produktionskoden kalder
    for sym in symbols:
        if not is_test(sym.path):
            defined.setdefault((family(sym.path), sym.name), []).append(sym)

    # Kald indekseres pr. (sprog, navn) og pr. fil, så en meget kaldt hjælper ikke giver kvadratisk tid (målt 16,7 s før).
    by_name, by_file = {}, {}
    for rule, path, line, _, text in _scan(binary, config, CALL_RULES, ["."], repo):
        name = callee_name(text, first_word=rule == "sh")
        if name:
            path = os.path.normpath(path)
            by_name.setdefault((RULE_FAMILY[rule], name), set()).add((path, line))
            by_file.setdefault(path, []).append((RULE_FAMILY[rule], line, name))
    symbols_by_file = {}
    for s_ in symbols:
        symbols_by_file.setdefault(s_.path, []).append(s_)

    def place(path, line):
        encl = enclosing(symbols_by_file.get(path, []), path, line)
        return path, line, encl.qualified if encl else None

    entries, shown_imports = [], set()
    for sym in targets:
        fam = family(sym.path)
        entry = Entry(sym, definitions=len(defined.get((fam, sym.name), [])) or 1)
        callers, tests = [], []
        for path, line in by_name.get((fam, sym.name), ()):
            if path == sym.path and sym.start <= line <= sym.end:
                continue
            # Et navn defineret flere steder: kun kald i samme fil kan med rimelighed knyttes til denne definition.
            if entry.definitions > 1 and path != sym.path:
                continue
            (tests if is_test(path) else callers).append((path, line))
        # Omsluttende funktion slås kun op for de viste steder; resten tælles.
        entry.callers = [place(*c) if i < MAX_CALLERS else (*c, None) for i, c in enumerate(sorted(callers))]
        entry.tests = [place(*c) if i < MAX_CALLERS else (*c, None) for i, c in enumerate(sorted(tests))]
        for cfam, line, name in by_file.get(sym.path, ()):
            if cfam != fam or not sym.start <= line <= sym.end or name == sym.name:
                continue
            targets_of_call = defined.get((fam, name), [])
            if not targets_of_call:
                continue  # indbygget eller fra en afhængighed
            label = (f"{name} ({targets_of_call[0].path}:{targets_of_call[0].start})" if len(targets_of_call) == 1
                     else f"{name} ({len(targets_of_call)} steder)")
            if label not in entry.callees:
                entry.callees.append(label)
        if sym.path not in shown_imports:
            entry.imports = [i for i in imports.get(sym.path, []) if i][:MAX_IMPORTS]
            shown_imports.add(sym.path)
        entries.append(entry)
    return entries


def _places(rows, limit):
    shown = [f"{safe(p)}:{line}" + (f" i `{safe(encl)}`" if encl else "") for p, line, encl in sorted(rows)[:limit]]
    extra = len(rows) - limit
    return "; ".join(shown) + (f" (+{extra} flere)" if extra > 0 else "")


def render(entries, max_callers=MAX_CALLERS):
    lines = ["# Kodegraf for ændrede funktioner", "",
             "Fra ast-grep og navnebaseret: en kalder med samme navn kan være en anden funktion; kun kald i samme sprog "
             "tæller. Brug grafen til at finde, hvor du skal læse, og verificér i koden.", ""]
    if not entries:
        return "\n".join(lines + ["Ingen ændrede funktioner i understøttede sprog (PHP, TypeScript/JavaScript, Python, Go, Shell, Ruby)."]) + "\n"
    for n, e in enumerate(entries):
        s = e.symbol
        block = [f"## `{safe(s.qualified)}` ({safe(s.kind)}, {safe(s.path)}:{s.start}–{s.end})"]
        if e.definitions > 1:
            block.append(f"- Usikkert: `{safe(s.name)}` er defineret {e.definitions} steder i repoet; kun kaldere i samme fil er vist.")
        block.append(f"- Kaldere ({len(e.callers)}): {_places(e.callers, max_callers) or 'ingen fundet'}")
        if e.tests:
            block.append(f"- Tests ({len(e.tests)}): {_places(e.tests, max_callers)}")
        if e.callees and s.kind in ("function", "method"):
            block.append("- Kalder: " + ", ".join(safe(c) for c in e.callees[:MAX_CALLEES]) + (" …" if len(e.callees) > MAX_CALLEES else ""))
        if e.imports:
            block.append(f"- Imports i {safe(s.path)}: " + "; ".join(safe(i) for i in e.imports))
        if len(lines) + len(block) + 1 > MAX_LINES:
            lines.append(f"(+{len(entries) - n} ændrede funktioner udeladt)")
            break
        lines += block + [""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--ast-grep", dest="binary", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        if not os.access(args.binary, os.X_OK):
            raise RuntimeError(f"ast-grep findes ikke: {args.binary}")
        text = render(build(args.repo, args.base, args.binary))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as err:
        print(f"::warning::Kodegraf ikke lavet: {err}", file=sys.stderr)
        text = f"# Kodegraf\n\nIngen kodegraf for denne PR ({str(err)[:200]}).\n"
    Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

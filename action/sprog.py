#!/usr/bin/env python3
"""Finder sprogene i PR'ens aendrede filer og vaelger opengrep-regelmapper til dem.

  sprog.py --files <aendrede.txt> --rules <opengrep-rules> --configs <ud.txt>

Skriver én regelmappe pr. linje til --configs og udskriver sprogene til rapporten
(fx "PHP, TypeScript"). Kun mapper, der findes, kommer med. java/ vaelges aldrig:
den indeholder den ene regel i opengrep-rules, der er maerket proprietaer.
"""
import argparse
import os
import sys
from pathlib import PurePosixPath

EXTENSIONS = {
    ".php": "php",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".py": "python",
    ".sh": "shell", ".bash": "shell",
    ".go": "go",
    ".rb": "ruby",
    ".html": "html", ".htm": "html",
}
NAMES = {"php": "PHP", "javascript": "JavaScript", "typescript": "TypeScript", "python": "Python",
         "shell": "Shell", "go": "Go", "ruby": "Ruby", "html": "HTML", "dockerfile": "Dockerfile",
         "github-actions": "GitHub Actions"}
RULES = {
    "php": ["php"],
    "javascript": ["javascript"],
    "typescript": ["javascript", "typescript"],  # JavaScript-reglerne gaelder ogsaa TypeScript
    "python": ["python"],
    "shell": ["bash"],
    "go": ["go"],
    "ruby": ["ruby"],
    "html": ["html"],
    "dockerfile": ["dockerfile"],
    "github-actions": ["yaml/github-actions"],
}
ALWAYS = ["generic/secrets"]


def language(path):
    p = PurePosixPath(path)
    name = p.name.lower()
    if p.parts[:2] == (".github", "workflows") and len(p.parts) == 3 and p.suffix in (".yml", ".yaml"):
        return "github-actions"  # GitHub koerer kun filer direkte i mappen
    if p.suffix.lower() in EXTENSIONS:
        return EXTENSIONS[p.suffix.lower()]
    if name.startswith("dockerfile") or name.endswith(".dockerfile"):
        return "dockerfile"
    return None


def detect(files):
    return sorted({lang for lang in map(language, files) if lang})


def rule_dirs(languages, root, any_changes=True):
    wanted = (ALWAYS if any_changes else []) + [d for lang in languages for d in RULES.get(lang, [])]
    return sorted({os.path.join(str(root), d) for d in wanted if os.path.isdir(os.path.join(str(root), d))})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--configs", required=True)
    args = ap.parse_args(argv)
    with open(args.files, errors="replace") as fh:
        files = [line.strip() for line in fh if line.strip()]
    languages = detect(files)
    with open(args.configs, "w") as fh:
        fh.writelines(f"{d}\n" for d in rule_dirs(languages, args.rules, any_changes=bool(files)))
    return ", ".join(NAMES[lang] for lang in languages)


if __name__ == "__main__":
    print(main(sys.argv[1:]))

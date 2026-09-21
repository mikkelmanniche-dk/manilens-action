#!/bin/bash
# Koerer ManiLens' faste tjek og gratis scannere paa PR'ens aendringer.
# Koeres i et job UDEN hemmeligheder, fordi PR-koden kan vaere ondsindet.
#
#   scanners.sh <repo> <base-sha> <bin-mappe> <rapport.md>
#
# Hver scanner kun paa de aendrede filer, hvor det giver mening. Et tjek, der
# fejler eller mangler, stopper aldrig scriptet: rapporten er data til reviewet.
set -uf -o pipefail
IFS=$'\n'

REPO="$(cd "$1" && pwd -P)" BASE="$2" BIN="$(cd "$3" && pwd)"
REPORT="$(cd "$(dirname "$4")" && pwd)/$(basename "$4")"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
RAW="$WORK/raw" LOGS="$WORK/logs"
mkdir -p "$RAW" "$LOGS"
export PATH="$BIN:$PATH"
cd "$REPO" || exit 1

git diff --no-color "$BASE" HEAD > "$WORK/pr.patch" || exit 2
HEAD_SHA="$(git rev-parse HEAD)" || exit 2
changed() {  # changed <glob>... → aendrede/tilfoejede filer der findes i HEAD
  git -c core.quotePath=false diff --name-only --diff-filter=AMR "$BASE" HEAD -- "$@" 2>/dev/null | while read -r f; do
    [ -f "$f" ] && printf '%s\n' "$f"
  done
}
selected() {
  python3 -c 'import json,sys; sys.exit(0 if any(t["tool"] == sys.argv[2] and t["selected"] for t in json.load(open(sys.argv[1]))["tools"]) else 1)' "$PLAN" "$1"
}
have() {
  selected "$1" || return 1
  command -v "$1" >/dev/null 2>&1 && return 0
  echo "$1: ikke installeret; tjekket blev ikke kørt" >> "$WORK/notes"
  return 1
}
scan_exit() {  # accepted exit codes: 0 plus the scanner's findings code
  local status="$1" tool="$2" findings_code="${3:-1}"
  if [ "$status" -ne 0 ] && [ "$status" -ne "$findings_code" ]; then
    echo "$tool: kunne ikke gennemføre tjekket (exit $status)" >> "$WORK/notes"
  fi
}

log() {  # log <navn> <kommando...>: tekst-output + status til rapporten
  local name="$1"; shift
  local out status
  out="$(run_scan "$name" 0 "$@" 2>&1)"; status=$?
  { [ $status -eq 0 ] && echo "OK" || echo "FEJL (exit $status)"; printf '%s\n' "$out"; } > "$LOGS/$name.log"
}

# One plan drives both installation and the evidence we expect to receive.
PLAN="${MANILENS_SCAN_PLAN:-$WORK/plan.json}"
[ -f "$PLAN" ] || python3 "$HERE/scanner_plan.py" "$REPO" "$BASE" "$PLAN" || exit 2
export MANILENS_SCAN_EVENTS="$WORK/events.jsonl"
run_scan() { python3 "$HERE/scanner_runtime.py" "$@"; }
python3 "$HERE/project_checks.py" "$REPO" "$PLAN" "$LOGS" "$MANILENS_SCAN_EVENTS" || exit 2

# ---------------------------------------------------------- scannere
# ---------------------------------------------------------- sprog
# Sprogene i de aendrede filer vaelger opengrep-regelmapperne og staar i rapporten.
changed '*' > "$WORK/changed.txt"
python3 "$HERE/sprog.py" --files "$WORK/changed.txt" --rules "$BIN/opengrep-rules" --configs "$WORK/opengrep-configs" \
  > "$WORK/languages" 2>/dev/null || { echo "sprog: kunne ikke afgøre sprogene; opengrep blev ikke kørt" >> "$WORK/notes"; : > "$WORK/opengrep-configs"; }

# Opengrep: kun nye fund i forhold til base (diff-aware), regler fra opengrep-rules paa en fast commit.
if [ -s "$WORK/opengrep-configs" ] && have opengrep; then
  OPENGREP_ARGS=()
  while read -r dir; do OPENGREP_ARGS+=(--config "$dir"); done < "$WORK/opengrep-configs"
  run_scan opengrep 0 opengrep scan "${OPENGREP_ARGS[@]}" --baseline-commit "$BASE" --json --quiet \
    --timeout 60 --max-target-bytes 1000000 -o "$RAW/opengrep.json" . >"$LOGS/.opengrep.err" 2>&1
  status=$?
  scan_exit "$status" opengrep 0
  # Vis opengreps sidste fejllinje, saa et crash kan diagnosticeres fra rapporten.
  [ "$status" -ne 0 ] && grep -v '^[[:space:]]*$' "$LOGS/.opengrep.err" | tail -1 | cut -c1-200 | sed 's/^/opengrep: /' >> "$WORK/notes"
elif selected opengrep && [ ! -d "$BIN/opengrep-rules" ]; then
  echo "opengrep: regler mangler; tjekket blev ikke kørt" >> "$WORK/notes"
fi

PY="$(changed '*.py')"
if [ -n "$PY" ] && have ruff; then
  # shellcheck disable=SC2086
  run_scan ruff 0,1 ruff check --no-cache --output-format json --select E9,F63,F7,F82,B,S102,S104,S105,S106,S301,S307,S324,S501,S506,S602,S604,S605,S608,S701,PLE --ignore B008,B905 $PY > "$RAW/ruff.json" 2>/dev/null
  scan_exit "$?" ruff
fi

SH="$(changed '*.sh')"
if [ -n "$SH" ] && have shellcheck; then
  # shellcheck disable=SC2086
  run_scan shellcheck 0,1 shellcheck -f json1 -S warning $SH > "$RAW/shellcheck.json" 2>/dev/null
  scan_exit "$?" shellcheck
fi

WF="$(changed ':(glob).github/workflows/*.yml' ':(glob).github/workflows/*.yaml')"
if [ -n "$WF" ] && have actionlint; then
  # shellcheck disable=SC2086
  run_scan actionlint 0,1 actionlint -format '{{json .}}' $WF > "$RAW/actionlint.json" 2>/dev/null
  scan_exit "$?" actionlint
fi
if [ -n "$WF" ] && have zizmor; then
  # Sikkerhed i workflows (injektion, farlige triggere, rettigheder). Repoets egen zizmor-config bruges ikke,
  # og online-tjek er slaaet fra (jobbet har intet token).
  # shellcheck disable=SC2086
  run_scan zizmor 0 zizmor --no-config --no-online-audits --no-exit-codes --persona=regular --format=json $WF \
    > "$RAW/zizmor.json" 2>/dev/null
  scan_exit "$?" zizmor 0
fi

DOCKER="$(changed '*Dockerfile*')"
if [ -n "$DOCKER" ] && have hadolint; then
  # shellcheck disable=SC2086
  run_scan hadolint 0,1 hadolint -f json $DOCKER > "$RAW/hadolint.json" 2>/dev/null
  scan_exit "$?" hadolint
fi

SQL="$(changed '*.sql')"
if [ -n "$SQL" ] && have squawk; then
  # squawk: farlige Postgres-migreringer (laaser tabeller, mister data, mangler index).
  # shellcheck disable=SC2086
  run_scan squawk 0,1 squawk --reporter json -e require-lock-timeout -e require-statement-timeout -e prefer-robust-stmts \
    -e constraint-missing-not-valid -e require-concurrent-index-creation -e prefer-bigint-over-int -e prefer-bigint-over-smallint -e prefer-identity -e prefer-text-field \
    $SQL > "$RAW/squawk.json" 2>/dev/null
  scan_exit "$?" squawk
fi

PHP="$(changed '*.php')"
if [ -n "$PHP" ] && command -v php >/dev/null; then
  : > "$LOGS/php -l.log"
  status=OK
  while read -r f; do
    out="$(run_scan php-l 0 php -l "$f" 2>&1)" || { status="FEJL"; echo "$out" >> "$LOGS/php -l.body"; }
  done <<< "$PHP"
  { echo "$status"; cat "$LOGS/php -l.body" 2>/dev/null; } > "$LOGS/php -l.log"; rm -f "$LOGS/php -l.body"
  if [ -f "$BIN/phpstan.phar" ]; then
    # shellcheck disable=SC2086
    run_scan phpstan 0,1 php -d memory_limit=1G "$BIN/phpstan.phar" analyse --no-progress --no-interaction --level 5 \
      --error-format json $PHP > "$RAW/phpstan.json" 2>/dev/null
    scan_exit "$?" phpstan
  else
    echo "phpstan: ikke installeret; tjekket blev ikke kørt" >> "$WORK/notes"
  fi
fi

HTML="$(changed '*.html' '*.htm')"
if [ -n "$HTML" ] && have html-validate; then
  # Kun markup-fejl, der braekker siden eller tilgaengeligheden — ikke stil-praeferencer.
  # shellcheck disable=SC2086
  run_scan html-validate 0,1 html-validate --config "$HERE/config/html-validate.json" --formatter json $HTML > "$RAW/htmlvalidate.json" 2>/dev/null
  scan_exit "$?" html-validate
fi

CSS="$(changed '*.css' '*.scss')"
if [ -n "$CSS" ] && have stylelint; then
  # Syntaksfejl og ugyldige vaerdier; stylelint skriver JSON-rapporten til stderr.
  # shellcheck disable=SC2086
  run_scan stylelint 0,2 stylelint --config "$HERE/config/stylelint.json" --formatter json --allow-empty-input $CSS \
    > /dev/null 2> "$RAW/stylelint.json"
  scan_exit "$?" stylelint 2
fi

TS="$(changed '*.ts' '*.tsx' '*.mts' ':!supabase/functions/**')"
if [ -z "${MANILENS_SKIP_REPO_CHECKS:-}" ] && [ -n "$TS" ] && [ -f tsconfig.json ] && [ -x node_modules/.bin/tsc ]; then
  # Kun hvis repoet ikke selv har et typecheck-script (saa har det allerede koert).
  if ! grep -q '"typecheck"' package.json 2>/dev/null; then
    run_scan tsc 0 node_modules/.bin/tsc --noEmit --pretty false 2>&1 | head -400 > "$RAW/tsc.txt"
    # Typecheck failure must block even when it refers to an unchanged file.
    type_status="${PIPESTATUS[0]}"
    [ "$type_status" -eq 0 ] && echo OK > "$LOGS/tsc.log" || echo "FEJL (exit $type_status)" > "$LOGS/tsc.log"
  fi
fi

JS="$(changed '*.js' '*.jsx' '*.mjs' '*.cjs' '*.ts' '*.tsx' '*.mts' '*.cts' ':!supabase/functions/**')"
if [ -n "$JS" ] && have oxlint; then
  # Fejl og mistaenkelig kode, ikke stil. Egen config, saa repoets egne oxlint-filer ikke slaar regler fra.
  # shellcheck disable=SC2086
  run_scan oxlint 0,1 oxlint -c "$HERE/config/oxlint.json" --disable-nested-config --format json $JS > "$RAW/oxlint.json" 2>/dev/null
  scan_exit "$?" oxlint
fi

GO="$(changed '*.go')"
if [ -n "$GO" ] && [ -f go.mod ] && command -v go >/dev/null && have golangci-lint; then
  # Kun nye fund i forhold til base. Kraever kompilérbar kode; ellers bliver det en note (scan_report).
  run_scan golangci-lint 0,1 golangci-lint run --no-config --default=none --enable=errcheck,govet,ineffassign,staticcheck,gosec \
    --new-from-rev="$BASE" --timeout=5m --show-stats=false --output.text.path=/dev/null \
    --output.json.path="$RAW/golangci.json" ./... >/dev/null 2>&1
  scan_exit "$?" golangci-lint
elif [ -n "$GO" ] && [ ! -f go.mod ]; then
  echo "golangci-lint: go.mod ligger ikke i roden; Go-tjekket blev ikke kørt" >> "$WORK/notes"
fi

DENO_FILES="$(changed 'supabase/functions/*.ts' 'supabase/functions/**/*.ts')"
if [ -n "$DENO_FILES" ] && have deno; then
  # shellcheck disable=SC2086
  run_scan deno 0,1 deno lint --json --rules-exclude=no-explicit-any,no-import-prefix,require-await $DENO_FILES > "$RAW/denolint.json" 2>/dev/null
  scan_exit "$?" deno-lint
  if [ -z "${MANILENS_SKIP_REPO_CHECKS:-}" ]; then
    # shellcheck disable=SC2086
    log "deno check (Supabase-funktioner)" deno check $DENO_FILES
  fi
fi

if have trivy; then
  # Kun fejlkonfiguration (Dockerfile, Terraform, k8s, CloudFormation); sårbare pakker tager osv-scanner.
  run_scan trivy 0 trivy fs --quiet --scanners misconfig --format json --output "$RAW/trivy.json" . >/dev/null 2>&1
  scan_exit "$?" trivy 0
fi

LOCKS="$(changed 'package-lock.json' '**/package-lock.json' 'pnpm-lock.yaml' 'yarn.lock' 'composer.lock' \
  'requirements*.txt' 'poetry.lock' 'uv.lock' 'go.sum' 'Cargo.lock' 'deno.lock' '**/deno.lock')"
if [ -n "$LOCKS" ] && have osv-scanner; then
  run_scan osv-scanner 0,1 osv-scanner scan source -r --format json . > "$RAW/osv-head.json" 2>/dev/null
  scan_exit "$?" osv-head
  if git worktree add -q "$WORK/base" "$BASE" 2>/dev/null; then
    (cd "$WORK/base" && run_scan osv-scanner 0,1 osv-scanner scan source -r --format json . > "$RAW/osv-base.json" 2>/dev/null)
    scan_exit "$?" osv-base
    git worktree remove --force "$WORK/base" 2>/dev/null
  fi
fi

if have gitleaks; then
  leak_status=0
  run_scan gitleaks 0,1 gitleaks git --no-banner --redact --report-format json --report-path "$RAW/gitleaks-raw.json" \
    --log-opts="$BASE..HEAD" . >/dev/null 2>&1 || leak_status=$?
  python3 - "$RAW/gitleaks-raw.json" "$LOGS/gitleaks.log" "$leak_status" <<'PY'
import json, sys
error = int(sys.argv[3]) not in (0, 1)
try:
    leaks = json.load(open(sys.argv[1]))
    if not isinstance(leaks, list):
        raise ValueError("invalid output")
except (OSError, ValueError):
    leaks = []
    error = True
with open(sys.argv[2], "w") as fh:
    fh.write("FEJL (scanneren kunne ikke gennemføre tjekket)\n" if error
             else "OK\n" if not leaks and sys.argv[3] == "0"
             else f"FEJL ({len(leaks)} mulige hemmeligheder)\n")
    for l in leaks:
        fh.write(f"{l.get('File')}:{l.get('StartLine')} {l.get('RuleID')} (commit {str(l.get('Commit'))[:7]})\n")
PY
fi

rm -f "$LOGS/.opengrep.err"
python3 "$HERE/scan_report.py" --raw "$RAW" --diff "$WORK/pr.patch" --out "$REPORT" --logs "$LOGS" --root "$REPO" \
  --head "$HEAD_SHA" --notes "$WORK/notes" --languages "$WORK/languages" --plan "$PLAN" --events "$MANILENS_SCAN_EVENTS"

# Kodegraf (motor v2.1 punkt 3): kontekst til reviewet ved siden af rapporten; kan aldrig få tjekket til at fejle.
python3 "$HERE/graph.py" --repo "$REPO" --base "$BASE" --ast-grep "$BIN/ast-grep" --out "$(dirname "$REPORT")/graph.md" || true

# Historik (motor v2.1 punkt 5): git log/blame for de ændrede linjer; samme regler som grafen.
python3 "$HERE/historik.py" --repo "$REPO" --base "$BASE" --out "$(dirname "$REPORT")/historik.md" || true

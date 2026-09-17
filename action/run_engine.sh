#!/bin/bash
# Koerer ManiLens-motoren paa én PR og skriver resultatet som JSON.
# Bruges baade af testbanen (bench/run.sh) og af GitHub-workflowen.
#
#   run_engine.sh --src DIR --diff FIL --meta FIL --out FIL \
#                 [--tools FIL] [--previous FIL] [--rules FIL]... [--log FIL]
#
# Claude faar kun Read/Grep/Glob/Task: ingen Bash, ingen skrivning, intet net.
# Exit 0 = gyldigt resultat skrevet. Alt andet er en fejl (og blokerer i CI).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$(cd "${MANILENS_ENGINE:-$HERE/../engine}" && pwd)"
CLAUDE="${MANILENS_CLAUDE:-claude}"
MODEL="${MANILENS_MODEL:-claude-opus-5}"

SRC="" DIFF="" META="" OUT="" TOOLS="" PREVIOUS="" CONTEXT_BASE="" LOG="/dev/stderr"
RULES=("$ENGINE/regler/faelles.md")
while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC="$2" ;;
    --diff) DIFF="$2" ;;
    --meta) META="$2" ;;
    --out) OUT="$2" ;;
    --tools) TOOLS="$2" ;;
    --previous) PREVIOUS="$2" ;;
    --context-base) CONTEXT_BASE="$2" ;;
    --rules) [ -f "$2" ] && RULES+=("$2") ;;
    --log) LOG="$2" ;;
    *) echo "ukendt argument: $1" >&2; exit 64 ;;
  esac
  shift 2
done
for v in SRC DIFF META OUT; do
  [ -n "${!v}" ] || { echo "--$(echo $v | tr A-Z a-z) mangler" >&2; exit 64; }
done

# Filter generated output in local benchmarks as well as in CI.
FILTERED="$(mktemp -d)"
trap 'rm -rf "$FILTERED"' EXIT
FILTER_EXTRA="$SRC/.manilens/ignore"
if [ -n "$CONTEXT_BASE" ]; then
  python3 "$HERE/knowledge.py" --repo "$SRC" --base "$CONTEXT_BASE" --diff "$DIFF" --out "$FILTERED/knowledge.md" --ignore-out "$FILTERED/ignore"
  RULES+=("$FILTERED/knowledge.md")
  FILTER_EXTRA="$FILTERED/ignore"
fi
python3 "$HERE/filter_diff.py" "$DIFF" "$FILTERED/diff.patch" --extra "$FILTER_EXTRA" >> "$LOG" 2>&1
DIFF="$FILTERED/diff.patch"

# Forvalg uden model (triage.py): er der beviseligt ingen kode at reviewe, og ingen åbne fund fra
# tidligere, skrives et godkendt resultat uden at bruge tokens. Fejler forvalget, reviewes som normalt.
TRIAGE="$FILTERED/triage.json"
python3 "$HERE/triage.py" "$DIFF" > "$TRIAGE" 2>>"$LOG" || echo '{"skip": false, "checks": []}' > "$TRIAGE"
if python3 - "$TRIAGE" "${PREVIOUS:-}" "$OUT" "$HERE" <<'PY' >>"$LOG" 2>&1
import json, sys
triage_path, previous, out, here = sys.argv[1:]
sys.path.insert(0, here)
from triage import skipped_result
decision = json.load(open(triage_path))
open_previous = bool(previous) and bool(json.load(open(previous)))
if not decision.get("skip") or open_previous:
    sys.exit(1)
json.dump(skipped_result(decision["reason"], decision["checks"]), open(out, "w"), ensure_ascii=False, indent=2)
print(f"ingen model: {decision['reason']}")
PY
then
  exit 0
fi

ADD_DIRS=(--add-dir "$ENGINE" --add-dir "$(dirname "$DIFF")" --add-dir "$(dirname "$META")")
for f in "$TOOLS" "$PREVIOUS" "${RULES[@]}"; do
  [ -n "$f" ] && ADD_DIRS+=(--add-dir "$(dirname "$f")")
done

# Stierne saettes ind med python, saa specialtegn i stier ikke kan braekke prompten.
PROMPT="$(python3 - "$ENGINE/orchestrator.md" "$DIFF" "$META" "${TOOLS:-(ingen værktøjsoutput)}" \
  "${PREVIOUS:-(ingen tidligere fund)}" "$ENGINE" "${RULES[@]}" <<'PY'
import sys
template, diff, meta, tools, previous, engine, *rules = sys.argv[1:]
text = open(template).read()
import os
# Modelfordeling: "fuld" bruger opus til alle fejl-/sikkerhedsreviewere; "sparsom"
# sparer abonnementets kvote ved at give anden fejl-runde og sikkerhed til sonnet.
tier = os.environ.get("MANILENS_TIER", "sparsom")
models = {
    "fuld":    {"MODEL_FEJL1": "opus", "MODEL_FEJL2": "opus",   "MODEL_SIKKERHED": "opus",   "MODEL_VERIFY": "opus"},
    "sparsom": {"MODEL_FEJL1": "opus", "MODEL_FEJL2": "sonnet", "MODEL_SIKKERHED": "sonnet", "MODEL_VERIFY": "opus"},
}.get(tier)
if models is None:
    sys.exit(f"ukendt MANILENS_TIER: {tier}")
for key, value in {"DIFF": diff, "META": meta, "TOOLS": tools, "PREVIOUS": previous,
                   "ENGINE": engine, "RULES": " og ".join(rules), **models}.items():
    text = text.replace("{{%s}}" % key, value)
print(text)
PY
)"

RAW="$(mktemp)"
# Tomt HOME + kun "user"-indstillinger: PR'ens egen .claude/settings.json (og dermed
# dens hooks, som koerer uden om --disallowedTools) indlaeses aldrig.
CLEAN_HOME="$(mktemp -d)"
trap 'rm -rf "$RAW" "$CLEAN_HOME" "$FILTERED"' EXIT
if [ -n "${CI:-}" ]; then RUN_HOME="$CLEAN_HOME"; else RUN_HOME="$HOME"; fi
STATUS=0
(cd "$SRC" && env -u GITHUB_TOKEN -u GH_TOKEN -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
    -u ANTHROPIC_BASE_URL -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX \
    -u CLAUDE_CODE_USE_FOUNDRY HOME="$RUN_HOME" python3 "$HERE/claude_subscription.py" "$CLAUDE" -p "$PROMPT" \
    --model "$MODEL" \
    --max-turns "${MANILENS_MAX_TURNS:-20}" \
    --setting-sources user \
    --strict-mcp-config \
    "${ADD_DIRS[@]}" \
    --allowedTools "Read,Grep,Glob,Task" \
    --disallowedTools "Bash,Write,Edit,NotebookEdit,WebFetch,WebSearch" \
    --output-format json) > "$RAW" 2>>"$LOG" || STATUS=$?
cp "$RAW" "$LOG.raw.json" 2>/dev/null || true
if [ "$STATUS" -ne 0 ]; then
  echo "Claude-kørslen fejlede (exit $STATUS); råt svar gemt ved loggen" >> "$LOG"
  exit "$STATUS"
fi

python3 - "$RAW" "$OUT" "$HERE" "$TRIAGE" <<'PY' 2>>"$LOG"
import json, re, sys
raw, out, here, triage_path = sys.argv[1:]
sys.path.insert(0, here)
from post_review import validate
run = json.load(open(raw))
if run.get("is_error"):
    sys.exit(f"Claude-kørslen fejlede: {str(run.get('result'))[:300]}")
match = re.search(r"\{.*\}", run.get("result", ""), re.S)
if not match:
    sys.exit("intet JSON-objekt i resultatet")
data = json.loads(match.group(0))
# Advarsler fra forvalget lægges til uden om modellen, så de ikke afhænger af, hvad den svarer.
known = {c.get("name") for c in data.get("pre_merge_checks", []) if isinstance(c, dict)}
extra = [c for c in json.load(open(triage_path)).get("checks", []) if c["name"] not in known]
if isinstance(data.get("pre_merge_checks", []), list):
    data["pre_merge_checks"] = data.get("pre_merge_checks", []) + extra
validate(data)
json.dump(data, open(out, "w"), ensure_ascii=False, indent=2)
print(f"{len(data.get('findings', []))} fund, verdict {data['verdict']}")
PY

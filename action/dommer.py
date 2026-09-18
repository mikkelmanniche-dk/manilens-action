#!/usr/bin/env python3
"""Dommerens grænse (engine/orchestrator.md trin 4 og 5), håndhævet i kode.

  dommer.py <result.json>     # normaliserer filen på stedet

Fund med confidence under MIN_CONFIDENCE flyttes til `rejected` (auditerbart), og verdict genberegnes.
Køres i review-jobbet lige efter motoren, så review-oversigten og GitHub-reviewet altid viser det samme.
Et resultat med andet verdict end approve/request_changes (fx error) røres aldrig.
"""
import json
import sys
from pathlib import Path
from tekster import t

BLOCKING = ("kritisk", "alvorlig")
MIN_CONFIDENCE = 80




def is_confident(finding):
    value = finding.get("confidence")
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= MIN_CONFIDENCE


def filter_confidence(result):
    """Fjern fund under MIN_CONFIDENCE fra 'findings' og flyt dem til 'rejected' (auditerbart)."""
    kept, dropped = [], []
    for f in result.get("findings", []):
        if is_confident(f):
            kept.append(f)
        else:
            value = f.get("confidence")
            reason = (t("below_threshold", value=value, minimum=MIN_CONFIDENCE)
                      if isinstance(value, (int, float)) and not isinstance(value, bool)
                      else t("confidence_missing", minimum=MIN_CONFIDENCE))
            dropped.append({"path": f.get("path"), "line": f.get("line", 0),
                            "title": f.get("title", ""), "reason": reason})
    result["findings"] = kept
    if dropped:
        result["rejected"] = result.get("rejected", []) + dropped


def recompute_verdict(result):
    """Genberegn verdict efter dommerens filtrering, efter orchestratorens regel (trin 5)."""
    blocking_finding = any(f.get("severity") in BLOCKING for f in result.get("findings", []))
    failed_error_check = any(c.get("mode") == "error" and c.get("status") == "fail"
                             for c in result.get("pre_merge_checks", []))
    return "request_changes" if blocking_finding or failed_error_check else "approve"


def normalize(result):
    """Anvend grænsen og genberegn verdict. Idempotent; rører kun gyldige approve/request_changes-resultater."""
    if not isinstance(result, dict) or result.get("verdict") not in ("approve", "request_changes"):
        return result
    filter_confidence(result)
    result["verdict"] = recompute_verdict(result)
    return result


def main(argv):
    if len(argv) != 2:
        print("brug: dommer.py <result.json>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    result = json.loads(path.read_text())
    path.write_text(json.dumps(normalize(result), ensure_ascii=False))
    kept = len(result.get("findings", [])) if isinstance(result, dict) else 0
    print(f"ManiLens-dommer: {kept} fund over grænsen ({MIN_CONFIDENCE})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

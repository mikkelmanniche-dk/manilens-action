#!/usr/bin/env python3
"""Læg review-oversigten op på manilens.mikkelmanniche.dk (v2).

  snapshot_upload.py --snapshot snapshot.json --head <sha> --pr <n>

Henter et GitHub OIDC-token (audience = MANILENS_URL), sender oversigten som gzip+base64 til
POST /api/snapshot og skriver kun `snapshot_url=…` til $GITHUB_OUTPUT. Enhver fejl giver en
advarsel og exit 0, så selve reviewet stadig postes. Tokens skrives aldrig ud.
"""
import argparse
import base64
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import render

MAX_ENCODED = 1_400_000  # brokerens grænse er 1,5 MB for hele kroppen
FINDING_KEYS = ("id", "severity", "category", "title", "body", "path", "line", "confidence", "agent_prompt")
FINDING_LIMITS = {"id": 64, "severity": 32, "category": 64, "title": 500, "body": 20_000, "path": 512, "agent_prompt": 20_000}
# Brokerens grænser (SnapshotValidator). Klippes her, så ét for langt felt ikke giver 422 for hele oversigten.
# Længder er UTF-8-bytes som PHP's strlen, ikke tegn: 500 × "æ" er 1000 bytes.
CHECK_LIMITS = {"name": 200, "explanation": 2000}
COUNT_LIMITS = {"tool": 64, "rule": 128, "severity": 32}
LIST_LIMITS = {"files": 300, "findings": 200, "checks": 50, "excluded": 1000, "scanner_counts": 100}
FILE_KEYS = ("path", "layer", "patch", "added", "removed")
MAX_PATH, MAX_LAYER, MAX_PATCH = 512, 64, 1_000_000
TOO_LARGE = "for stor til oversigten"
TIMEOUT = 20


class UploadError(Exception):
    pass


def clip(text, max_bytes):
    """Højst max_bytes UTF-8-bytes, uden at klippe et tegn i to."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


def whole(value, upper=None):
    """Heltal ≥ 0 (og ≤ upper), ellers None. bool tæller ikke som tal."""
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0 and (upper is None or value <= upper)
    return value if ok else None


def payload(snapshot, scanner_counts=()):
    """Kun de felter brokeren gemmer; summary som tekst (brokerens skema). Ugyldige rækker udelades."""
    summary = snapshot.get("summary")
    base = snapshot.get("base_sha")
    files, too_large = split_files(snapshot.get("files", []))
    excluded = [[clip(str(x[0]), MAX_PATH), clip(str(x[1]), 200)] for x in snapshot.get("excluded") or [] if isinstance(x, (list, tuple)) and len(x) == 2]
    return {
        "schema": 1,
        "head_sha": snapshot["head_sha"],
        "base_sha": base if isinstance(base, str) and re.fullmatch(r"[0-9a-f]{40}", base) else None,
        "verdict": snapshot.get("verdict") if snapshot.get("verdict") in ("approve", "request_changes") else "not_run",
        "summary": summary if isinstance(summary, str) else render.summary({"summary": summary or {}}),
        "files": files[:LIST_LIMITS["files"]],
        "findings": [finding(f) for f in snapshot.get("findings", [])][:LIST_LIMITS["findings"]],
        "checks": [check(c) for c in snapshot.get("checks") or [] if isinstance(c, dict) and c.get("status") in ("pass", "fail", "skip")][:LIST_LIMITS["checks"]],
        "excluded": (excluded + too_large)[:LIST_LIMITS["excluded"]],
        "review": review_usage(snapshot.get("review")),
        "scanner_counts": [{**{k: clip(str(r[k]), n) for k, n in COUNT_LIMITS.items()}, "count": r["count"]} for r in scanner_counts
                           if isinstance(r, dict) and all(k in r for k in COUNT_LIMITS) and whole(r.get("count")) is not None][:LIST_LIMITS["scanner_counts"]],
    }


def split_files(files):
    """Filer brokeren kan gemme, og [sti, grund] for dem, der er for store (lang sti eller patch over 1 MB)."""
    kept, too_large = [], []
    for f in files:
        row = {k: f.get(k) for k in FILE_KEYS}
        path, patch = str(row["path"] or ""), str(row["patch"] or "")
        if len(path.encode("utf-8")) > MAX_PATH or len(patch.encode("utf-8")) > MAX_PATCH:
            too_large.append([clip(path, MAX_PATH), TOO_LARGE])
            continue
        if isinstance(row["layer"], str):
            row["layer"] = clip(row["layer"], MAX_LAYER)
        kept.append(row)
    return kept, too_large


def finding(f):
    out = {}
    for k in FINDING_KEYS:
        value = f.get(k)
        if k == "line":
            value = whole(value)
        elif k == "confidence":
            value = whole(value, 100)
        elif k in FINDING_LIMITS and isinstance(value, str):
            value = clip(value, FINDING_LIMITS[k])
        if value is not None:
            out[k] = value
    return out


def check(c):
    out = {k: clip(str(c.get(k) or ""), n) for k, n in CHECK_LIMITS.items()}
    out["status"] = c["status"]
    if c.get("mode") in ("error", "warning"):
        out["mode"] = c["mode"]
    return out


def review_usage(review):
    """Forbrug som brokeren tager imod, ellers None (så resten af oversigten stadig kommer op)."""
    if not isinstance(review, dict) or review.get("mode") not in ("full", "incremental"):
        return None
    cost, duration = review.get("cost_usd"), review.get("duration_s")
    cost_ok = cost is None or (isinstance(cost, (int, float)) and not isinstance(cost, bool) and 0 <= cost <= 1000)
    duration_ok = duration is None or whole(duration, 86_400) is not None
    return {"mode": review["mode"], "cost_usd": cost, "duration_s": duration} if cost_ok and duration_ok else None


def read_counts(path):
    """tools.md.counts.json fra tjek-jobbet, eller en tom liste. En manglende fil stopper aldrig uploaden."""
    if not path:
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def oidc_token(audience):
    url, request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        raise UploadError("OIDC er ikke tilgængeligt (mangler permissions: id-token: write)")
    req = urllib.request.Request(url + "&audience=" + urllib.parse.quote(audience, safe=""),
                                 headers={"Authorization": "bearer " + request_token})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        data = json.load(res)
    value = data.get("value") if isinstance(data, dict) else None
    if not isinstance(value, str) or value.count(".") != 2:
        raise UploadError("OIDC-udstederen gav intet token")
    return value


def upload(base, token, pr, head, encoded):
    body = json.dumps({"pr": pr, "head_sha": head, "encoding": "gzip+base64", "data": encoded}).encode()
    req = urllib.request.Request(base + "/api/snapshot", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            data = json.load(res)
    except urllib.error.HTTPError as err:
        raise UploadError(f"brokeren svarede HTTP {err.code}") from None
    inner = data.get("data") if isinstance(data, dict) else None
    url = inner.get("url") if isinstance(inner, dict) else None
    if not isinstance(url, str) or not re.fullmatch(re.escape(base) + r"/r/[A-Za-z0-9_-]{22}", url):
        raise UploadError("brokeren gav en ugyldig URL")
    return url


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--scanner-counts", help="tools.md.counts.json fra tjek-jobbet (valgfri)")
    args = ap.parse_args(argv)
    base = os.environ.get("MANILENS_URL", "https://manilens.mikkelmanniche.dk").rstrip("/")
    try:
        with open(args.snapshot) as fh:
            snapshot = json.load(fh)
        if snapshot.get("head_sha") != args.head or not re.fullmatch(r"[0-9a-f]{40}", args.head):
            raise UploadError("oversigten hører ikke til denne commit")
        encoded = base64.b64encode(gzip.compress(json.dumps(payload(snapshot, read_counts(args.scanner_counts)), ensure_ascii=False).encode())).decode()
        if len(encoded) > MAX_ENCODED:
            raise UploadError(f"oversigten er for stor ({len(encoded)} bytes)")
        url = upload(base, oidc_token(base), args.pr, args.head, encoded)
    except (UploadError, OSError, ValueError, KeyError, AttributeError, TypeError) as err:
        print(f"::warning::Review-oversigten kunne ikke gemmes: {err}")
        return 0
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as fh:
            fh.write(f"snapshot_url={url}\n")
    print("review-oversigt gemt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

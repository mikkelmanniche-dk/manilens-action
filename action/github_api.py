"""Lille GitHub-klient uden afhaengigheder (kun standardbiblioteket).

Bruges af post_review.py i GitHub Actions. Tokenet laeses fra miljoeet og
skrives aldrig ud.
"""
import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
RETRIES = 3


class GitHubError(RuntimeError):
    pass


def _token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GitHubError("GITHUB_TOKEN mangler i miljøet")
    return token


def request(method, path, body=None):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "manilens",
    })
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:500]
            if err.code >= 500 and attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise GitHubError(f"{method} {path} → {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            raise GitHubError(f"{method} {path} → netværksfejl: {err.reason}") from err
    raise GitHubError(f"{method} {path} mislykkedes")


def paginate(path):
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        batch = request("GET", f"{path}{sep}per_page=100&page={page}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def graphql(query, variables):
    result = request("POST", "/graphql", {"query": query, "variables": variables})
    if result.get("errors"):
        raise GitHubError(f"GraphQL-fejl: {result['errors']}")
    return result["data"]

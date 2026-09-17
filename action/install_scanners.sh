#!/bin/bash
# Installerer ManiLens' gratis scannere i en mappe — faste versioner, checksum-tjekket.
#
#   install_scanners.sh <bin-mappe>            (Linux x86_64 i CI, macOS arm64 lokalt)
#
# Semgrep og Ruff installeres med pip i en venv i <bin-mappe>/venv.
# Checksummer for trivy/osv-scanner/actionlint/hadolint er fra projekternes egne
# checksum-filer; squawk, phpstan og shellcheck er målt ved første download (14/9-2026).
set -euo pipefail

BIN="$(mkdir -p "$1" && cd "$1" && pwd)"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) OS=linux ;;
  Darwin-arm64) OS=mac ;;
  *) echo "ikke understøttet platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

SEMGREP_VERSION=1.136.0
# Python 3.12-venv har ikke setuptools; semgreps opentelemetry importerer pkg_resources.
SETUPTOOLS_VERSION=80.9.0
RUFF_VERSION=0.16.7
TRIVY_VERSION=0.74.0
OSV_VERSION=2.6.0
ACTIONLINT_VERSION=1.7.12
HADOLINT_VERSION=2.15.1
SQUAWK_VERSION=2.65.0
PHPSTAN_VERSION=2.2.14
SHELLCHECK_VERSION=0.11.0
GITLEAKS_VERSION=8.30.1
HTML_VALIDATE_VERSION=11.15.0
STYLELINT_VERSION=17.15.0

fetch() {  # url sha256 destination
  local tmp; tmp="$(mktemp)"
  curl -sSfL --retry 3 -o "$tmp" "$1"
  echo "$2  $tmp" | shasum -a 256 -c - >/dev/null || { echo "checksum passer ikke: $1" >&2; rm -f "$tmp"; exit 1; }
  mv "$tmp" "$3"
}

gh_release() { echo "https://github.com/$1/releases/download/$2/$3"; }

if [ "$OS" = linux ]; then
  fetch "$(gh_release aquasecurity/trivy v$TRIVY_VERSION trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz)" \
    2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a "$BIN/trivy.tgz"
  fetch "$(gh_release google/osv-scanner v$OSV_VERSION osv-scanner_linux_amd64)" \
    ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108 "$BIN/osv-scanner"
  fetch "$(gh_release rhysd/actionlint v$ACTIONLINT_VERSION actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz)" \
    8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 "$BIN/actionlint.tgz"
  fetch "$(gh_release hadolint/hadolint v$HADOLINT_VERSION hadolint-linux-x86_64)" \
    c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507 "$BIN/hadolint"
  fetch "$(gh_release sbdchd/squawk v$SQUAWK_VERSION squawk-linux-x64)" \
    9b7b2b9529a469647e32c6346e8d5a8a760857cbe2ee94ccb37f60e408babffb "$BIN/squawk"
  fetch "$(gh_release koalaman/shellcheck v$SHELLCHECK_VERSION shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.xz)" \
    8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198 "$BIN/shellcheck.txz"
  fetch "$(gh_release gitleaks/gitleaks v$GITLEAKS_VERSION gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz)" \
    551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb "$BIN/gitleaks.tgz"
  tar -xJf "$BIN/shellcheck.txz" -C "$BIN" --strip-components=1 "shellcheck-v$SHELLCHECK_VERSION/shellcheck"
  tar -xzf "$BIN/gitleaks.tgz" -C "$BIN" gitleaks
else
  fetch "$(gh_release aquasecurity/trivy v$TRIVY_VERSION trivy_${TRIVY_VERSION}_macOS-ARM64.tar.gz)" \
    1caada5e0e2091909357c7525d3aa76f4b660b13821bc143b190c7483e31cc11 "$BIN/trivy.tgz"
  fetch "$(gh_release google/osv-scanner v$OSV_VERSION osv-scanner_darwin_arm64)" \
    98c460dcd37de25819babd757d04542045b6243113e209edcd4d89fedb0256b4 "$BIN/osv-scanner"
  fetch "$(gh_release rhysd/actionlint v$ACTIONLINT_VERSION actionlint_${ACTIONLINT_VERSION}_darwin_arm64.tar.gz)" \
    aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f "$BIN/actionlint.tgz"
  fetch "$(gh_release hadolint/hadolint v$HADOLINT_VERSION hadolint-macos-arm64)" \
    5c09f3213f8e40406abe048233d985eebef336d4a6a20021be47fadb6cf480a2 "$BIN/hadolint"
  fetch "$(gh_release sbdchd/squawk v$SQUAWK_VERSION squawk-darwin-arm64)" \
    05b140108aaa04404ed8a0e600dc83ab5c678cac929e621df6f08eb189bdfe5f "$BIN/squawk"
fi

tar -xzf "$BIN/trivy.tgz" -C "$BIN" trivy
tar -xzf "$BIN/actionlint.tgz" -C "$BIN" actionlint
fetch "$(gh_release phpstan/phpstan $PHPSTAN_VERSION phpstan.phar)" \
  a7d45c01d3bd5aceb2cb9e596a67e50ff9f12b8757a373b93c0761deb8cd77e1 "$BIN/phpstan.phar"
rm -f "$BIN"/*.tgz "$BIN"/*.txz
chmod +x "$BIN"/trivy "$BIN"/osv-scanner "$BIN"/actionlint "$BIN"/hadolint "$BIN"/squawk
[ -f "$BIN/shellcheck" ] && chmod +x "$BIN/shellcheck"

# HTML- og CSS-validering (stylelint fanger CSS-syntaksfejl som en løs "}") (npm, faste versioner, ingen install-scripts)
mkdir -p "$BIN/npm"
npm install --prefix "$BIN/npm" --no-audit --no-fund --ignore-scripts --silent \
  "html-validate@$HTML_VALIDATE_VERSION" "stylelint@$STYLELINT_VERSION"
ln -sf "$BIN/npm/node_modules/.bin/html-validate" "$BIN/html-validate"
ln -sf "$BIN/npm/node_modules/.bin/stylelint" "$BIN/stylelint"

python3 -m venv "$BIN/venv"
"$BIN/venv/bin/pip" install --quiet --disable-pip-version-check \
  "semgrep==$SEMGREP_VERSION" "setuptools==$SETUPTOOLS_VERSION" "ruff==$RUFF_VERSION"
ln -sf "$BIN/venv/bin/semgrep" "$BIN/semgrep"
ln -sf "$BIN/venv/bin/ruff" "$BIN/ruff"

echo "Scannere installeret i $BIN"

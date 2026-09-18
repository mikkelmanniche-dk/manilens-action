#!/bin/bash
# Installerer ManiLens' gratis scannere i en mappe — faste versioner, checksum-tjekket.
#
#   install_scanners.sh <bin-mappe>            (Linux x86_64 i CI, macOS arm64 lokalt)
#
# Checksummer for trivy/osv-scanner/actionlint/hadolint er fra projekternes egne
# checksum-filer; squawk, phpstan og shellcheck er målt ved første download (14/9-2026);
# opengrep, ruff, zizmor, oxlint, golangci-lint og ast-grep er GitHubs digest for release-filen (17/9-2026).
# Opengreps regler hentes med git paa en fast commit (git tjekker indholdet mod SHA'en).
#
# MANILENS_SCANNER_CACHE=<mappe>: downloads genbruges derfra, men kun hvis checksummen passer.
set -euo pipefail

BIN="$(mkdir -p "$1" && cd "$1" && pwd)"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) OS=linux ;;
  Darwin-arm64) OS=mac ;;
  *) echo "ikke understøttet platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

OPENGREP_VERSION=1.30.0
# opengrep/opengrep-rules er arkiveret; licens Commons Clause + LGPL-2.1 (se public/README.md).
OPENGREP_RULES_COMMIT=f1d2b562b414783763fd02a6ed2736eaed622efa
RUFF_VERSION=0.16.7
ZIZMOR_VERSION=1.30.1
OXLINT_VERSION=1.83.0
GOLANGCI_LINT_VERSION=2.13.2
AST_GREP_VERSION=0.45.3  # MIT; kodegrafen (graph.py). Kun binæren ast-grep bruges (sg kolliderer på Linux).
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

CACHE="${MANILENS_SCANNER_CACHE:-}"
[ -z "$CACHE" ] || mkdir -p "$CACHE"

sha_ok() { echo "$1  $2" | shasum -a 256 -c - >/dev/null 2>&1; }

fetch() {  # url sha256 destination
  local tmp; tmp="$(mktemp)"
  if [ -n "$CACHE" ] && [ -f "$CACHE/$2" ] && cp "$CACHE/$2" "$tmp" && sha_ok "$2" "$tmp"; then
    mv "$tmp" "$3"
    return
  fi
  curl -sSfL --retry 3 -o "$tmp" "$1"
  sha_ok "$2" "$tmp" || { echo "checksum passer ikke: $1" >&2; rm -f "$tmp"; exit 1; }
  [ -z "$CACHE" ] || cp "$tmp" "$CACHE/$2"
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
  fetch "$(gh_release opengrep/opengrep v$OPENGREP_VERSION opengrep_manylinux_x86)" \
    35779bdd72e92129c8df2a77f0c55e8c08356801ea92591ef32108d6b28d564c "$BIN/opengrep"
  fetch "$(gh_release astral-sh/ruff $RUFF_VERSION ruff-x86_64-unknown-linux-gnu.tar.gz)" \
    73894c7b7c9a53fd66ed715eb3a1ec65077f316328e377057a98bdb7fcba0326 "$BIN/ruff.tgz"
  fetch "$(gh_release zizmorcore/zizmor v$ZIZMOR_VERSION zizmor-x86_64-unknown-linux-gnu.tar.gz)" \
    e65324f4430c2717591937edcec90ccbefaf14c174f8ec9415e03ca875b46e1a "$BIN/zizmor.tgz"
  fetch "$(gh_release oxc-project/oxc apps_v$OXLINT_VERSION oxlint-x86_64-unknown-linux-gnu.tar.gz)" \
    4995c0f6a55ed8aa611a7c5461dd4d61cdc41ce767d6d92aae665d135b50e5a1 "$BIN/oxlint.tgz"
  fetch "$(gh_release golangci/golangci-lint v$GOLANGCI_LINT_VERSION golangci-lint-$GOLANGCI_LINT_VERSION-linux-amd64.tar.gz)" \
    2277d43b98ec0054280f2ac26b53268bae97682444678a59a657dd565da021d6 "$BIN/golangci-lint.tgz"
  fetch "$(gh_release ast-grep/ast-grep $AST_GREP_VERSION app-x86_64-unknown-linux-gnu.zip)" \
    f8ac830881339d1edee6b2652f54798c0f4da5a827f2db38a08ee31117783ce8 "$BIN/ast-grep.zip"
  tar -xJf "$BIN/shellcheck.txz" -C "$BIN" --strip-components=1 "shellcheck-v$SHELLCHECK_VERSION/shellcheck"
  tar -xzf "$BIN/gitleaks.tgz" -C "$BIN" gitleaks
  tar -xzf "$BIN/ruff.tgz" -C "$BIN" --strip-components=1 ruff-x86_64-unknown-linux-gnu/ruff
  tar -xzf "$BIN/oxlint.tgz" -C "$BIN" && mv "$BIN/oxlint-x86_64-unknown-linux-gnu" "$BIN/oxlint"
  tar -xzf "$BIN/golangci-lint.tgz" -C "$BIN" --strip-components=1 "golangci-lint-$GOLANGCI_LINT_VERSION-linux-amd64/golangci-lint"
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
  fetch "$(gh_release opengrep/opengrep v$OPENGREP_VERSION opengrep_osx_arm64)" \
    0f5bc3dec09d995c61331a4017b856ede508f90d95b018d95f1dc6166be89fdd "$BIN/opengrep"
  fetch "$(gh_release astral-sh/ruff $RUFF_VERSION ruff-aarch64-apple-darwin.tar.gz)" \
    80221a5e0b1ae29262a74496f2ad1380c1ab52b3edd8cee13ec76d8acff406ca "$BIN/ruff.tgz"
  fetch "$(gh_release zizmorcore/zizmor v$ZIZMOR_VERSION zizmor-aarch64-apple-darwin.tar.gz)" \
    e28d22b087f9ebb8d99da6e740d348c930f559961c7c3f12badda54f882195a2 "$BIN/zizmor.tgz"
  fetch "$(gh_release oxc-project/oxc apps_v$OXLINT_VERSION oxlint-aarch64-apple-darwin.tar.gz)" \
    18021b3bc2557e1cb9bb912ff7ae730d877f3e2ba1e06f98c75a979e5fa3a97e "$BIN/oxlint.tgz"
  fetch "$(gh_release golangci/golangci-lint v$GOLANGCI_LINT_VERSION golangci-lint-$GOLANGCI_LINT_VERSION-darwin-arm64.tar.gz)" \
    f4bf83f0b64f055c42b28fc9a38861839f69c096e61c788e72dfaae412011789 "$BIN/golangci-lint.tgz"
  fetch "$(gh_release ast-grep/ast-grep $AST_GREP_VERSION app-aarch64-apple-darwin.zip)" \
    6d2279dea5bea2ad79c66ea93f5fe54ba926e398a8a26de76c56db68fe59eac6 "$BIN/ast-grep.zip"
  tar -xzf "$BIN/ruff.tgz" -C "$BIN" --strip-components=1 ruff-aarch64-apple-darwin/ruff
  tar -xzf "$BIN/oxlint.tgz" -C "$BIN" && mv "$BIN/oxlint-aarch64-apple-darwin" "$BIN/oxlint"
  tar -xzf "$BIN/golangci-lint.tgz" -C "$BIN" --strip-components=1 "golangci-lint-$GOLANGCI_LINT_VERSION-darwin-arm64/golangci-lint"
fi

tar -xzf "$BIN/trivy.tgz" -C "$BIN" trivy
tar -xzf "$BIN/actionlint.tgz" -C "$BIN" actionlint
tar -xzf "$BIN/zizmor.tgz" -C "$BIN" zizmor
unzip -oq "$BIN/ast-grep.zip" ast-grep -d "$BIN"
fetch "$(gh_release phpstan/phpstan $PHPSTAN_VERSION phpstan.phar)" \
  a7d45c01d3bd5aceb2cb9e596a67e50ff9f12b8757a373b93c0761deb8cd77e1 "$BIN/phpstan.phar"
rm -f "$BIN"/*.tgz "$BIN"/*.txz "$BIN"/*.zip
chmod +x "$BIN"/trivy "$BIN"/osv-scanner "$BIN"/actionlint "$BIN"/hadolint "$BIN"/squawk \
  "$BIN"/opengrep "$BIN"/ruff "$BIN"/zizmor "$BIN"/oxlint "$BIN"/golangci-lint "$BIN"/ast-grep

# Opengreps regler: git afviser indhold, der ikke passer til commit-SHA'en.
rm -rf "$BIN/opengrep-rules"
git init -q "$BIN/opengrep-rules"
git -C "$BIN/opengrep-rules" fetch -q --depth 1 https://github.com/opengrep/opengrep-rules.git "$OPENGREP_RULES_COMMIT"
git -C "$BIN/opengrep-rules" -c advice.detachedHead=false checkout -q FETCH_HEAD
[ "$(git -C "$BIN/opengrep-rules" rev-parse HEAD)" = "$OPENGREP_RULES_COMMIT" ] || { echo "opengrep-rules: forkert commit" >&2; exit 1; }
rm -rf "$BIN/opengrep-rules/.git"
[ -f "$BIN/shellcheck" ] && chmod +x "$BIN/shellcheck"

# HTML- og CSS-validering (stylelint fanger CSS-syntaksfejl som en løs "}") (npm, faste versioner, ingen install-scripts)
mkdir -p "$BIN/npm"
npm install --prefix "$BIN/npm" --no-audit --no-fund --ignore-scripts --silent \
  "html-validate@$HTML_VALIDATE_VERSION" "stylelint@$STYLELINT_VERSION"
ln -sf "$BIN/npm/node_modules/.bin/html-validate" "$BIN/html-validate"
ln -sf "$BIN/npm/node_modules/.bin/stylelint" "$BIN/stylelint"

echo "Scannere installeret i $BIN"

#!/usr/bin/env bash
#
# Set the add-on version and open a changelog section for it.
#
#     dev/scripts/bump-version.sh 0.9.9~pre
#     dev/scripts/bump-version.sh 1.0.0 --dry-run
#
# Changes addon.xml, which is the only place the version is written down, and
# puts a new section at the top of CHANGELOG.md listing every commit since the
# last tag as [(hash) subject]. The prose under the heading is yours to write:
# the commits stay underneath it so anyone reading can see how it got here.
#
# It does not commit, tag or push anything.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ADDON_XML="$ROOT/addon.xml"
CHANGELOG="$ROOT/CHANGELOG.md"

VERSION=${1:-}
DRY_RUN=${2:-}

die() { echo "$*" >&2; exit 1; }

[ -n "$VERSION" ] || die "usage: $(basename "$0") <version> [--dry-run]
  release:     1.0.0        tagged v1.0.0
  pre-release: 1.0.0~beta1  tagged p1.0.0-beta1"

# Kodi's own rules, from VALID_ADDON_VERSION_CHARACTERS in AddonVersion.cpp,
# plus '-' which it reads as the separator before a revision. Anything else is
# dropped with 'is not a valid version' and the add-on reads as 0.0.0.
case "$VERSION" in
  *[!a-zA-Z0-9.+_@~-]*) die "'$VERSION' has characters Kodi will not accept" ;;
esac

# '-' makes what follows a revision, and a revision sorts ABOVE no revision:
# 1.0.0-beta1 is *newer* than 1.0.0 to Kodi, so nobody on the beta would ever
# be offered the release. '~' is the one character that sorts below empty.
case "$VERSION" in
  *-*) die "use '~' rather than '-' for a pre-release: ${VERSION//-/\~}" ;;
esac

# The tag that goes with this version, per the two release workflows: '~'
# cannot appear in a git ref, so it becomes '-' there. A version carrying a
# '~' is a pre-release and belongs to prerelease.yml, which does not publish
# to the add-on repository.
if [[ "$VERSION" == *"~"* ]]; then
  TAG="p${VERSION//\~/-}"
  KIND="pre-release (not published to the repository)"
else
  TAG="v$VERSION"
  KIND="release (published to every installed device)"
fi

CURRENT=$(sed -n 's/.*<addon .*version="\([^"]*\)".*/\1/p' "$ADDON_XML")
LAST_TAG=$(git -C "$ROOT" describe --tags --abbrev=0 2>/dev/null || true)

if [ -n "$LAST_TAG" ]; then
  RANGE="$LAST_TAG..HEAD"
  SINCE="since $LAST_TAG"
else
  RANGE=""
  SINCE="from the beginning of the repository"
fi

# --no-merges: a merge commit says nothing a reader wants in a changelog, and
# the commits it brought in are listed on their own.
COMMITS=$(git -C "$ROOT" log --no-merges --abbrev=6 --format='- (%h) %s' $RANGE)
COUNT=$(printf '%s' "$COMMITS" | grep -c '^-' || true)

echo "version:   $CURRENT -> $VERSION"
echo "tag:       $TAG    $KIND"
echo "commits:   $COUNT $SINCE"

if [ "$DRY_RUN" = "--dry-run" ]; then
  echo
  echo "--- CHANGELOG.md would gain ---"
  printf '## %s (%s)\n\n%s\n' "$TAG" "$(date +%Y-%m-%d)" "$COMMITS"
  exit 0
fi

# addon.xml carries version= on the root element and on every <import>, so
# match the line that opens the add-on rather than the attribute alone.
sed -i "s|\(<addon [^>]*id=\"service.fcast.receiver\"[^>]*version=\"\)[^\"]*|\1$VERSION|" "$ADDON_XML"

NEW=$(sed -n 's/.*<addon .*version="\([^"]*\)".*/\1/p' "$ADDON_XML")
[ "$NEW" = "$VERSION" ] || die "addon.xml still says $NEW, the substitution missed"

SECTION=$(printf '## %s (%s)\n\n_Summary goes here._\n\n%s\n' \
  "$TAG" "$(date +%Y-%m-%d)" "$COMMITS")

# Straight after the file's own header, so the newest release reads first.
python3 - "$CHANGELOG" "$SECTION" <<'PYTHON'
import sys

path, section = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()

# Command substitution eats trailing newlines on the way in, and a heading
# that follows a list without a blank line between them is part of the list
# as far as Markdown is concerned.
section = section.rstrip("\n") + "\n\n"

marker = "\n## "
at = text.find(marker)
if at == -1:
    open(path, "a", encoding="utf-8").write("\n" + section)
else:
    open(path, "w", encoding="utf-8").write(
        text[:at + 1] + section + text[at + 1:])
PYTHON

echo
echo "addon.xml and CHANGELOG.md updated. Still to do by hand:"
echo "  1. write the summary in CHANGELOG.md, above the commit list"
echo "  2. update <news> in addon.xml - that is what Kodi shows, not the file"
echo "  3. commit, then: git tag $TAG && git push origin $TAG"

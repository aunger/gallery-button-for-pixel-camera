#!/usr/bin/env bash
# Usage: ./scripts/tag-release.sh <version>  (e.g. ./scripts/tag-release.sh 1.2.3)
# Creates an annotated git tag and pushes it to origin, which triggers the release workflow.
# The tag is the single source of truth for versionName — no file edits required.
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "Error: version argument required." >&2
    echo "Usage: $0 <version>  (e.g. $0 1.2.3)" >&2
    exit 1
fi

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must be semver X.Y.Z, got: '$VERSION'" >&2
    exit 1
fi

TAG="v${VERSION}"

if git rev-parse "$TAG" &>/dev/null; then
    echo "Error: tag $TAG already exists." >&2
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Error: working tree is dirty — commit or stash changes before releasing." >&2
    exit 1
fi

echo "Tagging $TAG on $(git rev-parse --short HEAD) …"
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"
echo "Done. The release workflow will build and publish $TAG."

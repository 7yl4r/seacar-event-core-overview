#!/usr/bin/env bash
# Publish ./output to the `gh-pages` branch (GitHub Pages source) and keep
# main in sync. No GitHub Action needed: run `make deploy`.
set -euo pipefail
cd "$(dirname "$0")"

REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-gh-pages}"
SITE="${SITE:-output}"

wt="/tmp/opencode/gh-pages-wt"
rm -rf "$wt"

echo "==> main"
git add -A
git diff --cached --quiet || git commit -m "site update $(date -u '+%F %T %Z')"
current_branch="$(git branch --show-current)"
echo "==> push ${current_branch} -> ${REMOTE} main"
git push "$REMOTE" "HEAD:refs/heads/main"

echo "==> staging site from ${SITE}/ into a fresh ${BRANCH} repo"
git init -q "$wt"
git -C "$wt" checkout -q -b "$BRANCH"
cp -r "$SITE"/. "$wt"/
touch "$wt/.nojekyll"   # keep GitHub Pages from running Jekyll

author_name="$(git config user.name)"
author_email="$(git config user.email)"
export GIT_AUTHOR_NAME="$author_name" GIT_AUTHOR_EMAIL="$author_email"
export GIT_COMMITTER_NAME="$author_name" GIT_COMMITTER_EMAIL="$author_email"
git -C "$wt" add -A
git -C "$wt" commit -q --allow-empty -m "publish ${SITE} ($(date -u '+%F %T %Z'))"

url="$(git remote get-url "$REMOTE")"
echo "==> push -> ${REMOTE} ${BRANCH} (force: page content replaces history)"
git -C "$wt" push --force "$url" "$BRANCH:refs/heads/$BRANCH"

rm -rf "$wt"
echo "==> done: https://7yl4r.github.io/seacar-event-core-overview/"

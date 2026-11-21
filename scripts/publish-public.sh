#!/bin/sh
# Publishes this private working repo to its public copy, as one new commit with no private history.
#
#     scripts/publish-public.sh <ref> <public clone>
#
# It takes <ref> (a branch, tag or commit) as `git archive` gives it, so nothing uncommitted and no
# history goes along, leaves out what .publicignore lists, and looks through what's left with
# scripts/public-scan.py: tailnet names and addresses, home folders, Gmail addresses, keys, and the
# real values in the deny list, which lives outside the repo ($PUBLIC_DENY, by default
# ~/.config/xrnhub/public-deny.txt, chmod 600). Anything found stops it, and nothing is written.
#
# Then the public clone's files are replaced with the snapshot and committed there as Xernnn, with
# a message naming the date and the private commit. It never pushes: check the commit, then push it.
# A new public clone is one `git init -b main` away.
set -eu

if [ $# -ne 2 ]; then
    echo "usage: scripts/publish-public.sh <ref> <public clone>" >&2
    exit 2
fi
REF=$1
CLONE=$2
NAME=Xernnn
EMAIL=117729152+Xernnn@users.noreply.github.com
DENY=${PUBLIC_DENY:-${XDG_CONFIG_HOME:-$HOME/.config}/xrnhub/public-deny.txt}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)

COMMIT=$(git -C "$REPO" rev-parse --verify --quiet "$REF^{commit}") || {
    echo "No commit called $REF here." >&2
    exit 2
}
if ! git -C "$CLONE" rev-parse --git-dir >/dev/null 2>&1; then
    echo "$CLONE isn't a git repo. Make the public clone first: git init -b main $CLONE" >&2
    exit 2
fi
if [ "$(cd "$CLONE" && pwd -P)" = "$(cd "$REPO" && pwd -P)" ]; then
    echo "$CLONE is this repo, not its public copy." >&2
    exit 2
fi
if [ -n "$(git -C "$CLONE" status --porcelain)" ]; then
    echo "$CLONE has changes of its own; commit or drop them first." >&2
    exit 2
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
TREE=$WORK/tree
mkdir "$TREE"

echo "Taking $REF ($COMMIT)..."
git -C "$REPO" archive --format=tar "$COMMIT" | tar -x -C "$TREE"

# One path or shell pattern a line, from the top of the repo; # starts a comment.
if [ -f "$TREE/.publicignore" ]; then
    sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' "$TREE/.publicignore" | while IFS= read -r pattern; do
        case $pattern in
            /* | *..*) echo ".publicignore: $pattern points outside the repo" >&2; exit 1 ;;
        esac
        (cd "$TREE" && for path in $pattern; do
            if [ -e "$path" ] || [ -L "$path" ]; then
                rm -rf -- "$path"
                echo "  left out: $path"
            fi
        done)
    done
fi

echo "Looking through it..."
if ! python3 -I "$HERE/public-scan.py" --deny "$DENY" "$TREE"; then
    echo "Not published: the snapshot holds something that must stay private (above)." >&2
    exit 1
fi

echo "Committing it to $CLONE..."
git -C "$CLONE" ls-files -z | (cd "$CLONE" && xargs -0 -r rm -f --)
find "$CLONE" -mindepth 1 -depth -type d -empty ! -path "$CLONE/.git" ! -path "$CLONE/.git/*" -delete
(cd "$TREE" && tar -cf - .) | tar -x -C "$CLONE"
git -C "$CLONE" add -A
if git -C "$CLONE" diff --cached --quiet; then
    echo "Nothing changed since the last snapshot; no commit."
    exit 0
fi
DAY=$(git -C "$REPO" show -s --format=%cs "$COMMIT")
SHORT=$(git -C "$REPO" rev-parse --short=12 "$COMMIT")
GIT_AUTHOR_NAME=$NAME GIT_AUTHOR_EMAIL=$EMAIL GIT_COMMITTER_NAME=$NAME GIT_COMMITTER_EMAIL=$EMAIL \
    git -C "$CLONE" -c commit.gpgsign=false commit --quiet --no-verify \
    -m "Snapshot of the working repo as of $DAY" \
    -m "Published from the private working repo's $SHORT by scripts/publish-public.sh."
git -C "$CLONE" log -1 --format='Committed %h by %an <%ae>: %s'
echo "Check it, then push it: git -C $CLONE push"

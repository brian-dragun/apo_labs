#!/usr/bin/env bash
# restore-lab.sh — reset lab files to the original workshop code, straight from git
# ===============================================================================
# Usage (from the repo root):
#   bash restore-lab.sh <lab1|lab2|lab3|shared|all> [file] [--from <ref>] [--dry-run] [--yes]
#
# Examples:
#   bash restore-lab.sh lab1 --dry-run                  # show what differs, change nothing
#   bash restore-lab.sh lab1 gate_starter.py            # restore one file
#   bash restore-lab.sh lab2                            # restore every original lab2 file
#   bash restore-lab.sh lab1 --from upstream/main       # restore the instructor's LATEST version
#   bash restore-lab.sh all --yes                       # restore everything, no prompt
#
# Source of the originals:
#   Default is the tag 'workshop-original' (the instructor's code as of Day 1, pushed to
#   origin, so it survives even if the upstream repo disappears). --from takes any git ref.
#
# Safety:
#   - Lists the files that will be overwritten and asks before changing anything.
#   - Copies your current version of each file to .restore_snapshots/<timestamp>/ first,
#     so a restore can itself be undone (cp the snapshot back).
#   - Only touches files that exist in the source ref. Files you added are never deleted.
#   - Writes the working tree only; your commits and staged changes are not touched.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOT_ROOT="$REPO_DIR/.restore_snapshots"
DEFAULT_REF="workshop-original"
VALID_TARGETS=(lab1 lab2 lab3 shared)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info() { echo -e "${CYAN}[restore]${RESET} $*"; }
ok()   { echo -e "${GREEN}[restore] ✓${RESET} $*"; }
warn() { echo -e "${YELLOW}[restore] ⚠${RESET} $*"; }
die()  { echo -e "${RED}[restore] ✗${RESET} $*" >&2; exit 1; }

usage() { sed -n '4,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

main() {
    local target="" file="" ref="$DEFAULT_REF" dry_run=false assume_yes=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --from)    [[ $# -ge 2 ]] || die "--from needs a git ref (e.g. upstream/main)"
                       ref="$2"; shift ;;
            --dry-run) dry_run=true ;;
            --yes|-y)  assume_yes=true ;;
            -h|--help) usage; exit 0 ;;
            -*)        usage; die "Unknown option: $1" ;;
            *)         if [[ -z "$target" ]]; then target="$1"
                       elif [[ -z "$file" ]]; then file="${1#./}"
                       else usage; die "Too many arguments"; fi ;;
        esac
        shift
    done
    [[ -n "$target" ]] || { usage; exit 1; }

    cd "$REPO_DIR"
    git rev-parse --verify --quiet "$ref^{commit}" >/dev/null \
        || die "Git ref '$ref' not found. Fetch it first: git fetch origin --tags  (or: git fetch upstream)"

    local paths=()
    if [[ "$target" == "all" ]]; then
        [[ -z "$file" ]] || die "A single file can only be restored from one lab, not 'all'"
        paths=("${VALID_TARGETS[@]}")
    else
        [[ " ${VALID_TARGETS[*]} " == *" $target "* ]] || die "Unknown target '$target' (use: ${VALID_TARGETS[*]} all)"
        file="${file#"$target"/}"   # accept both 'gate_starter.py' and 'lab1/gate_starter.py'
        if [[ -n "$file" ]]; then
            git cat-file -e "$ref:$target/$file" 2>/dev/null \
                || die "No original '$target/$file' in $ref (list them: git ls-tree -r --name-only $ref $target)"
            paths=("$target/$file")
        else
            paths=("$target")
        fi
    fi

    # Files that exist in the ref and differ in the working tree:
    # M = modified (content or mode), D = deleted locally, T = type changed.
    # Files you added (A) are excluded, so they are never touched.
    local changed=()
    mapfile -t changed < <(git diff --name-only --diff-filter=MDT "$ref" -- "${paths[@]}")

    if [[ ${#changed[@]} -eq 0 ]]; then
        ok "Already matches $ref — nothing to restore"
        exit 0
    fi

    local rel
    info "These ${#changed[@]} file(s) differ from $ref and would be restored:"
    for rel in "${changed[@]}"; do
        if [[ -e "$rel" ]]; then echo "    modified  $rel"; else echo "    missing   $rel"; fi
    done
    if $dry_run; then
        info "Dry run — nothing changed. See exact changes with: git diff $ref -- <path>"
        exit 0
    fi
    if ! $assume_yes; then
        read -r -p "Overwrite with the versions from $ref? [y/N] " reply
        [[ "$reply" =~ ^[Yy]$ ]] || { warn "Cancelled — nothing changed"; exit 0; }
    fi

    local snapshot
    snapshot="$SNAPSHOT_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
    for rel in "${changed[@]}"; do
        if [[ -e "$rel" ]]; then
            mkdir -p "$snapshot/$(dirname "$rel")"
            cp -p "$rel" "$snapshot/$rel"
        fi
    done
    # Working tree only: the index (staged changes) and your commits stay as they are
    git restore --source="$ref" --worktree -- "${changed[@]}"

    ok "Restored ${#changed[@]} file(s) from $ref"
    if [[ -d "$snapshot" ]]; then
        ok "Your previous versions are saved in ${snapshot#"$REPO_DIR"/}/"
    fi
}

main "$@"

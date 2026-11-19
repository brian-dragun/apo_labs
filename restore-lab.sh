#!/usr/bin/env bash
# restore-lab.sh — reset lab files to the original workshop code in backup_labs/
# ===============================================================================
# Usage (from the repo root):
#   bash restore-lab.sh <lab1|lab2|lab3|shared|all> [file] [--dry-run] [--yes]
#
# Examples:
#   bash restore-lab.sh lab1 --dry-run                  # show what differs, change nothing
#   bash restore-lab.sh lab1 gate_starter.py            # restore one file
#   bash restore-lab.sh lab2                            # restore every original lab2 file
#   bash restore-lab.sh all --yes                       # restore everything, no prompt
#
# Safety:
#   - Lists the files that will be overwritten and asks before changing anything.
#   - Copies your current version of each file to .restore_snapshots/<timestamp>/ first,
#     so a restore can itself be undone (cp the snapshot back).
#   - Only overwrites files that exist in backup_labs/. Files you added are never deleted.
#   - backup_labs/ itself is never modified.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$REPO_DIR/backup_labs"
SNAPSHOT_ROOT="$REPO_DIR/.restore_snapshots"
VALID_TARGETS=(lab1 lab2 lab3 shared)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info() { echo -e "${CYAN}[restore]${RESET} $*"; }
ok()   { echo -e "${GREEN}[restore] ✓${RESET} $*"; }
warn() { echo -e "${YELLOW}[restore] ⚠${RESET} $*"; }
die()  { echo -e "${RED}[restore] ✗${RESET} $*" >&2; exit 1; }

usage() { sed -n '4,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# Print repo-relative paths of original files under the given targets (optionally one file)
list_originals() {
    local file="$1"; shift
    local target
    for target in "$@"; do
        if [[ -n "$file" ]]; then
            echo "$target/$file"   # existence already validated in main()
        else
            (cd "$BACKUP_DIR" && find "$target" -type f \
                -not -path "*/__pycache__/*" -not -name "*:Zone.Identifier" | sort)
        fi
    done
}

main() {
    local target="" file="" dry_run=false assume_yes=false arg
    for arg in "$@"; do
        case "$arg" in
            --dry-run) dry_run=true ;;
            --yes|-y)  assume_yes=true ;;
            -h|--help) usage; exit 0 ;;
            -*)        usage; die "Unknown option: $arg" ;;
            *)         if [[ -z "$target" ]]; then target="$arg"
                       elif [[ -z "$file" ]]; then file="${arg#./}"
                       else usage; die "Too many arguments"; fi ;;
        esac
    done
    [[ -n "$target" ]] || { usage; exit 1; }
    [[ -d "$BACKUP_DIR" ]] || die "backup_labs/ not found — nothing to restore from"

    local targets=()
    if [[ "$target" == "all" ]]; then
        [[ -z "$file" ]] || die "A single file can only be restored from one lab, not 'all'"
        targets=("${VALID_TARGETS[@]}")
    else
        [[ " ${VALID_TARGETS[*]} " == *" $target "* ]] || die "Unknown target '$target' (use: ${VALID_TARGETS[*]} all)"
        file="${file#"$target"/}"   # accept both 'gate_starter.py' and 'lab1/gate_starter.py'
        if [[ -n "$file" && ! -f "$BACKUP_DIR/$target/$file" ]]; then
            die "No original '$target/$file' in backup_labs/ (list them: ls -R backup_labs/$target)"
        fi
        targets=("$target")
    fi

    local changed=() rel
    while IFS= read -r rel; do
        if ! cmp -s "$BACKUP_DIR/$rel" "$REPO_DIR/$rel" 2>/dev/null; then changed+=("$rel"); fi
    done < <(list_originals "$file" "${targets[@]}")

    if [[ ${#changed[@]} -eq 0 ]]; then
        ok "Already matches the original — nothing to restore"
        exit 0
    fi

    info "These ${#changed[@]} file(s) differ from the original and would be restored:"
    for rel in "${changed[@]}"; do
        if [[ -e "$REPO_DIR/$rel" ]]; then echo "    modified  $rel"; else echo "    missing   $rel"; fi
    done
    if $dry_run; then
        info "Dry run — nothing changed. Diff one file with: diff backup_labs/<path> <path>"
        exit 0
    fi
    if ! $assume_yes; then
        read -r -p "Overwrite with the original versions? [y/N] " reply
        [[ "$reply" =~ ^[Yy]$ ]] || { warn "Cancelled — nothing changed"; exit 0; }
    fi

    local snapshot
    snapshot="$SNAPSHOT_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
    for rel in "${changed[@]}"; do
        if [[ -e "$REPO_DIR/$rel" ]]; then
            mkdir -p "$snapshot/$(dirname "$rel")"
            cp -p "$REPO_DIR/$rel" "$snapshot/$rel"
        fi
        mkdir -p "$REPO_DIR/$(dirname "$rel")"
        cp "$BACKUP_DIR/$rel" "$REPO_DIR/$rel"
    done
    ok "Restored ${#changed[@]} file(s) from backup_labs/"
    if [[ -d "$snapshot" ]]; then
        ok "Your previous versions are saved in ${snapshot#"$REPO_DIR"/}/"
    fi
}

main "$@"

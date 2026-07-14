#!/usr/bin/env bash
#
# bench_transfer.sh
#
# Benchmarks rsync transfer time (laptop -> HPC).
#
# TWO MODES:
#
# 1. NODE_COUNT mode (edit the variable below, then run with no args):
#    Selects the first NODE_COUNT folders (sorted) matching node_* inside
#    EDGE_INBOX_DIR and syncs only those - e.g. NODE_COUNT=10 sends
#    node_000..node_009. Change the number, re-run, type TOTP once per
#    run. This is the manual scaling-curve workflow: no MFA-window
#    dependency, no batch loop, you're present for every prompt.
#
#      NODE_COUNT=10 ./bench_transfer.sh
#
# 2. Positional-arg mode (original behaviour, unchanged):
#    Point at any local dir (e.g. a collected_configs timestamp folder)
#    and a remote dest, syncs everything in it.
#
#      ./bench_transfer.sh <local_dir> <remote_dest_dir>
#      ./bench_transfer.sh "/d/Projects/.../collected_configs/20260615_151800" ~/bench/single
#
# Note: run from Git Bash/WSL, so Windows paths like D:\... need to be
# written as /d/Projects/... (Git Bash) or /mnt/d/Projects/... (WSL).
#
# Requires: rsync, ssh key already set up as below. Edit SSH_KEY / REMOTE
# if yours differ.

set -euo pipefail

SSH_KEY="C:/Users/amrit/.ssh/hpc_dawn_key"
REMOTE_HOST="dn-kuma1@login-dawn.hpc.cam.ac.uk"

# --- NODE_COUNT mode config - only used if NODE_COUNT env var is set ---
EDGE_INBOX_DIR="/d/Projects/Isambard-HPC-Edge/hpc-smart-camera-workflow/edge_data_inbox"
REMOTE_BASE_DIR="/home/dn-kuma1/Smart-Transport-SUMO/edge_data_inbox"

if [ -n "${NODE_COUNT:-}" ]; then
    # --- NODE_COUNT mode ---
    if [ ! -d "$EDGE_INBOX_DIR" ]; then
        echo "Error: EDGE_INBOX_DIR '$EDGE_INBOX_DIR' does not exist"
        exit 1
    fi
 
    mapfile -t ALL_NODES < <(find "$EDGE_INBOX_DIR" -mindepth 1 -maxdepth 1 -type d -name "node_*" | sort)
    TOTAL_AVAILABLE=${#ALL_NODES[@]}
 
    if [ "$NODE_COUNT" -gt "$TOTAL_AVAILABLE" ]; then
        echo "Error: NODE_COUNT=$NODE_COUNT but only $TOTAL_AVAILABLE node folders found in $EDGE_INBOX_DIR"
        exit 1
    fi
 
    # Build the selected slice: node_000 .. node_(NODE_COUNT-1)
    SELECTED_NODES=("${ALL_NODES[@]:0:$NODE_COUNT}")
    FIRST_NODE=$(basename "${SELECTED_NODES[0]}")
    LAST_NODE=$(basename "${SELECTED_NODES[-1]}")
 
    REMOTE_DIR="${REMOTE_BASE_DIR}/n${NODE_COUNT}"
    RUN_LABEL="NODE_COUNT=$NODE_COUNT ($FIRST_NODE .. $LAST_NODE)"
 
    # Write the list of selected folders to a temp file for rsync
    # --files-from, so we transfer exactly the selected subset without
    # first copying it into a staging dir (no extra local disk I/O
    # before the timer starts, and no leftover staging folders to clean up).
    FILES_FROM=$(mktemp)
    for d in "${SELECTED_NODES[@]}"; do
        basename "$d"
    done > "$FILES_FROM"
 
    LOCAL_DIR="$EDGE_INBOX_DIR"
    # -r is required here even though -a is passed below: --files-from
    # disables -a's implied recursion into listed directories, so without
    # -r rsync creates the empty dirs but transfers none of their contents.
    RSYNC_EXTRA_ARGS=(-r --files-from="$FILES_FROM")
 
    # --- Pre-transfer stats (only over the selected subset) ---
    FOLDER_COUNT=$NODE_COUNT
    FILE_COUNT=0
    TOTAL_BYTES=0
    for d in "${SELECTED_NODES[@]}"; do
        FILE_COUNT=$((FILE_COUNT + $(find "$d" -type f | wc -l)))
        TOTAL_BYTES=$((TOTAL_BYTES + $(du -sb "$d" | cut -f1)))
    done
    TOTAL_HUMAN=$(numfmt --to=iec-i --suffix=B "$TOTAL_BYTES" 2>/dev/null || echo "${TOTAL_BYTES} bytes")
 
else
    # --- Positional-arg mode (original behaviour) ---
    if [ $# -ne 2 ]; then
        echo "Usage: $0 <local_dir> <remote_dest_dir>"
        echo "   or: NODE_COUNT=<N> $0   (selects first N node_* folders from EDGE_INBOX_DIR)"
        exit 1
    fi
 
    LOCAL_DIR="$1"
    REMOTE_DIR="$2"
    RUN_LABEL="$LOCAL_DIR"
    RSYNC_EXTRA_ARGS=()
 
    if [ ! -d "$LOCAL_DIR" ]; then
        echo "Error: local dir '$LOCAL_DIR' does not exist"
        exit 1
    fi
 
    # --- Gather pre-transfer stats ---
    # Count subfolders (submission events) vs raw files separately -
    # folder count is the meaningful unit here (1 folder = 1 edge device
    # submission), file count is just folders x 4.
    FOLDER_COUNT=$(find "$LOCAL_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
    FILE_COUNT=$(find "$LOCAL_DIR" -type f | wc -l)
    TOTAL_BYTES=$(du -sb "$LOCAL_DIR" | cut -f1)
    TOTAL_HUMAN=$(du -sh "$LOCAL_DIR" | cut -f1)
 
    # If pointed directly at a single timestamp folder (no subfolders, just
    # the 4 files), FOLDER_COUNT will be 0 - treat that as 1 submission.
    if [ "$FOLDER_COUNT" -eq 0 ]; then
        FOLDER_COUNT=1
    fi
fi
 
echo "=================================================="
echo "  Transfer benchmark: $RUN_LABEL -> $REMOTE_HOST:$REMOTE_DIR"
echo "=================================================="
echo "  Submission folders:  $FOLDER_COUNT"
echo "  Total files:         $FILE_COUNT"
echo "  Total size:          $TOTAL_HUMAN ($TOTAL_BYTES bytes)"
echo "--------------------------------------------------"
 
# Ensure remote dir exists first (not timed - setup, not transfer)
ssh -i "$SSH_KEY" "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR'"
 
# --- Timed transfer ---
# No -z (compression) by default: these are small text/XML/JSON files,
# and we want to see real transfer + per-file SSH overhead, not
# compression-skewed numbers. Add -z yourself to compare if curious.
#
# We keep the wall-clock START/END timer (ELAPSED) for reference, but it
# includes TOTP entry time whenever a fresh SSH session is required - it
# is NOT a reliable network measurement on its own. Instead we parse
# rsync's own self-reported transfer rate from its --stats output
# ("sent X bytes ... Y bytes/sec"), which is timed internally by rsync
# AFTER the SSH session is already authenticated, so it excludes TOTP
# entry entirely. RSYNC_REPORTED_MBPS is the number to trust; ELAPSED /
# THROUGHPUT_MBPS (wall-clock derived) is kept only to show, by
# comparison, how much time TOTP entry is adding on a given run.
START_TIME=$(date +%s.%N)
 
RSYNC_OUTPUT=$(rsync -av --stats \
    -e "ssh -i $SSH_KEY" \
    "${RSYNC_EXTRA_ARGS[@]}" \
    "$LOCAL_DIR"/ "$REMOTE_HOST:$REMOTE_DIR"/ | tee /dev/stderr)
 
END_TIME=$(date +%s.%N)
# awk instead of bc - bc isn't bundled with Git Bash by default, awk is.
ELAPSED=$(awk -v a="$START_TIME" -v b="$END_TIME" 'BEGIN { printf "%.4f", b - a }')
 
# Parse rsync's self-reported rate from a line like:
#   sent 855,990 bytes  received 55 bytes  39,816.05 bytes/sec
RSYNC_BYTES_PER_SEC=$(echo "$RSYNC_OUTPUT" | grep -oP '[\d,]+\.\d+(?= bytes/sec)' | tr -d ',')
if [ -n "${RSYNC_BYTES_PER_SEC:-}" ]; then
    RSYNC_REPORTED_MBPS=$(awk -v bps="$RSYNC_BYTES_PER_SEC" 'BEGIN { printf "%.4f", bps / 1048576 }')
else
    RSYNC_REPORTED_MBPS="N/A"
fi
 
# Clean up temp files-from list if NODE_COUNT mode was used
[ -n "${FILES_FROM:-}" ] && rm -f "$FILES_FROM"
 
# --- Results ---
THROUGHPUT_MBPS=$(awk -v bytes="$TOTAL_BYTES" -v t="$ELAPSED" 'BEGIN { if (t > 0) printf "%.4f", (bytes / 1048576) / t; else print "N/A" }')
PER_FOLDER=$(awk -v t="$ELAPSED" -v n="$FOLDER_COUNT" 'BEGIN { if (n > 0) printf "%.4f", t / n; else print "N/A" }')
 
echo "--------------------------------------------------"
echo "  RESULTS"
echo "--------------------------------------------------"
echo "  Submission folders:      $FOLDER_COUNT"
echo "  Total files:             $FILE_COUNT"
echo "  Total size:              $TOTAL_HUMAN"
echo "  Wall-clock elapsed:      ${ELAPSED}s  (includes TOTP entry - not a clean network number)"
echo "  Wall-clock throughput:   ${THROUGHPUT_MBPS} MB/s  (includes TOTP entry)"
echo "  rsync-reported throughput: ${RSYNC_REPORTED_MBPS} MB/s  (excludes TOTP/SSH setup - TRUST THIS ONE)"
echo "  Avg time/submission:     ${PER_FOLDER}s  (wall-clock, includes TOTP entry)"
if [ "$FILE_COUNT" -gt 0 ]; then
    PER_FILE=$(awk -v t="$ELAPSED" -v n="$FILE_COUNT" 'BEGIN { printf "%.4f", t / n }')
    echo "  Avg time/file:            ${PER_FILE}s  (wall-clock, includes TOTP entry)"
fi
echo "=================================================="
#!/bin/bash
#SBATCH -J mc_aggregate
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --mem=8G
#SBATCH --gres=gpu:1
#SBATCH --time=01:00:00
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_aggregate_%j.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_aggregate_%j.err

set -euo pipefail

# ── must match mc_array.sh ────────────────────────────────────────────────────
CHECKPOINT_STEP=3600    # seconds between checkpoints
N_CHECKPOINTS=24        # total number of checkpoints
WINDOW=7200             # prediction horizon (seconds)
INTERVAL=900            # edgeData aggregation interval (seconds)
N_SEEDS=2               # seeds used in the array job
SIM_START=0

PROJECT_ROOT="$HOME/Smart-Transport-SUMO"

module purge
module load rhel9/default-dawn

echo "=== MC aggregate: $(date) ==="
echo "Checkpoints: $N_CHECKPOINTS × ${CHECKPOINT_STEP}s   window: ${WINDOW}s   seeds: $N_SEEDS"

FAILED=()

for (( ci=0; ci<N_CHECKPOINTS; ci++ )); do
    CHECKPOINT=$(( SIM_START + ci * CHECKPOINT_STEP ))

    # Don't try to aggregate a window that started past end-of-sim
    if [ "$CHECKPOINT" -ge 86400 ]; then
        continue
    fi

    CKPT_TAG=$(printf 'ckpt_%05d' "$CHECKPOINT")
    CKPT_DIR="$PROJECT_ROOT/mc_results/${CKPT_TAG}"

    # Skip silently if the directory doesn't exist (array task may have been
    # skipped or failed entirely for this checkpoint)
    if [ ! -d "$CKPT_DIR" ]; then
        echo "[SKIP] $CKPT_TAG — directory not found"
        continue
    fi

    # Count how many run dirs actually produced edgedata
    N_FOUND=$(find "$CKPT_DIR" -name "edgedata_mc.xml" 2>/dev/null | wc -l)
    if [ "$N_FOUND" -eq 0 ]; then
        echo "[SKIP] $CKPT_TAG — no edgedata_mc.xml files found"
        FAILED+=("$CKPT_TAG")
        continue
    fi

    echo ""
    echo "─── Aggregating $CKPT_TAG  ($N_FOUND / $N_SEEDS runs found) ───"

    python3 "$PROJECT_ROOT/scripts/montecarlo_predict_24h.py" \
        --sumocfg    "$PROJECT_ROOT/sumo/sumo_city.sumocfg" \
        --checkpoint "$CHECKPOINT" \
        --window     "$WINDOW" \
        --interval   "$INTERVAL" \
        --reps       "$N_SEEDS" \
        --seed-start 1 \
        --output     "$CKPT_DIR" \
        --aggregate-only \
    && echo "[OK]   $CKPT_TAG" \
    || { echo "[FAIL] $CKPT_TAG"; FAILED+=("$CKPT_TAG"); }
done

echo ""
echo "=== Aggregation complete: $(date) ==="

if [ ${#FAILED[@]} -gt 0 ]; then
    echo "FAILED checkpoints (${#FAILED[@]}):"
    printf '  %s\n' "${FAILED[@]}"
    exit 1
else
    echo "All checkpoints aggregated successfully."
fi

# ── optional: stitch all per-checkpoint mc_run_meta.json into one summary ────
echo ""
echo "Writing combined JSON summary → $PROJECT_ROOT/mc_results/all_checkpoints_meta.json"

python3 - <<'PYEOF'
import json, pathlib, os

root = pathlib.Path(os.environ["HOME"]) / "Smart-Transport-SUMO" / "mc_results"
records = []
for ckpt_dir in sorted(root.glob("ckpt_*")):
    meta_path = ckpt_dir / "mc_run_meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            data = json.load(f)
        data["ckpt_dir"] = ckpt_dir.name
        records.append(data)

out = root / "all_checkpoints_meta.json"
out.write_text(json.dumps(records, indent=2))
print(f"  wrote {len(records)} checkpoint entries → {out}")
PYEOF

#!/bin/bash
#SBATCH -J mc_bn_agg_24h
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --exclusive
#SBATCH --time=01:00:00
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_%j.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_%j.err

set -euo pipefail

# ── redirect into per-job subdirectory ───────────────────────────────────────
LOG_DIR="$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_JOB_ID}"
mkdir -p "$LOG_DIR"
exec > "$LOG_DIR/mc_${SLURM_JOB_ID}.out" \
     2>"$LOG_DIR/mc_${SLURM_JOB_ID}.err"

# ── must match mc_array_bottleneck_24h.sh ────────────────────────────────────
N_SEEDS=10
CHECKPOINT_STEP=3600
N_CHECKPOINTS=24
WINDOW=7200
INTERVAL=900
SIM_START=0

PROJECT_ROOT="$HOME/Smart-Transport-SUMO"

module purge
module load rhel9/default-dawn

echo "=== Bottleneck 24h MC aggregate: $(date) ==="
echo "Checkpoints: $N_CHECKPOINTS × ${CHECKPOINT_STEP}s   window: ${WINDOW}s   seeds: $N_SEEDS"
echo "Bottleneck windows: 07:00-09:00 (AM) and 17:00-19:00 (PM)"

FAILED=()

for (( ci=0; ci<N_CHECKPOINTS; ci++ )); do
    CHECKPOINT=$(( SIM_START + ci * CHECKPOINT_STEP ))

    if [ "$CHECKPOINT" -ge 86400 ]; then
        continue
    fi

    CKPT_TAG=$(printf 'ckpt_bn_%05d' "$CHECKPOINT")
    CKPT_DIR="$PROJECT_ROOT/mc_results/${CKPT_TAG}"

    if [ ! -d "$CKPT_DIR" ]; then
        echo "[SKIP] $CKPT_TAG — directory not found"
        continue
    fi

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
        --edge="-2535039#2" --edge="79633771#0" --edge="79633771#2" \
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

# ── stitch all per-checkpoint mc_run_meta.json into one summary ──────────────
echo ""
echo "Writing combined summary → $PROJECT_ROOT/mc_results/bottleneck_24h_meta.json"

python3 - <<'PYEOF'
import json, pathlib, os

root = pathlib.Path(os.environ["HOME"]) / "Smart-Transport-SUMO" / "mc_results"
records = []
for ckpt_dir in sorted(root.glob("ckpt_bn_*")):
    meta_path = ckpt_dir / "mc_run_meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            data = json.load(f)
        data["ckpt_dir"] = ckpt_dir.name
        records.append(data)

out = root / "bottleneck_24h_meta.json"
out.write_text(json.dumps(records, indent=2))
print(f"  wrote {len(records)} checkpoint entries → {out}")

# quick density check on the three target edges across all checkpoints
print("\nTarget edge density summary (mean across 10 seeds):")
print(f"  {'Checkpoint':<12}  {'Edge':<18}  {'Max density (vkl)':<20}  density>1?")
print("  " + "-"*60)

import xml.etree.ElementTree as ET
target = {"-2535039#2", "79633771#0", "79633771#2"}

for ckpt_dir in sorted(root.glob("ckpt_bn_*")):
    pred = ckpt_dir / "mc_prediction.edg.xml"
    if not pred.exists():
        continue
    tree = ET.parse(pred)
    edge_max = {}
    for interval in tree.getroot().findall("interval"):
        for edge in interval.findall("edge"):
            eid = edge.get("id")
            if eid in target:
                d = float(edge.get("density", 0))
                edge_max[eid] = max(edge_max.get(eid, 0), d)
    for eid in sorted(edge_max):
        d = edge_max[eid]
        flag = "*** YES ***" if d > 1.0 else "no"
        print(f"  {ckpt_dir.name:<12}  {eid:<18}  {d:<20.4f}  {flag}")
PYEOF

# remove empty SLURM stub files
rm -f "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_JOB_ID}.out" \
      "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_JOB_ID}.err"
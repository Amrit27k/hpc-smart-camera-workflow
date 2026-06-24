#!/bin/bash
#SBATCH -J mc_predict
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --exclusive
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.err
#SBATCH --array=1-48

set -euo pipefail

# ── tunables ──────────────────────────────────────────────────────────────────
N_SEEDS=2               # number of independent seeds per checkpoint
CHECKPOINT_STEP=3600    # seconds between checkpoint starts (3600 = every 1 h)
N_CHECKPOINTS=24        # how many checkpoints (24 × 3600 s = 24 h)
WINDOW=7200             # prediction horizon in seconds (7200 = 2 h)
INTERVAL=900            # edgeData aggregation interval (900 = 15 min)

SIM_START=0             # first checkpoint offset from sim time 0

PROJECT_ROOT="$HOME/Smart-Transport-SUMO"
SUMO_SIF="$PROJECT_ROOT/sumo_latest.sif"

# ── decode array index → (checkpoint, seed) ──────────────────────────────────
# SLURM array is 1-based; convert to 0-based then split
IDX=$(( SLURM_ARRAY_TASK_ID - 1 ))
CKPT_IDX=$(( IDX / N_SEEDS ))          # 0 … N_CHECKPOINTS-1
SEED_IDX=$(( IDX % N_SEEDS ))          # 0 … N_SEEDS-1

CHECKPOINT=$(( SIM_START + CKPT_IDX * CHECKPOINT_STEP ))
SEED=$(( SEED_IDX + 1 ))               # seeds start at 1
END_TIME=$(( CHECKPOINT + WINDOW ))

# Guard: don't run past the end of the simulation (86400 s = 24 h)
if [ "$CHECKPOINT" -ge 86400 ]; then
    echo "[$SLURM_ARRAY_TASK_ID] checkpoint=$CHECKPOINT is past sim end — skipping."
    exit 0
fi
if [ "$END_TIME" -gt 86400 ]; then
    END_TIME=86400
fi

CKPT_TAG=$(printf 'ckpt_%05d' "$CHECKPOINT")   # e.g. ckpt_03600
SEED_STR=$(printf '%04d' "$SEED")               # e.g. 0001

# ── paths ─────────────────────────────────────────────────────────────────────
RUN_DIR="$PROJECT_ROOT/mc_results/${CKPT_TAG}/run_${SEED_STR}"

# container-side (/sumo = $PROJECT_ROOT)
EDGEDATA_FILE_CTR="/sumo/mc_results/${CKPT_TAG}/run_${SEED_STR}/edgedata_mc.xml"
ADD_FILE_CTR="/sumo/mc_results/${CKPT_TAG}/run_${SEED_STR}/mc_edgedata_cfg.add.xml"
SUMO_LOG_CTR="/sumo/mc_results/${CKPT_TAG}/run_${SEED_STR}/sumo.log"

module purge
module load rhel9/default-dawn

mkdir -p "$RUN_DIR" "$PROJECT_ROOT/logs"

# Write the edgeData additional file (container-side output path)
printf '<?xml version="1.0" encoding="UTF-8"?>\n<additional>\n  <edgeData id="mc_edge" begin="%s" end="%s" freq="%s" file="%s" excludeEmpty="true"/>\n</additional>\n' \
    "$CHECKPOINT" "$END_TIME" "$INTERVAL" "$EDGEDATA_FILE_CTR" \
    > "$RUN_DIR/mc_edgedata_cfg.add.xml"

echo "[$SLURM_ARRAY_TASK_ID] ckpt=${CKPT_TAG}  seed=${SEED}  window=${CHECKPOINT}-${END_TIME}  $(date)"

apptainer exec -B "$PROJECT_ROOT":/sumo "$SUMO_SIF" sumo \
    --configuration-file /sumo/sumo/sumo_city.sumocfg \
    --begin             "$CHECKPOINT" \
    --end               "$END_TIME" \
    --seed              "$SEED" \
    --mesosim           true \
    --meso-edgelength   150 \
    --additional-files  "$ADD_FILE_CTR" \
    --no-step-log \
    --no-warnings \
    --log               "$SUMO_LOG_CTR"

echo "[$SLURM_ARRAY_TASK_ID] Done. edgeData → $EDGEDATA_FILE_CTR  $(date)"
#!/bin/bash
#SBATCH -J mc_bn_24h
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:45:00
#SBATCH --exclusive
#SBATCH --array=1-240
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.err

set -euo pipefail

N_SEEDS=10
CHECKPOINT_STEP=3600
N_CHECKPOINTS=24
WINDOW=7200
INTERVAL=900
SIM_START=0

PROJECT_ROOT="$HOME/Smart-Transport-SUMO"
SUMO_SIF="$PROJECT_ROOT/sumo_latest.sif"

# ── decode array index → checkpoint + seed
IDX=$(( SLURM_ARRAY_TASK_ID - 1 ))
CKPT_IDX=$(( IDX / N_SEEDS ))
SEED_IDX=$(( IDX % N_SEEDS ))

CHECKPOINT=$(( SIM_START + CKPT_IDX * CHECKPOINT_STEP ))
SEED=$(( SEED_IDX + 1 ))
END_TIME=$(( CHECKPOINT + WINDOW ))

if [ "$CHECKPOINT" -ge 86400 ]; then
    echo "[$SLURM_ARRAY_TASK_ID] checkpoint=$CHECKPOINT past sim end — skipping."
    exit 0
fi
if [ "$END_TIME" -gt 86400 ]; then
    END_TIME=86400
fi

CKPT_TAG=$(printf 'ckpt_bn_%05d' "$CHECKPOINT")
SEED_STR=$(printf '%04d' "$SEED")

# ── redirect output into per-job subdirectory
LOG_DIR="$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_ARRAY_JOB_ID}"
mkdir -p "$LOG_DIR"
exec > "$LOG_DIR/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.out" \
     2>"$LOG_DIR/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.err"

RUN_DIR="$PROJECT_ROOT/mc_results/${CKPT_TAG}/run_${SEED_STR}"

EDGEDATA_FILE_CTR="/sumo/mc_results/${CKPT_TAG}/run_${SEED_STR}/edgedata_mc.xml"
ADD_EDGEDATA_CTR="/sumo/mc_results/${CKPT_TAG}/run_${SEED_STR}/mc_edgedata_cfg.add.xml"
SUMO_LOG_CTR="/sumo/mc_results/${CKPT_TAG}/run_${SEED_STR}/sumo.log"
BOTTLENECK_ADD_CTR="/sumo/sumo/bottleneck_signals.add.xml"

module purge
module load rhel9/default-dawn

mkdir -p "$RUN_DIR"

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
    --additional-files  "${BOTTLENECK_ADD_CTR},${ADD_EDGEDATA_CTR}" \
    --no-step-log \
    --no-warnings \
    --log               "$SUMO_LOG_CTR"

echo "[$SLURM_ARRAY_TASK_ID] Done → $EDGEDATA_FILE_CTR  $(date)"

# remove empty SLURM stub files
rm -f "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.out" \
      "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.err"
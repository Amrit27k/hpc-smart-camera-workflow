#!/bin/bash
#SBATCH -J mc_bottleneck
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:45:00
#SBATCH --exclusive
#SBATCH --array=1-10
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.err

set -euo pipefail

# ── redirect output into per-job subdirectory ─────────────────────────────
LOG_DIR="$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_ARRAY_JOB_ID}"
mkdir -p "$LOG_DIR"
exec > "$LOG_DIR/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.out" \
     2>"$LOG_DIR/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.err"

# ── configuration ─────────────────────────────────────────────────────────
CHECKPOINT=25200
END_TIME=32400
INTERVAL=900

PROJECT_ROOT="$HOME/Smart-Transport-SUMO"
SUMO_SIF="$PROJECT_ROOT/sumo_latest.sif"

SEED=$SLURM_ARRAY_TASK_ID
SEED_STR=$(printf '%04d' "$SEED")

CKPT_TAG="ckpt_bottleneck_25200"
RUN_DIR="$PROJECT_ROOT/mc_results/${CKPT_TAG}/run_${SEED_STR}"

# container-side paths (/sumo = $PROJECT_ROOT)
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

echo "[$SEED] bottleneck run  ckpt=${CHECKPOINT}s  seed=${SEED}  $(date)"

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

echo "[$SEED] Done → $EDGEDATA_FILE_CTR  $(date)"

# remove empty SLURM stub files
rm -f "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.out" \
      "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.err"
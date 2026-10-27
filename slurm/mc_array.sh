#!/bin/bash
#SBATCH -J mc_predict
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --array=1-2
#SBATCH --exclusive
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_%A_%a.err

set -euo pipefail

# PROJECT_ROOT = $HOME/Smart-Transport-SUMO
# Bind: -B $PROJECT_ROOT:/sumo  →  container sees /sumo/ as project root
# sumo files are in $PROJECT_ROOT/sumo/ → /sumo/sumo/ inside container
PROJECT_ROOT="$HOME/Smart-Transport-SUMO"
SUMO_SIF="$PROJECT_ROOT/sumo_latest.sif"

SEED=$SLURM_ARRAY_TASK_ID
SEED_STR=$(printf '%04d' "$SEED")
CHECKPOINT=36000
END_TIME=43200
INTERVAL=900

# host-side paths
RUN_DIR="$PROJECT_ROOT/mc_results/run_${SEED_STR}"

# container-side paths (/sumo = $PROJECT_ROOT)
EDGEDATA_FILE_CTR="/sumo/mc_results/run_${SEED_STR}/edgedata_mc.xml"
ADD_FILE_CTR="/sumo/mc_results/run_${SEED_STR}/mc_edgedata_cfg.add.xml"
SUMO_LOG_CTR="/sumo/mc_results/run_${SEED_STR}/sumo.log"

module purge
module load rhel9/default-dawn
mkdir -p "$RUN_DIR" "$PROJECT_ROOT/logs"

printf '<?xml version="1.0" encoding="UTF-8"?>\n<additional>\n  <edgeData id="mc_edge" begin="%s" end="%s" freq="%s" file="%s" excludeEmpty="true"/>\n</additional>\n' "$CHECKPOINT" "$END_TIME" "$INTERVAL" "$EDGEDATA_FILE_CTR" > "$RUN_DIR/mc_edgedata_cfg.add.xml"

echo "[$SEED] Starting SUMO seed=$SEED  $(date)"

apptainer exec -B "$PROJECT_ROOT":/sumo "$SUMO_SIF" sumo --configuration-file /sumo/sumo/sumo_city.sumocfg --begin "$CHECKPOINT" --end "$END_TIME" --seed "$SEED" --mesosim true --meso-edgelength 150 --additional-files "$ADD_FILE_CTR" --no-step-log --no-warnings --log "$SUMO_LOG_CTR"

echo "[$SEED] SUMO done. edgeData: $EDGEDATA_FILE_CTR  $(date)"
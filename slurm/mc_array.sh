#!/bin/bash
#SBATCH --job-name=mc_predict
#SBATCH --partition=cclake          # adjust to your Dawn partition
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:30:00             # 30 min per rep is generous for MESO on this network
#SBATCH --array=1-8                 # one task per seed (8 reps total); change to 1-10 for 10 reps
#SBATCH --output=logs/mc_%A_%a.out  # %A = job array id, %a = task id (= seed)
#SBATCH --error=logs/mc_%A_%a.err

# Run from the project root on Dawn:
#   mkdir -p logs
#   sbatch slurm/mc_array.sh
#
# SLURM_ARRAY_TASK_ID maps 1:1 to --seed, so tasks run fully in parallel.
# Aggregation (mc_aggregate.sh) runs after all array tasks finish.

set -euo pipefail

# ── CONFIGURE THESE TWO PATHS FOR YOUR DAWN ACCOUNT ─────────────────────────
PROJECT_ROOT="$HOME/network-plus"
SIF="/path/to/sumo_latest.sif"        # full path to your .sif file on Dawn
# ─────────────────────────────────────────────────────────────────────────────

SUMO_DIR="$PROJECT_ROOT/pipeline_output/sumo"
SEED=$SLURM_ARRAY_TASK_ID            # seed = task id (1, 2, ... 8)
CHECKPOINT=36000                      # hour 10  (seconds)
END_TIME=43200                        # hour 12  (seconds)
INTERVAL=900                          # 15-min edgeData aggregation
RUN_DIR="$PROJECT_ROOT/mc_results/run_$(printf '%04d' $SEED)"

module purge
module load python/3.11               # adjust to Dawn's python module name

mkdir -p "$RUN_DIR" "$PROJECT_ROOT/logs"

# ── write per-run edgeData additional file ────────────────────────────────────
# Paths written into the XML must be the container-internal /data/... paths,
# because SUMO runs inside the container and sees /data as its root.
EDGEDATA_FILE_HOST="$RUN_DIR/edgedata_mc.xml"
EDGEDATA_FILE_CTR="/data/mc_results/run_$(printf '%04d' $SEED)/edgedata_mc.xml"
ADD_FILE_HOST="$RUN_DIR/mc_edgedata_cfg.add.xml"
ADD_FILE_CTR="/data/mc_results/run_$(printf '%04d' $SEED)/mc_edgedata_cfg.add.xml"

cat > "$ADD_FILE_HOST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<additional>
  <edgeData id="mc_edge"
            begin="$CHECKPOINT"
            end="$END_TIME"
            freq="$INTERVAL"
            file="$EDGEDATA_FILE_CTR"
            excludeEmpty="true"/>
</additional>
EOF

# ── run SUMO-MESO via Apptainer for this seed ────────────────────────────────
# -B binds $PROJECT_ROOT on the host to /data inside the container.
# All file paths passed to sumo use the /data/... form.
echo "[$SEED] Starting SUMO seed=$SEED  $(date)"

apptainer exec \
    -B "$PROJECT_ROOT":/data \
    "$SIF" \
    sumo \
        --configuration-file /data/pipeline_output/sumo/sumo_city.sumocfg \
        --begin              "$CHECKPOINT" \
        --end                "$END_TIME" \
        --seed               "$SEED" \
        --mesosim            true \
        --meso-edgelength    150 \
        --additional-files   "$ADD_FILE_CTR" \
        --no-step-log \
        --no-warnings \
        --log                "$EDGEDATA_FILE_CTR".log

echo "[$SEED] SUMO done. edgeData: $EDGEDATA_FILE_HOST  $(date)"

#!/bin/bash
#SBATCH --job-name=mc_aggregate
#SBATCH --partition=cclake
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=00:15:00
#SBATCH --output=logs/mc_aggregate_%j.out
#SBATCH --error=logs/mc_aggregate_%j.err

# Submit AFTER the array job finishes:
#   sbatch --dependency=afterok:<ARRAY_JOB_ID> slurm/mc_aggregate.sh
#
# Or submit both at once and let Slurm handle the dependency:
#   ARRAY_JID=$(sbatch --parsable slurm/mc_array.sh)
#   sbatch --dependency=afterok:$ARRAY_JID slurm/mc_aggregate.sh

set -euo pipefail

PROJECT_ROOT="$HOME/network-plus"

module purge
module load python/3.11

cd "$PROJECT_ROOT"

python scripts/montecarlo_predict.py \
    --sumocfg    pipeline_output/sumo/sumo_city.sumocfg \
    --checkpoint 36000 \
    --window     7200 \
    --interval   900 \
    --reps       8 \
    --seed-start 1 \
    --output     mc_results

echo "Aggregation done."
echo "Report: $PROJECT_ROOT/mc_results/mc_report.txt"
cat "$PROJECT_ROOT/mc_results/mc_report.txt"

#!/bin/bash
#SBATCH -J mc_aggregate
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --mem=4G
#SBATCH --time=00:15:00
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_aggregate_%j.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_aggregate_%j.err
set -euo pipefail
PROJECT_ROOT="$HOME/Smart-Transport-SUMO"
module purge
module load rhel9/default-dawn
python3 "$PROJECT_ROOT/scripts/montecarlo_citywide.py" \
    --sumocfg    "$PROJECT_ROOT/sumo/sumo_city.sumocfg" \
    --checkpoint 36000 \
    --window     7200 \
    --interval   900 \
    --reps       2 \
    --seed-start 1 \
    --output     "$PROJECT_ROOT/mc_results" \
    --aggregate-only
echo "Aggregation done."
cat "$PROJECT_ROOT/mc_results/mc_report.txt"
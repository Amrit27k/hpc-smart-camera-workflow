#!/bin/bash
#SBATCH --job-name=collect_edge_configs
#SBATCH --partition=cclake          # adjust to your Dawn partition
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:10:00
#SBATCH --output=logs/collect_%j.out
#SBATCH --error=logs/collect_%j.err

# Run from the project root on Dawn:
#   sbatch slurm/collect.sh

set -euo pipefail

PROJECT_ROOT="$HOME/network-plus"          # adjust if you uploaded elsewhere

module purge
module load python/3.11                    # adjust to Dawn's python module name
# collect_edge_configs.py uses only stdlib — no SUMO needed here

cd "$PROJECT_ROOT"

python scripts/collect_edge_configs.py \
    --inbox  edge_data_inbox \
    --output collected_configs \
    --stale-hours 3

echo "Collection done. Results in: $PROJECT_ROOT/collected_configs"

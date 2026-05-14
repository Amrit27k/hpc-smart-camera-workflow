#!/bin/bash
#SBATCH --job-name=ml_single_node    # Name of the job
#SBATCH --output=result_%j.out      # Standard output log (%j = Job ID)
#SBATCH --error=result_%j.err       # Error log
#SBATCH --nodes=1                   # Use exactly one node
#SBATCH --ntasks=1                  # Run a single task
#SBATCH --cpus-per-task=4           # Number of CPU cores per task
#SBATCH --mem=8G                    # Total memory for the node
#SBATCH --time=00:10:00             # Time limit (HH:MM:SS)
#SBATCH --partition=debug          # Target partition (change if needed)

# 1. Load your environment (Optional but recommended)
# module load python/3.10
cd /home/akumar/slurm/scripts
source ml_env/bin/activate
# source /path/to/your/venv/bin/activate

# 2. Run the script
python3 train_rf.py
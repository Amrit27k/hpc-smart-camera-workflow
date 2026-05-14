# Single-Node Machine Learning on Slurm

This repository contains a template for running a simple Machine Learning model (Random Forest) using a single-node Slurm configuration.

## 📂 Project Structure

* `train_rf.py`: The Python script containing the ML model logic.
* `submit.sh`: The Slurm batch script that requests resources and executes the code.
* `setup_env.sh`: A helper script to initialize the Python Virtual Environment. (optional) or just setup using python3 -m venv ml_env.
* `result_[ID].out`: Standard output logs (generated after run).
* `result_[ID].err`: Error logs (generated after run).

## 🚀 Getting Started

### 1. Prepare the Environment
Since cluster nodes often have minimal libraries installed, you must set up a virtual environment to handle the `sklearn` dependency.


### 2. Configure the Submission Script
Open submit.sh and ensure the paths match your directory structure. Specifically, check the #SBATCH directives:
```bash
--nodes=1: #Ensures the job stays on a single machine.

--mem=8G: #Adjust based on your dataset size.

--partition: debug #Change this to your cluster's specific partition name (e.g., compute, gpu, or normal).
```
### 3. Submit the Job
Use the sbatch command to queue your job:

```Bash
sbatch submit.sh
```

## 📊 Monitoring & Results
Check Job Status:

```bash
squeue -u $USER
```

### View Output:
Once the job completes, check the logs to see the model accuracy:

```Bash
cat result_*.out
```
### Debug Errors:
If the job fails, inspect the error log:

```Bash
cat result_*.err
```
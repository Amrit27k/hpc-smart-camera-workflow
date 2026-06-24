#!/bin/bash
#SBATCH -J mc_bn_aggregate
#SBATCH -A AIRR-P89-DAWN-GPU
#SBATCH -p pvc9
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --mem=8G
#SBATCH --exclusive
#SBATCH --time=00:15:00
#SBATCH --output=/home/%u/Smart-Transport-SUMO/logs/mc_%j.out
#SBATCH --error=/home/%u/Smart-Transport-SUMO/logs/mc_%j.err

set -euo pipefail

# ── redirect output into per-job subdirectory ─────────────────────────────
LOG_DIR="$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_JOB_ID}"
mkdir -p "$LOG_DIR"
exec > "$LOG_DIR/mc_${SLURM_JOB_ID}.out" \
     2>"$LOG_DIR/mc_${SLURM_JOB_ID}.err"

# ── configuration ─────────────────────────────────────────────────────────
PROJECT_ROOT="$HOME/Smart-Transport-SUMO"
CKPT_TAG="ckpt_bottleneck_25200"
CKPT_DIR="$PROJECT_ROOT/mc_results/${CKPT_TAG}"

module purge
module load rhel9/default-dawn

echo "=== Bottleneck MC aggregate: $(date) ==="

N_FOUND=$(find "$CKPT_DIR" -name "edgedata_mc.xml" 2>/dev/null | wc -l)
echo "Found $N_FOUND edgedata files (expected 10)"

TARGET_EDGES="-2535039#2,79633771#0,79633771#2"

python3 "$PROJECT_ROOT/scripts/montecarlo_citywide.py" \
    --sumocfg    "$PROJECT_ROOT/sumo/sumo_city.sumocfg" \
    --checkpoint 25200 \
    --window     7200 \
    --interval   900 \
    --reps       10 \
    --seed-start 1 \
    --output     "$CKPT_DIR" \
    --aggregate-only \
    --edges      "$TARGET_EDGES"

echo ""
echo "=== Density check on target edges ==="

python3 - <<'PYEOF'
import json, pathlib, os, xml.etree.ElementTree as ET

ckpt_dir = pathlib.Path(os.environ["HOME"]) / "Smart-Transport-SUMO" / "mc_results" / "ckpt_bottleneck_25200"
meta = json.loads((ckpt_dir / "mc_run_meta.json").read_text())

print(f"\nCheckpoint : {meta['checkpoint_s']}s  ({meta['checkpoint_s']//3600:02d}:00)")
print(f"Window     : {meta['checkpoint_s']}s → {meta['end_s']}s")
print(f"Reps       : {meta['n_reps']}")
print(f"\nCity-wide snapshots:")
for label, v in meta.get("citywide", {}).items():
    print(f"  {label:<8} speed={v['speed_mean_weighted_ms']} m/s  "
          f"density={v['density_mean_vkl']} vkl  edges={v['n_edges']}")

pred = ckpt_dir / "mc_prediction.edg.xml"
if pred.exists():
    tree = ET.parse(pred)
    target = {"-2535039#2", "79633771#0", "79633771#2"}
    print("\nTarget edge densities from mc_prediction.edg.xml:")
    print(f"  {'Interval':<13}  {'Edge':<18}  {'density (vkl)':<16}  {'speed (m/s)':<14}  density>1?")
    print("  " + "-"*72)
    for interval in tree.getroot().findall("interval"):
        b = float(interval.get("begin"))
        e = float(interval.get("end"))
        ts = f"{int(b)//3600:02d}:{(int(b)%3600)//60:02d}–{int(e)//3600:02d}:{(int(e)%3600)//60:02d}"
        for edge in interval.findall("edge"):
            eid = edge.get("id")
            if eid in target:
                d = float(edge.get("density", 0))
                s = float(edge.get("speed", 0))
                flag = "*** YES ***" if d > 1.0 else "no"
                print(f"  {ts:<13}  {eid:<18}  {d:<16.4f}  {s:<14.4f}  {flag}")
PYEOF

echo ""
echo "Aggregation complete: $(date)"
echo "Full report  : $CKPT_DIR/mc_report.txt"
echo "sumo-gui file: $CKPT_DIR/mc_prediction.edg.xml"

# remove empty SLURM stub files
rm -f "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_JOB_ID}.out" \
      "$HOME/Smart-Transport-SUMO/logs/mc_${SLURM_JOB_ID}.err"
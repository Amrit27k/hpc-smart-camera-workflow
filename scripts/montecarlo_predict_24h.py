#!/usr/bin/env python3
"""
montecarlo_citywide.py
======================
Monte Carlo traffic prediction using SUMO-MESO.

WHAT IT DOES
------------
1. Takes the existing 24-hr sumo_city.sumocfg + routes_city.xml as-is.
2. Picks a checkpoint time T (default: hour 10 = 36000 s).
3. Runs SUMO-MESO from T to T+window with N repetitions (default: 8),
   varying only --seed.  No demand scaling, no jitter.
   SUMO's mesoscopic gap-acceptance and queuing stochasticity gives
   natural variation across runs for free.
4. Each run dumps edgeData at 15-min intervals for that window.
5. Across all runs, for each (edge, timestep) computes:
      mean speed, mean density, min/max speed, min/max density.
6. Prints a summary table and writes:
      <output>/
          run_<seed>/edgedata_mc.xml   ← raw per-run edgeData
          mc_prediction.edg.xml        ← MC-aggregated speed/density per
                                          edge × timestep, SUMO edgeData schema
          mc_report.txt                ← human-readable report
          mc_run_meta.json             ← machine-readable run metadata

FULL-DAY WORKFLOW
-----------------
Run mc_array.sh (SLURM) which maps array indices → (checkpoint, seed) pairs,
writing each run into:

    mc_results/ckpt_<NNNNN>/run_<SEED>/edgedata_mc.xml

Then run mc_aggregate.sh which calls this script once per checkpoint with
--aggregate-only, producing one mc_prediction.edg.xml per checkpoint hour.

Load any mc_prediction.edg.xml straight into sumo-gui (Streets → color by
speed/density) to visualise the predicted traffic for that hour.

USAGE
-----
    python montecarlo_citywide.py [options]

    --sumocfg       path to sumo_city.sumocfg          (default: auto-detected)
    --checkpoint    simulation second to start from     (default: 36000 = 10h)
    --window        seconds to simulate forward         (default: 7200 = 2h)
    --interval      edgeData aggregation interval (s)   (default: 900 = 15min)
    --reps          number of Monte Carlo repetitions   (default: 8)
    --seed-start    first seed value                    (default: 1)
    --output        output directory                    (default: mc_results/ckpt_<NNNNN>/)
    --sumo-bin      sumo executable name                (default: sumo)
    --edges         comma-separated edge ids to report  (default: top 10 by flow)
    --aggregate-only  skip SUMO runs; read existing edgedata_mc.xml files

REQUIREMENTS
------------
    SUMO must be installed and `sumo` on PATH (or pass --sumo-bin / --sif).
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mc_predict")

# ── project paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SUMO_DIR     = PROJECT_ROOT / "sumo"
DEFAULT_CFG  = SUMO_DIR / "sumo_city.sumocfg"


# ── helpers ───────────────────────────────────────────────────────────────────

def seconds_to_hhmm(s: float) -> str:
    h = int(s) // 3600
    m = (int(s) % 3600) // 60
    return f"{h:02d}:{m:02d}"


def default_output_dir(checkpoint: int) -> Path:
    """
    Return  <project_root>/mc_results/ckpt_<NNNNN>/
    so each checkpoint's runs live in their own namespace and never
    overwrite each other, even when running the full-day array in parallel.
    """
    tag = f"ckpt_{checkpoint:05d}"
    return PROJECT_ROOT / "mc_results" / tag


# ── edgeData additional file builder ─────────────────────────────────────────

def write_mc_edgedata_add(out_dir: Path, begin: int, end: int,
                          interval: int, edgedata_file: str) -> Path:
    """
    Write a temporary additional file that configures edgeData collection
    for exactly the Monte Carlo window [begin, end] at `interval` resolution.
    """
    add = ET.Element("additional")
    ET.SubElement(add, "edgeData",
                  id="mc_edge",
                  begin=str(begin),
                  end=str(end),
                  freq=str(interval),
                  file=edgedata_file,
                  excludeEmpty="true")
    path = out_dir / "mc_edgedata_cfg.add.xml"
    ET.ElementTree(add).write(str(path), encoding="unicode", xml_declaration=True)
    return path


# ── single SUMO run ───────────────────────────────────────────────────────────

def run_sumo(sumo_bin: str, cfg: Path, seed: int,
             checkpoint: int, end_time: int,
             additional: Path, edgedata_out: Path,
             run_dir: Path,
             sif: str = "", bind: str = "") -> bool:
    """
    Launch one SUMO-MESO run.

    If `sif` is set, wraps the call in:
        apptainer exec -B <bind> <sif> sumo ...
    """
    if sif:
        if not bind:
            bind = f"{cfg.parent}:/data"
        host_root, ctr_root = bind.split(":", 1)
        host_root_p = Path(host_root).resolve()

        def to_ctr(p: Path) -> str:
            try:
                rel = p.resolve().relative_to(host_root_p)
                return f"{ctr_root}/{rel.as_posix()}"
            except ValueError:
                return str(p)

        cmd = [
            "apptainer", "exec", "-B", bind, sif, "sumo",
            "--configuration-file", to_ctr(cfg),
            "--begin",              str(checkpoint),
            "--end",                str(end_time),
            "--seed",               str(seed),
            "--mesosim",            "true",
            "--meso-edgelength",    "150",
            "--additional-files",   to_ctr(additional),
            "--no-step-log",
            "--no-warnings",
            "--log",                to_ctr(run_dir / "sumo.log"),
        ]
    else:
        cmd = [
            sumo_bin,
            "--configuration-file", str(cfg.resolve()),
            "--begin",              str(checkpoint),
            "--end",                str(end_time),
            "--seed",               str(seed),
            "--mesosim",            "true",
            "--meso-edgelength",    "150",
            "--additional-files",   str(additional.resolve()),
            "--no-step-log",
            "--no-warnings",
            "--log",                str((run_dir / "sumo.log").resolve()),
        ]
    log.info("  seed=%d  cmd: %s", seed, " ".join(cmd[-6:]))
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cfg.parent),
            timeout=600,
        )
        if result.returncode != 0:
            log.error("SUMO exited %d for seed=%d", result.returncode, seed)
            log.error(result.stderr[-2000:])
            return False
        return True
    except FileNotFoundError:
        log.error("SUMO binary '%s' not found.", sumo_bin)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        log.error("SUMO timed out for seed=%d", seed)
        return False


# ── parse edgeData output ─────────────────────────────────────────────────────

def parse_edgedata(path: Path) -> dict:
    """
    Parse a SUMO edgeData XML file.

    Returns:
        { (begin, end, edge_id): {"speed": float, "density": float,
                                   "sampled": float}, ... }
    """
    data = {}
    if not path.exists():
        log.warning("edgeData file not found: %s", path)
        return data
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        log.error("Parse error in %s: %s", path, exc)
        return data

    for interval in tree.getroot().findall("interval"):
        begin = float(interval.get("begin", 0))
        end   = float(interval.get("end",   0))
        for edge in interval.findall("edge"):
            eid     = edge.get("id", "")
            speed   = float(edge.get("speed",   edge.get("meanSpeed",   0) or 0))
            density = float(edge.get("density", edge.get("laneDensity", 0) or 0))
            sampled = float(edge.get("sampledSeconds", 0) or 0)
            data[(begin, end, eid)] = {
                "speed": speed, "density": density, "sampled": sampled
            }
    return data


# ── aggregate across runs ─────────────────────────────────────────────────────

def aggregate(all_runs: list) -> dict:
    """
    Aggregate a list of parse_edgedata() dicts (one per MC repetition).

    Returns per-(interval × edge) stats: mean/min/max/std for speed and density.
    """
    combined: dict = defaultdict(lambda: defaultdict(list))
    for run_data in all_runs:
        for key, vals in run_data.items():
            combined[key]["speed"].append(vals["speed"])
            combined[key]["density"].append(vals["density"])
            combined[key]["sampled"].append(vals.get("sampled", 0.0))

    result = {}
    for key, series in combined.items():
        speeds    = series["speed"]
        densities = series["density"]
        sampled   = series["sampled"]
        n = len(speeds)
        result[key] = {
            "speed_mean":    mean(speeds),
            "speed_min":     min(speeds),
            "speed_max":     max(speeds),
            "speed_std":     stdev(speeds) if n > 1 else 0.0,
            "density_mean":  mean(densities),
            "density_min":   min(densities),
            "density_max":   max(densities),
            "density_std":   stdev(densities) if n > 1 else 0.0,
            "sampled_mean":  mean(sampled) if sampled else 0.0,
            "n_runs":        n,
        }
    return result


# ── pick top edges by mean density ───────────────────────────────────────────

def top_edges_by_density(aggregated: dict, n: int = 10) -> list:
    edge_density = defaultdict(list)
    for (_, _, eid), vals in aggregated.items():
        edge_density[eid].append(vals["density_mean"])
    ranked = sorted(edge_density, key=lambda e: mean(edge_density[e]), reverse=True)
    return ranked[:n]


# ── city-wide aggregation ─────────────────────────────────────────────────────

def aggregate_citywide(aggregated: dict) -> dict:
    """
    Collapse per-edge stats into one city-wide row per (begin, end) timestep.

    Uses each edge's mean sampledSeconds as a traffic-volume weight so
    busy edges contribute more than quiet ones to the network-level speed.
    """
    by_interval = defaultdict(list)
    for (begin, end, eid), vals in aggregated.items():
        by_interval[(begin, end)].append(vals)

    result = {}
    for (begin, end), edge_list in by_interval.items():
        weights   = [v.get("sampled_mean", 0.0) for v in edge_list]
        speeds    = [v["speed_mean"]    for v in edge_list]
        densities = [v["density_mean"]  for v in edge_list]
        total_weight = sum(weights)

        weighted_speed = (
            sum(s * w for s, w in zip(speeds, weights)) / total_weight
            if total_weight > 0
            else (mean(speeds) if speeds else 0.0)
        )

        result[(begin, end)] = {
            "speed_mean_weighted":   weighted_speed,
            "speed_mean_simple":     mean(speeds)    if speeds    else 0.0,
            "speed_min":             min(speeds)     if speeds    else 0.0,
            "speed_max":             max(speeds)     if speeds    else 0.0,
            "density_mean":          mean(densities) if densities else 0.0,
            "density_min":           min(densities)  if densities else 0.0,
            "density_max":           max(densities)  if densities else 0.0,
            "n_edges":               len(edge_list),
            "total_sampled_seconds": total_weight,
        }
    return result


def citywide_snapshot(citywide: dict, checkpoint: int, window: int) -> dict:
    """
    Pull out city-wide rows at regular offsets through the prediction window.

    Reports at +15 min, +30 min, then every hour up to +window, so you get
    a full picture regardless of whether window is 2 h or 24 h.
    """
    # Build a set of target offsets: 900, 1800, then every 3600 up to window
    offsets: list[int] = []
    for s in (900, 1800):
        if s <= window:
            offsets.append(s)
    hour = 3600
    while hour <= window:
        if hour not in offsets:
            offsets.append(hour)
        hour += 3600

    snap = {}
    for offset in offsets:
        target_s = checkpoint + offset
        label = f"+{offset // 3600}h" if offset % 3600 == 0 else f"+{offset // 60}min"
        matched = None
        for (begin, end), vals in sorted(citywide.items()):
            if begin <= target_s <= end:
                matched = vals
                break
        if matched is None and citywide:
            _, matched = min(citywide.items(),
                             key=lambda kv: abs(kv[0][0] - target_s))
        if matched:
            snap[label] = {
                "speed_mean_weighted_ms": round(matched["speed_mean_weighted"], 3),
                "density_mean_vkl":       round(matched["density_mean"], 3),
                "n_edges":                matched["n_edges"],
            }
    return snap


# ── reporting ─────────────────────────────────────────────────────────────────

def write_edgedata_xml(aggregated: dict, path: Path,
                       dataset_id: str = "mc_prediction") -> None:
    """
    Write MC-aggregated per-edge stats in SUMO's native edgeData (meandata)
    XML schema so the file loads straight into sumo-gui as a data overlay.

    In sumo-gui: Edit → Visualization Settings → Streets tab →
    set "Color edges by" to a data-based scheme, then load this file.
    """
    root = ET.Element("meandata")
    by_interval = defaultdict(list)
    for (begin, end, eid), vals in aggregated.items():
        by_interval[(begin, end)].append((eid, vals))

    for begin, end in sorted(by_interval.keys()):
        interval_el = ET.SubElement(root, "interval",
                                    begin=f"{begin:.2f}",
                                    end=f"{end:.2f}",
                                    id=dataset_id)
        for eid, v in sorted(by_interval[(begin, end)], key=lambda t: t[0]):
            ET.SubElement(interval_el, "edge",
                          id=eid,
                          speed=f"{v['speed_mean']:.4f}",
                          density=f"{v['density_mean']:.4f}",
                          speed_min=f"{v['speed_min']:.4f}",
                          speed_max=f"{v['speed_max']:.4f}",
                          speed_std=f"{v['speed_std']:.4f}",
                          density_min=f"{v['density_min']:.4f}",
                          density_max=f"{v['density_max']:.4f}",
                          density_std=f"{v['density_std']:.4f}",
                          n_runs=str(v["n_runs"]))

    ET.ElementTree(root).write(str(path), encoding="unicode", xml_declaration=True)
    log.info("Wrote %s", path.name)


def write_report(aggregated: dict, citywide: dict, report_edges: list,
                 checkpoint: int, window: int, reps: int,
                 path: Path) -> None:
    """
    Human-readable prediction report covering all 15-min buckets in the window.

    City-wide section shows every interval; per-edge section shows the same.
    """
    lines = []
    lines.append("=" * 70)
    lines.append("  MONTE CARLO TRAFFIC PREDICTION REPORT")
    lines.append(f"  Checkpoint  : {seconds_to_hhmm(checkpoint)}  ({checkpoint} s)")
    lines.append(f"  Window      : +{window // 3600}h  "
                 f"({seconds_to_hhmm(checkpoint)} → {seconds_to_hhmm(checkpoint + window)})")
    lines.append(f"  Repetitions : {reps}")
    lines.append("=" * 70)

    # ── city-wide section ─────────────────────────────────────────────────────
    lines.append("\n  CITY-WIDE (traffic-weighted mean across all sampled edges)\n")
    lines.append(f"  {'Interval':<13}  {'Wtd speed (m/s)':<38}  Density (veh/km/lane)")
    lines.append("  " + "-" * 66)

    for (begin, end), v in sorted(citywide.items()):
        interval_str = f"{seconds_to_hhmm(begin)}–{seconds_to_hhmm(end)}"
        speed_str = (f"{v['speed_mean_weighted']:.2f}"
                     f"  (simple {v['speed_mean_simple']:.2f},"
                     f" rng {v['speed_min']:.2f}–{v['speed_max']:.2f})")
        density_str = (f"{v['density_mean']:.3f}"
                       f"  (rng {v['density_min']:.3f}–{v['density_max']:.3f})")
        lines.append(f"  {interval_str:<13}  {speed_str:<38}  {density_str}")

    lines.append("")

    # ── per-edge section ──────────────────────────────────────────────────────
    by_edge: dict = defaultdict(dict)
    for (begin, end, eid), vals in aggregated.items():
        by_edge[eid][(begin, end)] = vals

    for eid in report_edges:
        if eid not in by_edge:
            lines.append(f"\n  Edge {eid}: no data")
            continue
        lines.append(f"\n  Edge: {eid}")
        lines.append(f"  {'Interval':<13}  {'Speed (m/s)':<34}  Density (veh/km/lane)")
        lines.append("  " + "-" * 66)
        for (begin, end), v in sorted(by_edge[eid].items()):
            interval_str = f"{seconds_to_hhmm(begin)}–{seconds_to_hhmm(end)}"
            speed_str   = (f"{v['speed_mean']:.2f}"
                           f"  (rng {v['speed_min']:.2f}–{v['speed_max']:.2f})")
            density_str = (f"{v['density_mean']:.3f}"
                           f"  (rng {v['density_min']:.3f}–{v['density_max']:.3f})")
            lines.append(f"  {interval_str:<13}  {speed_str:<34}  {density_str}")

    lines.append("\n" + "=" * 70)

    report_text = "\n".join(lines)
    path.write_text(report_text, encoding="utf-8")
    print("\n" + report_text)
    log.info("Wrote %s", path.name)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Monte Carlo SUMO-MESO traffic prediction.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--sumocfg",    default=str(DEFAULT_CFG),
                    help="Path to sumo_city.sumocfg")
    ap.add_argument("--checkpoint", type=int, default=36000,
                    help="Simulation second to start prediction from (36000 = 10 h)")
    ap.add_argument("--window",     type=int, default=7200,
                    help="Seconds to run forward (7200 = 2 h)")
    ap.add_argument("--interval",   type=int, default=900,
                    help="edgeData aggregation interval in seconds (900 = 15 min)")
    ap.add_argument("--reps",       type=int, default=8,
                    help="Number of Monte Carlo repetitions")
    ap.add_argument("--seed-start", type=int, default=1,
                    help="First random seed")
    ap.add_argument("--output",     default="",
                    help="Output directory (default: mc_results/ckpt_<NNNNN>/)")
    ap.add_argument("--sumo-bin",   default="sumo",
                    help="SUMO executable name")
    ap.add_argument("--sif",        default="",
                    help="Apptainer .sif image path for containerised SUMO")
    ap.add_argument("--bind",       default="",
                    help="Apptainer bind mount  host_path:container_path")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="Skip SUMO runs — aggregate existing edgedata_mc.xml files")
    ap.add_argument("--edges", action="append", dest="edges", default=[],
                    help="Comma-separated edge ids for the report "
                         "(default: top 10 by mean density)")
    args = ap.parse_args()

    cfg        = Path(args.sumocfg).resolve()
    checkpoint = args.checkpoint
    end_time   = checkpoint + args.window

    # Default output dir is checkpoint-namespaced so parallel runs don't clash
    out_root = (
        Path(args.output).resolve()
        if args.output
        else default_output_dir(checkpoint)
    )
    out_root.mkdir(parents=True, exist_ok=True)

    if not cfg.exists():
        log.error("sumocfg not found: %s", cfg)
        return 1

    log.info("=" * 60)
    log.info("Monte Carlo prediction")
    log.info("  sumocfg    : %s", cfg)
    log.info("  checkpoint : %s (%d s)", seconds_to_hhmm(checkpoint), checkpoint)
    log.info("  window     : %s → %s (%d s)",
             seconds_to_hhmm(checkpoint), seconds_to_hhmm(end_time), args.window)
    log.info("  interval   : %d s (%.0f min)", args.interval, args.interval / 60)
    log.info("  reps       : %d  (seeds %d..%d)",
             args.reps, args.seed_start, args.seed_start + args.reps - 1)
    log.info("  output     : %s", out_root)
    log.info("=" * 60)

    all_run_data: list = []
    failed_seeds: list = []

    if args.aggregate_only:
        log.info("Aggregate-only mode: scanning %s ...", out_root)
        found = sorted(out_root.glob("run_*/edgedata_mc.xml"))
        if not found:
            log.error("No edgedata_mc.xml files found in %s", out_root)
            return 1
        for edgedata_path in found:
            run_data = parse_edgedata(edgedata_path)
            log.info("  read %s → %d entries",
                     edgedata_path.parent.name, len(run_data))
            if run_data:
                all_run_data.append(run_data)
    else:
        for i in range(args.reps):
            seed    = args.seed_start + i
            run_dir = out_root / f"run_{seed:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)

            edgedata_file = str((run_dir / "edgedata_mc.xml").resolve())
            add_path = write_mc_edgedata_add(
                run_dir, checkpoint, end_time, args.interval, edgedata_file
            )

            log.info("[%d/%d] seed=%d ...", i + 1, args.reps, seed)
            ok = run_sumo(
                sumo_bin=args.sumo_bin, cfg=cfg, seed=seed,
                checkpoint=checkpoint, end_time=end_time,
                additional=add_path,
                edgedata_out=Path(edgedata_file),
                run_dir=run_dir,
                sif=args.sif, bind=args.bind,
            )
            if not ok:
                failed_seeds.append(seed)
                continue

            run_data = parse_edgedata(Path(edgedata_file))
            log.info("  → %d (interval, edge) entries", len(run_data))
            all_run_data.append(run_data)

    if not all_run_data:
        log.error("No data to aggregate. Check sumo.log files in %s", out_root)
        return 1
    if failed_seeds:
        log.warning("%d runs failed (seeds: %s)", len(failed_seeds), failed_seeds)

    log.info("Aggregating %d successful runs ...", len(all_run_data))
    aggregated = aggregate(all_run_data)
    log.info("  %d (interval × edge) entries", len(aggregated))

    citywide = aggregate_citywide(aggregated)
    log.info("  %d city-wide timesteps", len(citywide))

    report_edges = (
        [e.strip() for e in args.edges.split(",") if e.strip()]
        if args.edges.strip()
        else top_edges_by_density(aggregated, n=10)
    )
    log.info("Report edges: %s", report_edges)

    write_edgedata_xml(aggregated, out_root / "mc_prediction.edg.xml")
    write_report(aggregated, citywide, report_edges,
                 checkpoint, args.window, len(all_run_data),
                 out_root / "mc_report.txt")

    summary_json = {
        "checkpoint_s":  checkpoint,
        "end_s":         end_time,
        "interval_s":    args.interval,
        "n_reps":        len(all_run_data),
        "failed_seeds":  failed_seeds,
        "report_edges":  report_edges,
        "citywide":      citywide_snapshot(citywide, checkpoint, args.window),
    }
    (out_root / "mc_run_meta.json").write_text(
        json.dumps(summary_json, indent=2), encoding="utf-8"
    )

    log.info("=" * 60)
    log.info("Done.  Results in: %s", out_root)
    log.info("  mc_prediction.edg.xml — per-edge×timestep, sumo-gui-loadable")
    log.info("  mc_report.txt         — human-readable (city-wide + per-edge)")
    log.info("  mc_run_meta.json      — machine-readable metadata + city-wide snapshot")
    log.info("  run_XXXX/             — raw edgeData per seed")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
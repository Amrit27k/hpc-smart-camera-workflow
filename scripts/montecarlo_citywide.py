#!/usr/bin/env python3
"""
montecarlo_predict.py
=====================
Monte Carlo traffic prediction using SUMO-MESO.

WHAT IT DOES
------------
1. Takes the existing 24-hr sumo_city.sumocfg + routes_city.xml as-is.
2. Picks a checkpoint time T (default: hour 10 = 36000 s).
3. Runs SUMO-MESO from T to T+2h with N repetitions (default: 8),
   varying only --seed.  No demand scaling, no jitter.
   SUMO's mesoscopic gap-acceptance and queuing stochasticity gives
   natural variation across runs for free.
4. Each run dumps edgeData at 15-min intervals for that 2-hr window.
5. Across all runs, for each (edge, timestep) computes:
      mean speed, mean density, min/max speed, min/max density.
6. Prints a summary table and writes:
      mc_results/
          run_<seed>/edgedata_mc.xml   ← raw per-run edgeData
          mc_prediction.edg.xml        ← MC-aggregated speed/density per
                                          edge × timestep, in SUMO's native
                                          edgeData (meandata) schema —
                                          load straight into sumo-gui to
                                          color the network by predicted
                                          traffic.
          mc_report.txt                ← human-readable report (city-wide
                                          summary + per-edge breakdown)

USAGE
-----
    python montecarlo_predict.py [options]

    --sumocfg       path to sumo_city.sumocfg          (default: auto-detected)
    --checkpoint    simulation second to start from     (default: 36000 = 10h)
    --window        seconds to simulate forward         (default: 7200 = 2h)
    --interval      edgeData aggregation interval (s)   (default: 900 = 15min)
    --reps          number of Monte Carlo repetitions   (default: 8)
    --seed-start    first seed value                    (default: 1)
    --output        output directory                    (default: mc_results/)
    --sumo-bin      sumo executable name                (default: sumo)
    --edges         comma-separated edge ids to report  (default: top 10 by flow)

REQUIREMENTS
------------
    pip install lxml   (or use stdlib xml.etree — already used here)
    SUMO must be installed and `sumo` on PATH (or pass --sumo-bin).
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

# ── logging ──────────────────────────────────────────────────────────────────
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
                  excludeEmpty="true",
                  # dump mean speed (m/s) and density (veh/km/lane)
                  )
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
    where `bind` is  host_path:container_path  (default: cfg.parent:/data).
    All paths passed to sumo are rewritten to the container-side mount point.

    Key sumo flags:
      --begin          start at checkpoint (skips 0..checkpoint warmup)
      --end             stop at checkpoint + window
      --seed            the only thing that changes across reps
      --mesosim         enable mesoscopic model
      --additional-files  our per-run edgeData config
      --no-step-log     suppress per-step console spam
    """
    if sif:
        # Resolve bind mount: default binds the sumo config's directory to /data
        if not bind:
            bind = f"{cfg.parent}:/data"
        host_root, ctr_root = bind.split(":", 1)
        host_root_p = Path(host_root).resolve()

        def to_ctr(p: Path) -> str:
            """Rewrite an absolute host path to its container equivalent."""
            try:
                rel = p.resolve().relative_to(host_root_p)
                return f"{ctr_root}/{rel.as_posix()}"
            except ValueError:
                return str(p)  # path outside bind mount — pass through as-is

        cmd = [
            "apptainer", "exec",
            "-B", bind,
            sif,
            "sumo",
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
            cwd=str(cfg.parent),   # relative paths in cfg resolve from here
            timeout=600,
        )
        if result.returncode != 0:
            log.error("SUMO exited %d for seed=%d", result.returncode, seed)
            log.error(result.stderr[-2000:])
            return False
        return True
    except FileNotFoundError:
        log.error("SUMO binary '%s' not found. Install SUMO and add it to PATH.", sumo_bin)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        log.error("SUMO timed out for seed=%d", seed)
        return False


# ── parse edgeData output ─────────────────────────────────────────────────────

def parse_edgedata(path: Path) -> dict:
    """
    Parse a SUMO edgeData XML file.

    Returns:
        {
          (begin, end, edge_id): {
              "speed":   float,   # mean speed m/s
              "density": float,   # veh/km/lane
              "sampled": float,   # sampledSeconds — vehicle-seconds observed
                                  # on this edge in this interval; used later
                                  # as a traffic-volume weight for city-wide
                                  # aggregation.
          },
          ...
        }
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
            data[(begin, end, eid)] = {"speed": speed, "density": density, "sampled": sampled}
    return data


# ── aggregate across runs ─────────────────────────────────────────────────────

def aggregate(all_runs: list) -> dict:
    """
    all_runs: list of dicts from parse_edgedata(), one per rep.

    Returns:
        {
          (begin, end, edge_id): {
              "speed_mean", "speed_min", "speed_max", "speed_std",
              "density_mean", "density_min", "density_max", "density_std",
              "sampled_mean",
              "n_runs"
          }
        }
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


# ── pick top edges by mean flow ───────────────────────────────────────────────

def top_edges_by_density(aggregated: dict, n: int = 10) -> list:
    """Return top-n edge ids ranked by mean density across all timesteps."""
    edge_density = defaultdict(list)
    for (_, _, eid), vals in aggregated.items():
        edge_density[eid].append(vals["density_mean"])
    ranked = sorted(edge_density, key=lambda e: mean(edge_density[e]), reverse=True)
    return ranked[:n]


# ── city-wide (network-level) aggregation ─────────────────────────────────────

def aggregate_citywide(aggregated: dict) -> dict:
    """
    Collapse the per-edge aggregated stats into one city-wide row per
    (begin, end) timestep.

    aggregated: output of aggregate() — keyed by (begin, end, edge_id).

    Returns:
        {
          (begin, end): {
              "speed_mean_weighted":   traffic-weighted mean speed (m/s)
                                       across all sampled edges, using each
                                       edge's mean sampledSeconds (vehicle-
                                       seconds observed) as its weight, so
                                       busy edges count more than quiet ones.
              "speed_mean_simple":     plain unweighted mean across edges,
                                       kept for comparison.
              "speed_min" / "speed_max": extremes of per-edge mean speed.
              "density_mean" / "_min" / "_max": same idea for density.
              "n_edges":               number of distinct edges sampled.
              "total_sampled_seconds": sum of vehicle-seconds across edges —
                                       a rough proxy for total network
                                       activity/volume in that interval.
          },
          ...
        }
    """
    by_interval = defaultdict(list)
    for (begin, end, eid), vals in aggregated.items():
        by_interval[(begin, end)].append(vals)

    result = {}
    for (begin, end), edge_list in by_interval.items():
        weights   = [v.get("sampled_mean", 0.0) for v in edge_list]
        speeds    = [v["speed_mean"] for v in edge_list]
        densities = [v["density_mean"] for v in edge_list]
        total_weight = sum(weights)

        if total_weight > 0:
            weighted_speed = sum(s * w for s, w in zip(speeds, weights)) / total_weight
        else:
            weighted_speed = mean(speeds) if speeds else 0.0

        result[(begin, end)] = {
            "speed_mean_weighted":   weighted_speed,
            "speed_mean_simple":     mean(speeds) if speeds else 0.0,
            "speed_min":             min(speeds) if speeds else 0.0,
            "speed_max":             max(speeds) if speeds else 0.0,
            "density_mean":          mean(densities) if densities else 0.0,
            "density_min":           min(densities) if densities else 0.0,
            "density_max":           max(densities) if densities else 0.0,
            "n_edges":               len(edge_list),
            "total_sampled_seconds": total_weight,
        }
    return result


def citywide_snapshot(citywide: dict, checkpoint: int) -> dict:
    """Pick out the +1h / +2h city-wide rows for the JSON summary."""
    snap = {}
    for label, target_s in (("+1h", checkpoint + 3600), ("+2h", checkpoint + 7200)):
        matched = None
        for (begin, end), vals in sorted(citywide.items()):
            if begin <= target_s <= end:
                matched = vals
                break
        if matched is None and citywide:
            (begin, end), matched = min(
                citywide.items(), key=lambda kv: abs(kv[0][0] - target_s)
            )
        if matched:
            snap[label] = {
                "speed_mean_weighted_ms": round(matched["speed_mean_weighted"], 3),
                "density_mean_vkl":       round(matched["density_mean"], 3),
                "n_edges":                matched["n_edges"],
            }
    return snap


# ── reporting ─────────────────────────────────────────────────────────────────

def seconds_to_hhmm(s: float) -> str:
    h = int(s) // 3600
    m = (int(s) % 3600) // 60
    return f"{h:02d}:{m:02d}"


def write_edgedata_xml(aggregated: dict, path: Path,
                       dataset_id: str = "mc_prediction") -> None:
    """
    Write the MC-aggregated per-edge stats out in SUMO's native edgeData
    (meandata) XML schema -- the same <meandata><interval><edge .../>
    structure your raw run_XXXX/edgedata_mc.xml files already use -- so
    it loads straight into sumo-gui as a data overlay.

    In sumo-gui: Edit > Visualization Settings > Streets tab > set
    "Color edges by" to a data-based scheme, then load this file as the
    edge data source. You can then color by speed or density, and step
    through the +1h/+2h (etc.) intervals as the GUI's simulated clock
    advances through the checkpoint window.

    "speed" and "density" are the mean across MC repetitions (the
    standard attribute names sumo-gui expects). Extra attributes
    (suffixed _min/_max/_std, plus n_runs) are also written so you can
    optionally color by prediction uncertainty instead of the mean.
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
    Human-readable prediction report.

    For each reported edge, prints predicted speed/density at:
      +1h (checkpoint+3600) and +2h (checkpoint+7200)
    as mean (min – max).
    """
    lines = []
    lines.append("=" * 70)
    lines.append("  MONTE CARLO TRAFFIC PREDICTION REPORT")
    lines.append(f"  Checkpoint  : {seconds_to_hhmm(checkpoint)}  ({checkpoint}s)")
    lines.append(f"  Window      : +{window//3600}h  "
                 f"({seconds_to_hhmm(checkpoint)} → {seconds_to_hhmm(checkpoint+window)})")
    lines.append(f"  Repetitions : {reps}")
    lines.append("=" * 70)

    target_offsets = {
        "+1h": checkpoint + 3600,
        "+2h": checkpoint + 7200,
    }

    # ── city-wide section ───────────────────────────────────────────────
    lines.append("\n  CITY-WIDE (network-level, traffic-weighted across all sampled edges)")
    lines.append("  " + "-" * 66)
    lines.append(f"  {'Offset':<6}  {'Interval':<12}  "
                 f"{'Speed (m/s)':<34}  {'Density (veh/km/lane)'}")

    for label, target_s in target_offsets.items():
        matched = None
        for (begin, end), vals in sorted(citywide.items()):
            if begin <= target_s <= end:
                matched = (begin, end, vals)
                break
        if matched is None and citywide:
            (begin, end), vals = min(
                citywide.items(), key=lambda kv: abs(kv[0][0] - target_s)
            )
            matched = (begin, end, vals)

        if matched:
            b, e, v = matched
            speed_str = (f"{v['speed_mean_weighted']:.2f} m/s  "
                        f"(unweighted {v['speed_mean_simple']:.2f}, "
                        f"range {v['speed_min']:.2f}-{v['speed_max']:.2f})")
            density_str = (f"{v['density_mean']:.3f} vkl  "
                          f"(range {v['density_min']:.3f}-{v['density_max']:.3f})")
            interval_str = f"{seconds_to_hhmm(b)}–{seconds_to_hhmm(e)}"
            lines.append(f"  {label:<6}  {interval_str:<12}  "
                         f"{speed_str:<34}  {density_str}")
            lines.append(f"          ({v['n_edges']} edges sampled, "
                         f"{v['total_sampled_seconds']:.0f} total vehicle-seconds observed)")
        else:
            lines.append(f"  {label:<6}  no matching interval")

    lines.append("\n" + "=" * 70)

    # ── per-edge section ────────────────────────────────────────────────
    # Group aggregated data by edge
    by_edge = defaultdict(dict)
    for (begin, end, eid), vals in aggregated.items():
        by_edge[eid][(begin, end)] = vals

    for eid in report_edges:
        if eid not in by_edge:
            lines.append(f"\n  Edge {eid}: no data (not sampled in any run)")
            continue
        lines.append(f"\n  Edge: {eid}")
        lines.append(f"  {'Offset':<6}  {'Interval':<12}  "
                     f"{'Speed (m/s)':<30}  {'Density (veh/km/lane)'}")
        lines.append("  " + "-" * 66)

        for label, target_s in target_offsets.items():
            # Find the interval that contains target_s
            matched = None
            for (begin, end), vals in sorted(by_edge[eid].items()):
                if begin <= target_s <= end:
                    matched = (begin, end, vals)
                    break
            # fallback: interval whose begin is closest to target_s
            if matched is None:
                closest = min(by_edge[eid].items(),
                              key=lambda kv: abs(kv[0][0] - target_s),
                              default=None)
                if closest:
                    (begin, end), vals = closest
                    matched = (begin, end, vals)

            if matched:
                b, e, v = matched
                speed_str   = (f"{v['speed_mean']:.2f} m/s  "
                               f"(range {v['speed_min']:.2f} – {v['speed_max']:.2f})")
                density_str = (f"{v['density_mean']:.3f} vkl  "
                               f"(range {v['density_min']:.3f} – {v['density_max']:.3f})")
                interval_str = f"{seconds_to_hhmm(b)}–{seconds_to_hhmm(e)}"
                lines.append(f"  {label:<6}  {interval_str:<12}  "
                             f"{speed_str:<30}  {density_str}")
            else:
                lines.append(f"  {label:<6}  no matching interval")

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
                    help="Simulation second to start from (36000 = 10h)")
    ap.add_argument("--window",     type=int, default=7200,
                    help="Seconds to run forward (7200 = 2h)")
    ap.add_argument("--interval",   type=int, default=900,
                    help="edgeData aggregation interval in seconds (900 = 15min)")
    ap.add_argument("--reps",       type=int, default=8,
                    help="Number of Monte Carlo repetitions")
    ap.add_argument("--seed-start", type=int, default=1,
                    help="First random seed; subsequent seeds are seed-start+i")
    ap.add_argument("--output",     default=str(PROJECT_ROOT / "mc_results"),
                    help="Output directory")
    ap.add_argument("--sumo-bin",   default="sumo",
                    help="SUMO executable name (ignored when --sif is set)")
    ap.add_argument("--sif",        default="",
                    help="Path to SUMO Apptainer .sif image. "
                         "When set, runs via: apptainer exec -B <bind> <sif> sumo ...")
    ap.add_argument("--bind",       default="",
                    help="Apptainer bind mount: host_path:container_path "
                         "(default: <sumocfg parent>:/data)")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="Skip SUMO runs — read existing edgedata_mc.xml files "
                         "from output/run_XXXX/ and go straight to aggregation. "
                         "Use this in the Slurm aggregate job after mc_array.sh finishes.")
    ap.add_argument("--edges",      default="",
                    help="Comma-separated edge ids to include in report "
                         "(default: top 10 by mean density)")
    args = ap.parse_args()

    cfg        = Path(args.sumocfg).resolve()
    checkpoint = args.checkpoint
    end_time   = checkpoint + args.window
    out_root   = Path(args.output).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if not cfg.exists():
        log.error("sumocfg not found: %s", cfg)
        return 1

    log.info("=" * 60)
    log.info("Monte Carlo prediction")
    log.info("  sumocfg    : %s", cfg)
    log.info("  checkpoint : %s (%ds)", seconds_to_hhmm(checkpoint), checkpoint)
    log.info("  window     : %s → %s (%ds)",
             seconds_to_hhmm(checkpoint), seconds_to_hhmm(end_time), args.window)
    log.info("  interval   : %ds (%.0f min)", args.interval, args.interval / 60)
    log.info("  reps       : %d  (seeds %d..%d)",
             args.reps, args.seed_start, args.seed_start + args.reps - 1)
    log.info("  output     : %s", out_root)
    if args.sif:
        log.info("  runner     : apptainer exec -B %s %s sumo",
                 args.bind or f"{cfg.parent}:/data", args.sif)
    else:
        log.info("  runner     : %s (direct)", args.sumo_bin)
    log.info("=" * 60)

    all_run_data: list = []
    failed_seeds: list = []

    if args.aggregate_only:
        # Skip SUMO — read whatever edgedata_mc.xml files already exist
        log.info("Aggregate-only mode: scanning %s for existing run folders ...", out_root)
        found = sorted(out_root.glob("run_*/edgedata_mc.xml"))
        if not found:
            log.error("No edgedata_mc.xml files found in %s", out_root)
            return 1
        for edgedata_path in found:
            run_data = parse_edgedata(edgedata_path)
            log.info("  read %s → %d entries", edgedata_path.parent.name, len(run_data))
            if run_data:
                all_run_data.append(run_data)
    else:
        for i in range(args.reps):
            seed     = args.seed_start + i
            run_dir  = out_root / f"run_{seed:04d}"
            run_dir.mkdir(parents=True, exist_ok=True)

            edgedata_file = str((run_dir / "edgedata_mc.xml").resolve())

            add_path = write_mc_edgedata_add(
                run_dir, checkpoint, end_time, args.interval, edgedata_file
            )

            log.info("[%d/%d] Running seed=%d ...", i + 1, args.reps, seed)
            ok = run_sumo(
                sumo_bin     = args.sumo_bin,
                cfg          = cfg,
                seed         = seed,
                checkpoint   = checkpoint,
                end_time     = end_time,
                additional   = add_path,
                edgedata_out = Path(edgedata_file),
                run_dir      = run_dir,
                sif          = args.sif,
                bind         = args.bind,
            )
            if not ok:
                failed_seeds.append(seed)
                continue

            run_data = parse_edgedata(Path(edgedata_file))
            log.info("  → parsed %d (interval, edge) entries", len(run_data))
            all_run_data.append(run_data)

    if not all_run_data:
        log.error("All SUMO runs failed. Check sumo.log files in %s", out_root)
        return 1

    if failed_seeds:
        log.warning("%d runs failed (seeds: %s)", len(failed_seeds), failed_seeds)

    log.info("Aggregating %d successful runs ...", len(all_run_data))
    aggregated = aggregate(all_run_data)
    log.info("Aggregated %d (interval × edge) entries", len(aggregated))

    citywide = aggregate_citywide(aggregated)
    log.info("City-wide rows: %d timesteps", len(citywide))

    # Decide which edges to report
    if args.edges.strip():
        report_edges = [e.strip() for e in args.edges.split(",") if e.strip()]
    else:
        report_edges = top_edges_by_density(aggregated, n=10)
        log.info("Auto-selected top %d edges by density: %s",
                 len(report_edges), report_edges)

    # Write outputs
    write_edgedata_xml(aggregated, out_root / "mc_prediction.edg.xml")
    write_report(aggregated, citywide, report_edges,
                 checkpoint, args.window, len(all_run_data),
                 out_root / "mc_report.txt")

    # Write a machine-readable summary JSON for downstream tools
    summary_json = {
        "checkpoint_s":  checkpoint,
        "end_s":         end_time,
        "interval_s":    args.interval,
        "n_reps":        len(all_run_data),
        "failed_seeds":  failed_seeds,
        "report_edges":  report_edges,
        "citywide":      citywide_snapshot(citywide, checkpoint),
    }
    (out_root / "mc_run_meta.json").write_text(
        json.dumps(summary_json, indent=2), encoding="utf-8"
    )

    log.info("=" * 60)
    log.info("Done.  Results in: %s", out_root)
    log.info("  mc_prediction.edg.xml — per-edge×timestep prediction, sumo-gui-loadable")
    log.info("  mc_report.txt         — human-readable prediction (city-wide + per-edge)")
    log.info("  mc_run_meta.json      — machine-readable run metadata + city-wide snapshot")
    log.info("  run_XXXX/             — raw edgeData per seed")
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
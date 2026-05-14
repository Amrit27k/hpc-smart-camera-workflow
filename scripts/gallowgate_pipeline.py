#!/usr/bin/env python3
"""
======================================================================
Newcastle CCTV -- Vehicle Detection -> SUMO Pipeline
======================================================================
Filename pattern:  CM_A69A1-20250401-000324.jpg
Folder pattern:    CM_A69A1_202504/

Steps:
  1. Discover + sort images per camera folder
  2. Subsample to target interval (default 5 min)
  3. YOLOv8 vehicle detection, one image at a time
  4. Per-frame counts saved to CSV
  5. SUMO routes.xml / vtypes.xml / sumo.sumocfg generated
  6. Optional timelapse video per camera

Usage (Windows):
    python gallowgate_pipeline.py ^
        --base_dir "C:\\Users\\nak240\\Documents\\cctv_A_202504\\amrit" ^
        --interval 5 --conf 0.4 --video --sumo_network network.net.xml

Requirements:
    pip install -r requirements.txt
    ffmpeg in PATH (only needed for --video)
======================================================================
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# -- Optional deps ---------------------------------------------------
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False

try:
    from tqdm import tqdm
    def progress(it, **kw): return tqdm(it, **kw)
except ImportError:
    def progress(it, desc="", **kw):
        print(f"  {desc} ...")
        return it

# ====================================================================
# CONFIGURATION -- edit to match your SUMO network
# ====================================================================

EDGE_MAP = {
    "CM_A69A1": "47290415#6",   # replace with real SUMO edge IDs
}
DEFAULT_EDGE = "edge_unknown"

VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

VTYPE_COLOURS = {
    "car":        ("passenger",  "255,165,0"),
    "bus":        ("bus",        "0,0,255"),
    "truck":      ("truck",      "255,0,0"),
    "motorcycle": ("motorcycle", "0,200,0"),
}

# Image extensions to accept -- never include extensionless files
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}

# Folder names that are never camera folders (venv, git, system dirs)
SKIP_FOLDER_NAMES = {
    "include", "lib", "lib64", "scripts", "bin", "site-packages",
    "__pycache__", ".git", "share", "etc", "tcl", "tools",
    "doc", "man", "include", "runs", "pipeline_output", "smart-traffic",
}


# ====================================================================
# 1 -- FILENAME PARSING
# ====================================================================

_FNAME_RE = re.compile(
    r"(?P<cam>[A-Za-z0-9_]+)"
    r"-(?P<date>\d{8})"
    r"-(?P<time>\d{6})"
    r"(?:\.\w+)?$"
)


def parse_timestamp(filepath: str) -> datetime:
    """Parse datetime from CM_A69A1-20250401-000324.jpg style filename."""
    name = Path(filepath).name
    m = _FNAME_RE.match(name)
    if not m:
        m = _FNAME_RE.match(Path(filepath).stem)
    if m:
        d, t = m.group("date"), m.group("time")
        try:
            return datetime(int(d[:4]), int(d[4:6]), int(d[6:8]),
                            int(t[:2]), int(t[2:4]), int(t[4:6]))
        except ValueError:
            pass
    return datetime.fromtimestamp(os.path.getmtime(filepath))


def camera_id_from_folder(name: str) -> str:
    """'CM_A69A1_202504' -> 'CM_A69A1'"""
    m = re.match(r"^(.+?)_\d{6}$", name)
    return m.group(1) if m else name


def is_venv(path: Path) -> bool:
    """Return True if path looks like a Python virtualenv."""
    markers = ["pyvenv.cfg", "Scripts", "Lib", "Include",
               "bin", "lib", "include"]
    hits = sum(1 for mk in markers if (path / mk).exists())
    return hits >= 3


# ====================================================================
# 1 -- IMAGE DISCOVERY
# ====================================================================

def discover_images(base_dir: str,
                    camera_filter=None) -> dict:
    """
    Scan base_dir subfolders for images.
    Returns {camera_id: [(datetime, filepath), ...]} sorted by time.
    Automatically skips virtualenv and system folders.
    """
    cameras = {}
    base = Path(base_dir)

    if not base.exists():
        print(f"  [ERROR] base_dir not found: {base_dir}")
        sys.exit(1)

    for cam_dir in sorted(base.iterdir()):
        if not cam_dir.is_dir():
            continue

        # Skip system / venv folders
        if cam_dir.name.lower() in SKIP_FOLDER_NAMES:
            print(f"  [SKIP] {cam_dir.name} -- system folder name")
            continue
        if is_venv(cam_dir):
            print(f"  [SKIP] {cam_dir.name} -- looks like a virtualenv")
            continue

        cam_id = camera_id_from_folder(cam_dir.name)
        if camera_filter and cam_id not in camera_filter:
            continue

        images = []
        for fp in cam_dir.iterdir():
            if fp.is_file() and fp.suffix.lower() in IMAGE_EXTS:
                try:
                    ts = parse_timestamp(str(fp))
                    images.append((ts, str(fp)))
                except Exception:
                    pass

        if not images:
            print(f"  [SKIP] {cam_dir.name} -- no .jpg/.png images found")
            continue

        images.sort(key=lambda x: x[0])
        cameras[cam_id] = images

        span = images[-1][0] - images[0][0]
        avg_gap = span / max(len(images) - 1, 1)
        print(f"  {cam_id:<22} {len(images):>4} images | "
              f"{images[0][0].strftime('%Y-%m-%d')} -> "
              f"{images[-1][0].strftime('%Y-%m-%d')} | "
              f"avg gap ~{int(avg_gap.total_seconds() // 60)} min")

    return cameras


# ====================================================================
# 2 -- SUBSAMPLING
# ====================================================================

def subsample(images: list, interval_min: int) -> list:
    """Keep one frame per interval_min window."""
    if interval_min <= 0 or not images:
        return images
    result = [images[0]]
    last_ts = images[0][0]
    for ts, fp in images[1:]:
        if (ts - last_ts) >= timedelta(minutes=interval_min):
            result.append((ts, fp))
            last_ts = ts
    print(f"    Subsampled {len(images)} -> {len(result)} frames "
          f"(target >={interval_min} min gap)")
    return result


def peak_hours_only(images: list, windows=None) -> list:
    """Keep only AM / lunch / PM peak hour frames."""
    if windows is None:
        windows = [(7, 9), (12, 13), (17, 19)]
    result = [(ts, fp) for ts, fp in images
              if any(s <= ts.hour < e for s, e in windows)]
    print(f"    Peak-hours filter: {len(images)} -> {len(result)} frames")
    return result


# ====================================================================
# 3 & 4 -- YOLO DETECTION + COUNT EXTRACTION
# ====================================================================

def run_detection(images: list, cam_id: str, detections_dir: str,
                  conf: float = 0.4, model_name: str = "yolov8n.pt") -> list:

    if not YOLO_AVAILABLE:
        print("  [WARN] ultralytics not installed -- detection skipped.")
        print("         Install: pip install ultralytics")
        return []

    # Validate files strictly before passing anything to YOLO
    valid = []
    for ts, fp in images:
        p = Path(fp)
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            valid.append((ts, fp))
        else:
            print(f"  [SKIP] Not a valid image file: {fp}")

    if not valid:
        print("  [WARN] No valid image files after validation.")
        return []

    model = YOLO(model_name)
    print(f"  Running {model_name} on {len(valid)} frames ...")

    records = []
    for ts, fp in progress(valid,
                           desc=f"  Detecting [{cam_id}]",
                           total=len(valid)):
        try:
            # One image at a time -- prevents YOLO autocast_list
            # from scanning folder paths or non-image files
            results = model.predict(
                source=fp,
                classes=list(VEHICLE_CLASSES.keys()),
                conf=conf,
                save=True,
                save_txt=True,
                project=detections_dir,
                name=cam_id,
                exist_ok=True,
                verbose=False,
            )
            result = results[0]
        except Exception as e:
            print(f"  [WARN] Skipping {Path(fp).name}: {e}")
            continue

        counts = defaultdict(int)
        if result.boxes is not None:
            for cls_id in result.boxes.cls.tolist():
                label = VEHICLE_CLASSES.get(int(cls_id))
                if label:
                    counts[label] += 1

        records.append({
            "camera":     cam_id,
            "date":       ts.strftime("%Y-%m-%d"),
            "time":       ts.strftime("%H:%M:%S"),
            "timestamp":  ts.strftime("%Y-%m-%d %H:%M:%S"),
            "filepath":   fp,
            "car":        counts["car"],
            "motorcycle": counts["motorcycle"],
            "bus":        counts["bus"],
            "truck":      counts["truck"],
            "total":      sum(counts.values()),
        })

    return records


def save_csv(records: list, path: str):
    fields = ["camera", "date", "time", "timestamp",
              "car", "motorcycle", "bus", "truck", "total", "filepath"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(records)
    print(f"  CSV  -> {path}")


# ====================================================================
# 5 -- SUMO FILE GENERATION
# ====================================================================

def get_edge(cam_id: str) -> str:
    for key, edge in EDGE_MAP.items():
        if key in cam_id:
            return edge
    return DEFAULT_EDGE


def write_xml(root: ET.Element, path: Path):
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(path), encoding="unicode", xml_declaration=True)


def build_sumo_files(all_records: list, sumo_dir: str,
                     step_length: int = 300, network_path: str = ""):
    out = Path(sumo_dir)
    out.mkdir(parents=True, exist_ok=True)

    # vtypes.xml
    vt = ET.Element("routes")
    for vtype, (vclass, colour) in VTYPE_COLOURS.items():
        ET.SubElement(vt, "vType", id=vtype, vClass=vclass, color=colour)
    write_xml(vt, out / "vtypes.xml")

    # routes.xml
    rt = ET.Element("routes")
    seen = set()
    for rec in all_records:
        cam = rec["camera"]
        if cam not in seen:
            ET.SubElement(rt, "route", id=f"route_{cam}",
                          edges=get_edge(cam))
            seen.add(cam)

    sorted_recs = sorted(all_records, key=lambda r: r["timestamp"])
    # Find first record with actual vehicle detections
    first_detection = next(
        (r for r in sorted_recs if int(r.get("total", 0)) > 0),
        sorted_recs[0]  # fallback to first record if none found
    )
    # Start simulation 10 seconds before first detection
    base_time = datetime.strptime(first_detection["timestamp"],
                                  "%Y-%m-%d %H:%M:%S") - timedelta(seconds=10)
    veh_id = 0

    for rec in sorted_recs:
        ts = datetime.strptime(rec["timestamp"], "%Y-%m-%d %H:%M:%S")
        depart_base = int((ts - base_time).total_seconds())
        rid = f"route_{rec['camera']}"
        offset = 0
        for vtype in ("car", "motorcycle", "bus", "truck"):
            count = rec.get(vtype, 0)
            if not count:
                continue
            spacing = max(1, step_length // count)
            for _ in range(count):
                ET.SubElement(rt, "vehicle",
                              id=f"v{veh_id}", type=vtype,
                              route=rid,
                              depart=str(depart_base + offset))
                veh_id += 1
                offset += spacing

    write_xml(rt, out / "routes.xml")
    print(f"  routes.xml   -> {out / 'routes.xml'}  ({veh_id} vehicles)")

    net = network_path if network_path else "YOUR_NETWORK.net.xml"
    cfg = f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <input>
    <net-file        value="{net}"/>
    <route-files     value="vtypes.xml,routes.xml"/>
  </input>
  <time>
    <begin           value="0"/>
    <step-length     value="1"/>
  </time>
  <output>
    <tripinfo-output value="tripinfo.xml"/>
  </output>
</configuration>
"""
    cfg_path = out / "sumo.sumocfg"
    cfg_path.write_text(cfg, encoding="utf-8")
    print(f"  sumo.sumocfg -> {cfg_path}")
    print(f"  Run with:      sumo-gui -c {cfg_path}")


# ====================================================================
# 6 -- TIMELAPSE VIDEO (optional)
# ====================================================================

def make_timelapse(images: list, cam_id: str, out_dir: str, fps: int = 10):
    if shutil.which("ffmpeg") is None:
        print("  [WARN] ffmpeg not found -- video skipped.")
        return

    tmp = Path(out_dir) / f"_tmp_{cam_id}"
    tmp.mkdir(parents=True, exist_ok=True)
    ext = Path(images[0][1]).suffix or ".jpg"

    for i, (_, fp) in enumerate(images):
        dst = tmp / f"frame{i:05d}{ext}"
        if not dst.exists():
            shutil.copy2(fp, dst)

    out_video = Path(out_dir) / f"{cam_id}_timelapse.mp4"
    # Build a concat list file (works on Windows — no glob needed)
    concat_file = tmp / "concat.txt"
    with open(concat_file, "w") as f:
        for img in sorted(tmp.glob(f"frame?????.{ext.lstrip('.')}")):
            f.write(f"file '{img.resolve()}'\n")
            f.write(f"duration {1/fps}\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        str(out_video),
    ]
    print(f"  Timelapse: {len(images)} frames @ {fps} fps ...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"  Video -> {out_video}")
    else:
        print(f"  [ERROR] ffmpeg:\n{res.stderr[-400:]}")
    shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================
# MAIN
# ====================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Newcastle CCTV: vehicle detection -> SUMO pipeline",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument(
        "--base_dir",
        default=r"H:\Desktop\Projects\network-plus",
        help="Parent folder containing CM_A69A1_202504 sub-folders"
    )
    ap.add_argument("--output_dir",   default="./pipeline_output")
    ap.add_argument("--interval",     type=int,   default=5,
                    help="Target gap between frames in minutes (0=use all)")
    ap.add_argument("--peak_only",    action="store_true",
                    help="Keep only 07-09, 12-13, 17-19 frames")
    ap.add_argument("--conf",         type=float, default=0.4,
                    help="YOLO confidence threshold (default 0.4)")
    ap.add_argument("--model",        default="./models/yolov8n.pt",
                    help="yolov8n.pt=fast | yolov8x.pt=accurate")
    ap.add_argument("--video",        action="store_true",
                    help="Build timelapse .mp4 per camera (needs ffmpeg)")
    ap.add_argument("--fps",          type=int,   default=10,
                    help="Timelapse playback fps (default 10)")
    ap.add_argument("--sumo_network", default=r"H:\Desktop\Projects\network-plus\pipeline_output\sumo\newcastle_stjames-full.net.xml",
                    help="Path to SUMO .net.xml network file"
    )  # replaced below
    ap.add_argument("--_dummy", default="", help=argparse.SUPPRESS)
    ap.add_argument("--step_length",  type=int,   default=300,
                    help="Seconds per camera timestep for SUMO (default 300)")
    ap.add_argument("--cameras",      nargs="*",
                    help="Only process these camera IDs e.g. CM_A69A1 CM_A69B2")
    args = ap.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    sep = "=" * 58
    print(f"\n{sep}")
    print("  NEWCASTLE CCTV -- VEHICLE DETECTION + SUMO PIPELINE")
    print(sep)
    print(f"  base_dir    : {args.base_dir}")
    print(f"  output_dir  : {out.resolve()}")
    print(f"  interval    : {args.interval} min  |  peak_only: {args.peak_only}")
    print(f"  model       : {args.model}  |  conf: {args.conf}")
    print()

    # 1. Discover
    print("[1/5] Scanning camera folders ...")
    cameras = discover_images(args.base_dir, camera_filter=args.cameras)
    if not cameras:
        print("  No camera folders found -- check --base_dir.")
        sys.exit(1)

    all_records = []

    for cam_id, images in cameras.items():
        print(f"\n{'-'*58}")
        print(f"  Camera: {cam_id}  ({len(images)} images)")

        # 2. Filter / subsample
        print("[2/5] Filtering frames ...")
        if args.peak_only:
            images = peak_hours_only(images)
        images = subsample(images, args.interval)
        if not images:
            print("  No frames left after filtering -- skipping.")
            continue

        # 3 & 4. Detect
        print("[3/5] Running vehicle detection ...")
        records = run_detection(
            images, cam_id,
            detections_dir=str(out / "detections"),
            conf=args.conf,
            model_name=args.model,
        )
        if records:
            save_csv(records, str(out / f"{cam_id}_counts.csv"))
            all_records.extend(records)

        # 6. Video (optional)
        if args.video:
            print("[6] Building timelapse ...")
            make_timelapse(images, cam_id, str(out), fps=args.fps)

    # 5. SUMO
    print(f"\n{'-'*58}")
    if all_records:
        print("[5/5] Generating SUMO demand files ...")
        build_sumo_files(
            all_records,
            sumo_dir=str(out / "sumo"),
            step_length=args.step_length,
            network_path=args.sumo_network,
        )
        save_csv(all_records, str(out / "all_cameras_counts.csv"))
    else:
        print("[5/5] Skipped -- no detection data available.")

    print(f"\n{sep}")
    print(f"  DONE -- outputs in: {out.resolve()}")
    print(sep + "\n")

    print("Output files:")
    for p in sorted(out.rglob("*")):
        if "_tmp_" not in str(p) and p.is_file():
            print(f"  {p.relative_to(out)}")


if __name__ == "__main__":
    main()
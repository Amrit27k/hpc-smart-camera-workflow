#!/usr/bin/env python3
"""
collect_edge_configs.py
=======================
Runs as a cron job every 2 hours on the collection edge node.

ASSUMPTION: each edge node pushes its latest SUMO input files into a
shared drop-zone directory with the layout:

    edge_data_inbox/
        <node_id>/
            routes_<node_id>.xml      ← route file for that node
            vtypes_<node_id>.xml      ← vtype definitions (optional)
            edgedata_<node_id>.xml    ← latest edgeData output (optional)

This script:
  1. Scans every sub-folder in INBOX_DIR for the latest version of each
     recognised file type.
  2. Merges all route files into one merged_routes.xml (deduplicating
     vType definitions, re-prefixing vehicle / route ids with node_id so
     they never collide).
  3. Copies the first vtypes file found as the canonical vtypes.xml (they
     are identical across nodes by convention; log a warning if they differ).
  4. Writes a combined edgedata_merged.xml that concatenates all interval
     blocks from every node's edgeData dump, tagging each edge id with a
     node prefix so downstream analysis knows where data originated.
  5. Stores all merged artefacts in OUTPUT_DIR with a timestamped sub-folder
     so every collection run is retained.
  6. Writes a manifest JSON summarising which nodes contributed, how many
     vehicles / edges each provided, and any nodes that were missing or stale.

Intended invocation (Windows Task Scheduler or cron on Linux):
    python collect_edge_configs.py [--inbox PATH] [--output PATH] [--stale-hours N]

To wire up as a Windows Scheduled Task every 2 hours:
    schtasks /create /tn "CollectEdgeConfigs" /tr "python <abs_path>/collect_edge_configs.py" ^
             /sc hourly /mo 2 /st 00:00
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# ── defaults (override via CLI or environment) ───────────────────────────────
DEFAULT_INBOX  = Path(__file__).resolve().parent.parent / "edge_data_inbox"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "collected_configs"
DEFAULT_STALE_HOURS = 3   # warn if a node's file is older than this


# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("collect")


# ── helpers ──────────────────────────────────────────────────────────────────

def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def find_latest(folder: Path, pattern: str):
    """Return the most-recently-modified file matching glob pattern, or None."""
    candidates = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def is_stale(path: Path, stale_hours: int) -> bool:
    age_s = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return age_s > stale_hours * 3600


def indent_xml(elem: ET.Element, level: int = 0) -> None:
    """In-place pretty-print for ElementTree (stdlib ET lacks this pre-3.9)."""
    pad = "\n" + "  " * level
    if len(elem):
        elem.text = pad + "  "
        for child in elem:
            indent_xml(child, level + 1)
        child.tail = pad  # type: ignore[assignment]
    if level:
        elem.tail = pad


# ── per-node discovery ────────────────────────────────────────────────────────

def discover_nodes(inbox: Path, stale_hours: int) -> dict:
    """
    Return a dict  node_id -> {routes, vtypes, edgedata, stale_flags}
    for every sub-directory found in inbox.
    """
    nodes = {}
    if not inbox.exists():
        log.error("Inbox directory does not exist: %s", inbox)
        return nodes

    for node_dir in sorted(inbox.iterdir()):
        if not node_dir.is_dir():
            continue
        nid = node_dir.name
        info: dict = {"node_id": nid, "dir": node_dir, "stale": [], "missing": []}

        for key, pattern in [
            ("routes",   "routes_*.xml"),
            ("vtypes",   "vtypes_*.xml"),
            ("edgedata", "edgedata_*.xml"),
        ]:
            found = find_latest(node_dir, pattern)
            if found:
                info[key] = found
                if is_stale(found, stale_hours):
                    info["stale"].append(key)
                    log.warning("STALE  node=%-20s  %s  (%.1fh old)",
                                nid, key, (datetime.now(timezone.utc).timestamp() - found.stat().st_mtime) / 3600)
            else:
                info[key] = None
                info["missing"].append(key)

        if info.get("routes") is None:
            log.warning("SKIP   node=%-20s  no routes file found", nid)
        else:
            log.info("FOUND  node=%-20s  routes=%s", nid, info["routes"].name)

        nodes[nid] = info
    return nodes


# ── merge routes ──────────────────────────────────────────────────────────────

def merge_routes(nodes: dict) -> ET.Element:
    """
    Merge all routes_*.xml files into a single <routes> element.

    Rules:
      - vType elements: keep only unique ids (first occurrence wins).
        Log a warning if the same id has different attributes across nodes.
      - <route> and <vehicle>/<flow> elements: prefix id with node_id to
        avoid collisions.  vehicle@route references are rewritten to match.
    """
    root = ET.Element("routes")
    seen_vtypes = {}   # id -> attrib dict
    total_vehicles = 0

    for nid, info in nodes.items():
        if info.get("routes") is None:
            continue
        try:
            tree = ET.parse(info["routes"])
        except ET.ParseError as exc:
            log.error("XML parse error in %s: %s", info["routes"], exc)
            continue

        src_root = tree.getroot()
        route_id_map = {}   # old route id -> new prefixed id

        # ── vTypes ──
        for vt in src_root.findall("vType"):
            vid = vt.get("id", "")
            attribs = dict(vt.attrib)
            if vid not in seen_vtypes:
                seen_vtypes[vid] = attribs
                root.append(vt)
            elif seen_vtypes[vid] != attribs:
                log.warning("vType id=%s differs between nodes; using first seen", vid)

        # ── routes ──
        for route in src_root.findall("route"):
            old_id = route.get("id", "")
            new_id = f"{nid}__{old_id}" if old_id else f"{nid}__route_{id(route)}"
            route_id_map[old_id] = new_id
            route.set("id", new_id)
            root.append(route)

        # ── vehicles / flows ──
        for tag in ("vehicle", "flow", "trip"):
            for elem in src_root.findall(tag):
                old_id = elem.get("id", "")
                elem.set("id", f"{nid}__{old_id}")
                ref = elem.get("route")
                if ref and ref in route_id_map:
                    elem.set("route", route_id_map[ref])
                root.append(elem)
                total_vehicles += 1

    log.info("Merged routes: %d vehicle/flow entries across %d nodes",
             total_vehicles, sum(1 for n in nodes.values() if n.get("routes")))
    return root


# ── merge vtypes (canonical copy) ────────────────────────────────────────────

def pick_canonical_vtypes(nodes: dict):
    """Return the first vtypes file found; warn if hashes differ."""
    files = [n["vtypes"] for n in nodes.values() if n.get("vtypes")]
    if not files:
        return None
    ref_hash = file_hash(files[0])
    for f in files[1:]:
        if file_hash(f) != ref_hash:
            log.warning("vtypes files differ across nodes — using %s", files[0])
            break
    return files[0]


# ── merge edgeData ────────────────────────────────────────────────────────────

def merge_edgedata(nodes: dict) -> ET.Element | None:
    """
    Concatenate all edgeData interval blocks, tagging each edge id with
    the originating node prefix so downstream tooling can filter by node.
    """
    any_found = any(n.get("edgedata") for n in nodes.values())
    if not any_found:
        return None

    root = ET.Element("meandata")

    for nid, info in nodes.items():
        if info.get("edgedata") is None:
            continue
        try:
            tree = ET.parse(info["edgedata"])
        except ET.ParseError as exc:
            log.error("XML parse error in %s: %s", info["edgedata"], exc)
            continue

        for interval in tree.getroot().findall("interval"):
            new_interval = ET.SubElement(root, "interval",
                                         begin=interval.get("begin", "0"),
                                         end=interval.get("end", "0"),
                                         id=f"{nid}__{interval.get('id', 'data')}",
                                         node=nid)
            for edge in interval.findall("edge"):
                new_edge = ET.SubElement(new_interval, "edge")
                new_edge.attrib = dict(edge.attrib)
                new_edge.set("id", f"{nid}__{edge.get('id', '')}")

    log.info("Merged edgeData from %d nodes",
             sum(1 for n in nodes.values() if n.get("edgedata")))
    return root


# ── write outputs ─────────────────────────────────────────────────────────────

def write_xml(elem: ET.Element, path: Path) -> None:
    indent_xml(elem)
    ET.ElementTree(elem).write(str(path), encoding="unicode", xml_declaration=True)
    log.info("Wrote %s  (%d bytes)", path.name, path.stat().st_size)


def write_manifest(nodes: dict, out_dir: Path, ts: str) -> None:
    manifest = {
        "collection_run": ts,
        "nodes": {},
    }
    for nid, info in nodes.items():
        manifest["nodes"][nid] = {
            "routes":   str(info["routes"])   if info.get("routes")   else None,
            "vtypes":   str(info["vtypes"])   if info.get("vtypes")   else None,
            "edgedata": str(info["edgedata"]) if info.get("edgedata") else None,
            "stale":    info.get("stale", []),
            "missing":  info.get("missing", []),
        }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("Wrote manifest.json")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Collect and merge SUMO config files from edge nodes every 2 hours.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--inbox",       default=str(DEFAULT_INBOX),
                    help="Root directory where edge nodes drop their files")
    ap.add_argument("--output",      default=str(DEFAULT_OUTPUT),
                    help="Root directory for merged outputs")
    ap.add_argument("--stale-hours", type=int, default=DEFAULT_STALE_HOURS,
                    help="Warn if a node file is older than this many hours")
    args = ap.parse_args()

    inbox  = Path(args.inbox)
    output = Path(args.output)
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = output / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Collection run  %s", ts)
    log.info("Inbox  : %s", inbox)
    log.info("Output : %s", out_dir)
    log.info("=" * 60)

    # 1. Discover nodes
    nodes = discover_nodes(inbox, args.stale_hours)
    active = [n for n in nodes.values() if n.get("routes")]
    if not active:
        log.error("No active nodes with route files found. Exiting.")
        return 1

    log.info("%d / %d nodes have route files", len(active), len(nodes))

    # 2. Merge routes
    merged_routes = merge_routes(nodes)
    write_xml(merged_routes, out_dir / "merged_routes.xml")

    # 3. Canonical vtypes
    canonical_vtypes = pick_canonical_vtypes(nodes)
    if canonical_vtypes:
        dest = out_dir / "vtypes.xml"
        shutil.copy2(canonical_vtypes, dest)
        log.info("Copied vtypes.xml from %s", canonical_vtypes.parent.name)
    else:
        log.warning("No vtypes files found in any node — vtypes.xml not written")

    # 4. Merge edgeData (best-effort; may not exist on first runs)
    merged_edgedata = merge_edgedata(nodes)
    if merged_edgedata is not None:
        write_xml(merged_edgedata, out_dir / "edgedata_merged.xml")
    else:
        log.info("No edgeData files found — skipping edgedata_merged.xml")

    # 5. Write a ready-to-use sumocfg pointing at merged files
    net_src = Path(__file__).resolve().parent.parent / "pipeline_output" / "sumo" / "newcastle_stjames-full.net.xml"
    cfg_lines = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- Auto-generated by collect_edge_configs.py  run={ts} -->
<configuration>
  <input>
    <net-file         value="{net_src}"/>
    <route-files      value="{out_dir / 'vtypes.xml'},{out_dir / 'merged_routes.xml'}"/>
    <additional-files value="{Path(__file__).resolve().parent.parent / 'pipeline_output' / 'sumo' / 'edgedata_cfg.add.xml'}"/>
  </input>
  <time>
    <begin            value="0"/>
    <end              value="86400"/>
    <step-length      value="1"/>
  </time>
  <mesoscopic>
    <mesosim          value="true"/>
    <meso-edgelength  value="150"/>
  </mesoscopic>
  <output>
    <tripinfo-output  value="{out_dir / 'tripinfo.xml'}"/>
  </output>
  <report>
    <no-step-log      value="true"/>
    <verbose          value="true"/>
  </report>
</configuration>
"""
    cfg_path = out_dir / "merged.sumocfg"
    cfg_path.write_text(cfg_lines, encoding="utf-8")
    log.info("Wrote merged.sumocfg")

    # 6. Manifest
    write_manifest(nodes, out_dir, ts)

    log.info("=" * 60)
    log.info("Done. Merged config ready at: %s", out_dir)
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

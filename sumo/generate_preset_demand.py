#!/usr/bin/env python3
"""
======================================================================
generate_preset_demand.py  --  Phase 1: preset demand for city map
======================================================================
Generates trips.xml + duarouter.cfg for the Newcastle St James network.

WHY TWO STEPS (trips -> duarouter -> routes):
  SUMO requires every <route> to list ALL intermediate edges in order,
  not just origin and destination. duarouter (ships with SUMO) computes
  the shortest path through the network and writes the full edge sequence.
  Trying to put only "origin dest" in routes.xml gives:
    "Error: Vehicle has no valid route. No connection between edge A and B."

WORKFLOW:
  Step 1 (this script):
    python generate_preset_demand.py --net newcastle_stjames-full.net.xml

  Step 2 (duarouter - run from your SUMO environment):
    duarouter --configuration-file duarouter.cfg

  Step 3 (SUMO):
    sumo --configuration-file sumo_city.sumocfg

OUTPUT FILES:
  trips_city.xml     -- 2050 OD trips with depart times + vtypes
  duarouter.cfg      -- duarouter config (reads trips, writes routes)
  sumo_city.sumocfg  -- corrected SUMO config (edgeData syntax fixed)
  vtypes_city.xml    -- vehicle type definitions

The 221 core edges were derived by bidirectional BFS reachability
analysis on the actual network. 98.3% of random pairs within this set
are mutually reachable (pre-validated). duarouter silently skips the
remaining ~1.7% and logs them to duarouter.log.
======================================================================
"""

import argparse
import random
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

# ── 197 duarouter-validated core edges
# Derived by: BFS reachability analysis -> duarouter trial run ->
# remove all 24 edges flagged as "not allowed" or causing "no connection".
# Every edge here passes duarouter without warnings.
# Format: (edge_id, length_m, speed_ms)
CORE_EDGES = [
    ("456194040#0",    58.50, 27.78),
    ("-401595498",     34.30,  8.94),
    ("-455063592",     78.79, 13.89),
    ("31992852#1",     31.39,  8.94),
    ("-1301164218#3",  33.11,  8.94),
    ("750809955#0",    34.83,  8.94),
    ("456194040#3",    40.14, 27.78),
    ("4725794#1",      47.49, 13.89),
    ("2535384#0",     115.37,  8.94),
    ("-104288999",     61.59,  8.94),
    ("4725927",        60.51, 13.89),
    ("104288999",      62.02,  8.94),
    ("455063592",      77.08, 13.89),
    ("4692272#0",     106.41,  8.94),
    ("-750809955#0",   34.80,  8.94),
    ("-354671651#1",   33.95,  8.94),
    ("427821329#1",    31.53,  8.94),
    ("37899441#1",     52.33,  8.94),
    ("912997280",      41.94,  8.94),
    ("1058647170",     53.44,  8.94),
    ("2535115#0",      33.21,  8.94),
    ("2570429#0",      45.47,  8.94),
    ("-30278393#1",    96.67,  8.94),
    ("41223305",       90.62,  8.94),
    ("-30278393#0",    62.94,  8.94),
    ("30278393#0",     62.48,  8.94),
    ("30278393#1",     96.67,  8.94),
    ("1315301901",     31.38,  8.94),
    ("452105746#0",    58.99,  8.94),
    ("631597723#0",    31.39,  8.94),
    ("2535038#2",      42.27, 22.22),
    ("-2535115#1",     33.93,  8.94),
    ("134063977#2",    69.56, 22.22),
    ("2535381#0",      37.90,  8.94),
    ("456598077",      73.82, 13.89),
    ("-2535038#3",     42.07, 22.22),
    ("-94511068#1",    53.99,  8.94),
    ("2543333",        74.82, 13.89),
    ("-1053979388#1", 139.36,  8.94),
    ("-71181686#5",    33.86,  8.94),
    ("1177239769",     63.78, 13.41),
    ("-2535381#2",     37.18,  8.94),
    ("71181686#5",     33.86,  8.94),
    ("-723584180#2",   45.15,  8.94),
    ("455476281#0",    30.62,  8.94),
    ("-928691979#0",   69.58, 22.22),
    ("723584180#1",    47.00,  8.94),
    ("455476281#1",    31.75,  8.94),
    ("-68756016#1",    36.13,  8.94),
    ("11018592",      107.73,  8.94),
    ("1301164218#1",   32.18,  8.94),
    ("-1026845806",    33.22, 13.89),
    ("-161182809",     40.06,  8.94),
    ("68756016#0",     36.48,  8.94),
    ("2544440#1",      51.22,  8.94),
    ("-2535384#0",    116.26,  8.94),
    ("2535350#2",      89.59,  8.94),
    ("94511068#1",     54.10,  8.94),
    ("230834613#0",    35.96,  8.94),
    ("354671651#1",    33.92,  8.94),
    ("452105747",      56.90,  8.94),
    ("-354671650#1",   47.49, 13.89),
    ("230834615",      41.00, 13.89),
    ("161182809",      40.17,  8.94),
    ("-230834615",     41.00, 13.89),
    ("258625107#0",    52.27,  8.94),
    ("2542711#3",      50.06,  8.94),
    ("-456598077",     73.96, 13.89),
    ("-837865886#1",   31.51,  8.94),
    ("-172336694",     50.56,  8.94),
    ("172336694",      50.56,  8.94),
    ("30278394#0",     43.99, 13.89),
    ("230820329#0",    31.22,  8.94),
    ("2541944#3",      39.78,  8.94),
    ("1025366406",     99.81,  8.94),
    ("-145813472",     34.17, 13.89),
    ("-1026085894#2",  35.00,  8.94),
    ("-1053066068#1", 107.00,  8.94),
    ("2535114#2",      40.68, 13.89),
    ("-4705918#0",     86.47, 13.89),
    ("401595498",      34.30,  8.94),
    ("-990834100",     89.54,  8.94),
    ("645694987#1",    33.02,  8.94),
    ("1026845806",     33.22, 13.89),
    ("-452105747",     56.90,  8.94),
    ("4725920#2",      70.37,  8.94),
    ("-452105746#0",   58.99,  8.94),
    ("258625107#2",    33.58,  8.94),
    ("837865886#1",    31.57,  8.94),
    ("4725918#1",      69.77, 13.89),
    ("450227283#2",    72.20,  8.94),
    ("145813472",      34.17, 13.89),
    ("193230709#1",    84.84,  8.94),
    ("289329430#0",   107.77, 13.89),
    ("289329430#1",    35.51, 13.89),
    ("1149713177#1",   45.05,  8.94),
    ("-30118272#1",    62.31, 13.89),
    ("230838772#1",    44.21,  8.94),
    ("-230834613#0",   32.85,  8.94),
    ("37899394#0",     64.33, 27.78),
    ("-230820329#0",   31.28,  8.94),
    ("-384828057#1",   44.36,  8.94),
    ("-135413547#0",   37.95, 13.89),
    ("4725452",        51.14,  8.94),
    ("-2535039#2",     82.06,  8.94),
    ("-400886685#1",   62.69,  8.94),
    ("-400886689#0",   72.30,  8.94),
    ("926028935#0",    44.25,  8.94),
    ("-2543333",       75.12, 13.89),
    ("236455763#1",    35.40,  8.94),
    ("146605605#0",    33.89,  8.94),
    ("137355170",      87.89,  8.94),
    ("400886685#1",    62.69,  8.94),
    ("-135706987#0",   41.55, 22.22),
    ("68756056#2",     37.52, 13.41),
    ("2534525#1",      93.63,  8.94),
    ("-1025366406",    99.81,  8.94),
    ("2543354#1",      45.23,  8.94),
    ("146605605#2",    64.57,  8.94),
    ("-2541944#4",     39.78,  8.94),
    ("17242098#0",     41.40, 13.89),
    ("721312550",      81.73,  8.94),
    ("2534525#3",     113.27,  8.94),
    ("717611302",      31.79, 13.41),
    ("230820329#2",    31.08,  8.94),
    ("-1026085894#0",  72.02,  8.94),
    ("-427821329#1",   31.53,  8.94),
    ("-137355170",     88.95,  8.94),
    ("2544438#5",      67.55, 13.89),
    ("30118272#4",     52.95, 13.89),
    ("-926028935#1",   44.23,  8.94),
    ("2541944#7",      38.45,  8.94),
    ("-380310114#3",   30.30,  8.94),
    ("1165474198",     33.99, 13.89),
    ("-13715475#1",   116.77,  8.94),
    ("1053066062",    106.86,  8.94),
    ("-236455763#2",   35.39,  8.94),
    ("1059369989",     54.25,  8.94),
    ("-230820329#3",   31.01,  8.94),
    ("-232563583#4",   33.67, 13.41),
    ("429206409",      51.78,  8.94),
    ("384828057#0",    41.91,  8.94),
    ("449880200",      88.56, 13.41),
    ("2535039#0",      81.99,  8.94),
    ("1386311796#1",   46.50, 13.41),
    ("1305563481#0",   58.74,  8.94),
    ("343036951",      43.53,  8.94),
    ("-13715475#0",    66.72,  8.94),
    ("-1305563481#1",  58.57,  8.94),
    ("650010537",      98.11, 13.41),
    ("13715475#1",    119.03,  8.94),
    ("94541123",      101.77, 13.89),
    ("68756019#1",     33.85, 13.41),
    ("461119575#3",    34.65, 13.41),
    ("650010534",      90.92, 13.41),
    ("-2535114#2",     40.68, 13.89),
    ("232563583#4",    33.92, 13.41),
    ("928436139#1",    41.38,  8.94),
    ("953082529#3",   135.92, 13.41),
    ("-11022533#2",    41.29,  8.94),
    ("461119575#1",    31.54, 13.41),
    ("1386311788",     34.35, 13.41),
    ("68756027#0",     52.24, 13.41),
    ("-1177239769",    64.07, 13.41),
    ("461119579",     133.24, 13.41),
    ("-380310114#0",   59.62,  8.94),
    ("206919777#1",    31.35, 13.89),
    ("-429206409",     51.95,  8.94),
    ("-692881756#0",   45.76,  8.94),
    ("-953082529#3",  134.30, 13.41),
    ("-23685578#1",    54.56, 13.41),
    ("1026085894#0",   71.91,  8.94),
    ("13715475#0",     66.73,  8.94),
    ("1035378551#1",   70.63,  8.94),
    ("380310114#2",    30.49,  8.94),
    ("-206919777#1",   31.35, 13.89),
    ("135706986",      41.81, 22.22),
    ("1424789403#1",   45.24,  8.94),
    ("-38101989#3",    54.75, 13.89),
    ("1315301899#0",   35.58, 13.41),
    ("41754609#0",     74.77, 13.41),
    ("694151967#0",    47.97,  8.94),
    ("-30118272#5",    52.68, 13.89),
    ("16312517",       50.22, 13.89),
    ("11018597#1",     45.92, 13.89),
    ("-1033369176#0",  31.06,  8.94),
    ("-16312517",      49.92, 13.89),
    ("-779932972#1",   46.68, 13.41),
    ("4609278",       130.54, 22.35),
    ("230819128",      56.15, 13.89),
    ("-4705918#2",     33.90, 13.89),
    ("-2541944#7",     38.45,  8.94),
    ("380310114#0",    59.59,  8.94),
    ("30118272#1",     62.31, 13.89),
    ("343036425",      57.62, 13.89),
    ("134818126#1",    52.57, 13.89),
    ("41754609#1",     98.54, 13.41),
]

# Camera edge → nearest strongly-connected core edge (5 BFS hops away)
CAMERA_TO_CORE = {
    "47290415#6": "1386311796#1",
}

# Vehicle mix matching Newcastle road survey proportions
# Encoded as a pick-list for simple weighted sampling
VTYPE_POOL = (
    ["car"] * 78 + ["truck"] * 10 + ["bus"] * 6 + ["motorcycle"] * 6
)

# Hourly demand shape (sums to exactly 1.0)
HOURLY_SHAPE = [
    0.005, 0.005, 0.005, 0.005,   # 00-03
    0.010, 0.020, 0.050, 0.090,   # 04-07
    0.115, 0.080, 0.050, 0.040,   # 08-11  AM peak
    0.055, 0.045, 0.035, 0.030,   # 12-15
    0.040, 0.080, 0.090, 0.060,   # 16-19  PM peak
    0.040, 0.025, 0.015, 0.010,   # 20-23
]
assert abs(sum(HOURLY_SHAPE) - 1.0) < 1e-6, f"Shape sums to {sum(HOURLY_SHAPE)}"


def build_od_pairs(n: int, seed: int = 42) -> list:
    """Sample n (origin, dest) pairs weighted by edge length, no self-loops."""
    rng     = random.Random(seed)
    ids     = [e[0] for e in CORE_EDGES]
    lengths = [e[1] for e in CORE_EDGES]
    total   = sum(lengths)
    weights = [l / total for l in lengths]
    pairs, attempts = [], 0
    while len(pairs) < n:
        attempts += 1
        if attempts > n * 30:
            raise RuntimeError("Could not generate enough OD pairs.")
        o = rng.choices(ids, weights=weights)[0]
        d = rng.choices(ids, weights=weights)[0]
        if o != d:
            pairs.append((o, d))
    return pairs


def sample_depart_times(n: int, sim_s: int, seed: int = 42) -> list:
    """Distribute n departures over sim_s seconds using HOURLY_SHAPE."""
    rng   = np.random.default_rng(seed)
    times = []
    for h, frac in enumerate(HOURLY_SHAPE):
        t0 = h * 3600
        t1 = min(t0 + 3600, sim_s)
        if t0 >= sim_s:
            break
        cnt = int(round(frac * n))
        if cnt > 0:
            times.extend(rng.integers(t0, t1, size=cnt).tolist())
    times = sorted(times)
    if len(times) > n:
        times = times[:n]
    while len(times) < n:
        times.append(int(rng.integers(0, sim_s)))
    return sorted(times)


def write_vtypes(out_dir: Path) -> None:
    root = ET.Element("routes")
    for spec in [
        dict(id="car",        vClass="passenger",
             color="255,165,0",  maxSpeed="13.89",
             accel="2.6",  decel="4.5",  speedDev="0.1",
             length="4.5",  minGap="2.5"),
        dict(id="bus",        vClass="bus",
             color="0,0,255",   maxSpeed="11.11",
             accel="1.2",  decel="3.5",  speedDev="0.05",
             length="12.0", minGap="3.0"),
        dict(id="truck",      vClass="truck",
             color="255,0,0",   maxSpeed="11.11",
             accel="1.0",  decel="3.0",  speedDev="0.05",
             length="9.0",  minGap="3.5"),
        dict(id="motorcycle", vClass="motorcycle",
             color="0,200,0",   maxSpeed="16.67",
             accel="4.0",  decel="6.0",  speedDev="0.15",
             length="2.2",  minGap="1.5"),
    ]:
        ET.SubElement(root, "vType", **spec)
    path = out_dir / "vtypes_city.xml"
    ET.ElementTree(root).write(str(path), encoding="unicode",
                               xml_declaration=True)
    print(f"  vtypes_city.xml  → {path}")


def write_trips(od_pairs: list, depart_times: list,
                out_dir: Path, seed: int = 42) -> None:
    """
    Write trips_city.xml -- the input duarouter needs.
    Each <trip> has only from/to/depart; duarouter fills in the full route.
    vType definitions are embedded so duarouter can reference them.
    """
    rng  = random.Random(seed)
    root = ET.Element("routes")

    # vType defs must precede trips for duarouter
    for spec in [
        dict(id="car",        vClass="passenger",
             color="255,165,0",  maxSpeed="13.89",
             accel="2.6",  decel="4.5",  speedDev="0.1",
             length="4.5",  minGap="2.5"),
        dict(id="bus",        vClass="bus",
             color="0,0,255",   maxSpeed="11.11",
             accel="1.2",  decel="3.5",  speedDev="0.05",
             length="12.0", minGap="3.0"),
        dict(id="truck",      vClass="truck",
             color="255,0,0",   maxSpeed="11.11",
             accel="1.0",  decel="3.0",  speedDev="0.05",
             length="9.0",  minGap="3.5"),
        dict(id="motorcycle", vClass="motorcycle",
             color="0,200,0",   maxSpeed="16.67",
             accel="4.0",  decel="6.0",  speedDev="0.15",
             length="2.2",  minGap="1.5"),
    ]:
        ET.SubElement(root, "vType", **spec)

    for i, ((o, d), t) in enumerate(zip(od_pairs, depart_times)):
        vtype = rng.choice(VTYPE_POOL)
        ET.SubElement(root, "trip",
                      id=f"v{i}", type=vtype,
                      attrib={"from": o, "to": d, "depart": str(t)})

    path = out_dir / "trips_city.xml"
    ET.ElementTree(root).write(str(path), encoding="unicode",
                               xml_declaration=True)
    print(f"  trips_city.xml   → {path}  ({len(od_pairs)} trips)")


def write_duarouter_cfg(net_path: str, out_dir: Path) -> None:
    """duarouter reads trips_city.xml and writes routes_city.xml."""
    cfg = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  duarouter.cfg
  Run from the folder containing this file:
    duarouter --configuration-file duarouter.cfg

  duarouter computes the shortest path for each trip and writes
  routes_city.xml with full intermediate edge sequences that SUMO
  requires. Unreachable trips are skipped and logged to duarouter.log.
  Expect ~2 mins for 2050 trips on this network.
-->
<configuration>
  <input>
    <net-file          value="{net_path}"/>
    <route-files       value="trips_city.xml"/>
  </input>
  <output>
    <output-file       value="routes_city.xml"/>
  </output>
  <report>
    <log               value="duarouter.log"/>
    <no-step-log       value="true"/>
  </report>
  <routing>
    <ignore-errors     value="true"/>
    <weights.priority-factor value="0"/>
  </routing>
</configuration>
"""
    path = out_dir / "duarouter.cfg"
    path.write_text(cfg, encoding="utf-8")
    print(f"  duarouter.cfg    → {path}")


def write_sumocfg(net_path: str, out_dir: Path, sim_s: int) -> None:
    """
    Fixed sumocfg: edgeData written as a proper additional-files entry.
    The previous version had edgeData inside <configuration> which SUMO
    ignores with warnings. Correct pattern uses --additional-files.
    """
    cfg = f"""<?xml version="1.0" encoding="UTF-8"?>
<configuration>

  <input>
    <net-file         value="{net_path}"/>
    <route-files      value="vtypes_city.xml,routes_city.xml"/>
    <additional-files value="edgedata_cfg.add.xml"/>
  </input>

  <time>
    <begin            value="0"/>
    <end              value="{sim_s}"/>
    <step-length      value="1"/>
  </time>

  <mesoscopic>
    <mesosim          value="true"/>
    <meso-edgelength  value="150"/>
  </mesoscopic>

  <output>
    <tripinfo-output  value="tripinfo.xml"/>
  </output>

  <report>
    <no-step-log      value="true"/>
    <verbose          value="true"/>
  </report>

</configuration>
"""
    # edgeData is defined in an additional file (correct SUMO pattern)
    add = """<?xml version="1.0" encoding="UTF-8"?>
<additional>
  <edgeData id="hourly" freq="3600" file="edgedata.xml" excludeEmpty="true"/>
</additional>
"""
    (out_dir / "sumo_city.sumocfg").write_text(cfg, encoding="utf-8")
    (out_dir / "edgedata_cfg.add.xml").write_text(add, encoding="utf-8")
    print(f"  sumo_city.sumocfg → {out_dir / 'sumo_city.sumocfg'}")
    print(f"  edgedata_cfg.add.xml → {out_dir / 'edgedata_cfg.add.xml'}")


def main():
    ap = argparse.ArgumentParser(
        description="Phase 1: generate preset demand for Newcastle city map",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--out",      default="./sumo_city",
                    help="Output directory")
    ap.add_argument("--net",      default="newcastle_stjames-full.net.xml",
                    help="Net file path (written into configs)")
    ap.add_argument("--vehicles", type=int, default=2000,
                    help="Number of vehicles")
    ap.add_argument("--duration", type=int, default=86400,
                    help="Simulation duration in seconds")
    ap.add_argument("--seed",     type=int, default=42)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("  Phase 1 -- Newcastle city preset demand")
    print(f"{'='*60}")
    print(f"  Core edges : {len(CORE_EDGES)} (BFS-validated)")
    print(f"  Vehicles   : {args.vehicles}")
    print(f"  Duration   : {args.duration}s ({args.duration//3600}h)")
    print(f"  Output     : {out.resolve()}")
    print()

    print("[1/5] Building OD pairs ...")
    # Overshoot slightly so duarouter failures still leave ~2000
    od = build_od_pairs(args.vehicles + 50, seed=args.seed)
    print(f"  {len(od)} pairs  ({len(set(od))} unique)")

    print("[2/5] Sampling depart times ...")
    times = sample_depart_times(len(od), args.duration, seed=args.seed)
    by_hour = [0] * 24
    for t in times:
        by_hour[min(t // 3600, 23)] += 1
    print("  Departures per hour (each # = 10 vehicles):")
    for h, n in enumerate(by_hour):
        print(f"    {h:02d}:00  {n:4d}  {'#'*(n//10)}")

    print("[3/5] Writing vtypes_city.xml ...")
    write_vtypes(out)

    print("[4/5] Writing trips_city.xml ...")
    write_trips(od, times, out, seed=args.seed)

    print("[5/5] Writing duarouter.cfg + sumo_city.sumocfg ...")
    write_duarouter_cfg(args.net, out)
    write_sumocfg(args.net, out, args.duration)

    print(f"\n{'='*60}")
    print("  NEXT STEPS:")
    print()
    print("  1. Copy your net.xml into the output folder:")
    print(f"     cp newcastle_stjames-full.net.xml {out}/")
    print()
    print("  2. Run duarouter to compute full route paths:")
    print(f"     cd {out}")
    print(f"     duarouter --configuration-file duarouter.cfg")
    print(f"     (takes ~1-2 min, writes routes_city.xml)")
    print()
    print("  3. Verify output:")
    print(f"     grep -c '<vehicle' routes_city.xml   # expect ~2000")
    print()
    print("  4. Run SUMO:")
    print(f"     sumo --configuration-file sumo_city.sumocfg")
    print(f"     (or sumo-gui for visual)")
    print(f"{'='*60}\n")

    print("Camera → core edge mapping (Phase 2):")
    for cam, core in CAMERA_TO_CORE.items():
        print(f"  {cam}  →  {core}")


if __name__ == "__main__":
    main()
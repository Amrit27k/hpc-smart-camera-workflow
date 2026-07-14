#!/usr/bin/env python3
"""
generate_synthetic_nodes.py

Generates synthetic per-node routes.xml + vtypes.xml files for the
remaining traffic-light junctions, based on real node_A data, to build
a 103-node dataset for the laptop-side network transfer benchmark.

This is NOT meant to produce valid SUMO routing (route edge sequences
are resampled from node A's real routes, not recomputed for each new
junction's actual position on the map) - it's meant to produce
realistic FILE SIZES and FILE COUNTS for network benchmarking, since
that's what's being tested here, not simulation correctness.

Vehicle counts per node are drawn from a lognormal distribution so the
103 nodes have a realistic busy/quiet spread rather than identical
copies of node A.

Usage:
    python3 generate_synthetic_nodes.py

Reads:
    routes_node_A.xml, vtypes_node_A.xml (real data)

Writes:
    output/node_<NNN>/routes_node_<NNN>.xml
    output/node_<NNN>/vtypes_node_<NNN>.xml
    (for 103 nodes total: node_A real data copied as-is + 102 synthetic)
"""

import re
import random
import os
import shutil

random.seed(42)  # reproducible runs

SRC_ROUTES = "D:/Projects/Isambard-HPC-Edge/hpc-smart-camera-workflow/edge_data_inbox/node_A/routes_node_A.xml"
SRC_VTYPES = "D:/Projects/Isambard-HPC-Edge/hpc-smart-camera-workflow/edge_data_inbox/node_A/vtypes_node_A.xml"
OUT_DIR = "D:/Projects/Isambard-HPC-Edge/hpc-smart-camera-workflow/edge_data_inbox"

NUM_SYNTHETIC_NODES = 102  # + node_A = 103 total (matches traffic-light junction count)

# Lognormal params tuned so median ~= a fraction of node A's count,
# with a long tail of a few busy nodes - mimics real urban traffic
# distribution (many quiet junctions, few arterial/busy ones).
NODE_A_VEHICLE_COUNT = 1895
LOGNORMAL_MEAN = 6.8   # ln-space mean -> median ~= exp(6.8) ~= 897
LOGNORMAL_SIGMA = 0.55  # spread


def parse_node_a():
    with open(SRC_ROUTES) as f:
        content = f.read()

    header_match = re.search(r'^(.*?<routes[^>]*>)', content, re.DOTALL)
    header = header_match.group(1) if header_match else '<routes>'

    vtype_defs = re.findall(r'<vType[^/]*/>', content)
    vehicles = re.findall(
        r'<vehicle id="v\d+" type="(\w+)" depart="[\d.]+">\s*<route edges="([^"]+)"/>\s*</vehicle>',
        content
    )
    return header, vtype_defs, vehicles


def generate_node(node_id, vehicle_count, header, vtype_defs, vehicles):
    """Generate one synthetic node's routes.xml by resampling real
    (type, route_edges) pairs from node A, with new depart times spread
    across a 24hr window, tagged with this node's id."""

    lines = [header.split('\n')[-1] if '\n' in header else header]
    # Simplify: just use a clean routes root + vtypes, avoid dragging
    # node A's duarouter config comment into every file (keeps files
    # representative of a "merged/clean" output rather than raw duarouter dump).
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<routes>']
    out.extend(vtype_defs)

    for i in range(vehicle_count):
        vtype, edges = random.choice(vehicles)
        depart = round(random.uniform(0, 86400), 2)
        out.append(f'    <vehicle id="{node_id}_v{i}" type="{vtype}" depart="{depart:.2f}">')
        out.append(f'        <route edges="{edges}"/>')
        out.append('    </vehicle>')

    out.append('</routes>')
    return '\n'.join(out)


def main():

    header, vtype_defs, vehicles = parse_node_a()
    print(f"Parsed node_A: {len(vehicles)} vehicles, {len(vtype_defs)} vtypes")

    # # --- node_A: copy real data as-is ---
    # node_a_dir = os.path.join(OUT_DIR, "node_A")
    # os.makedirs(node_a_dir)
    # shutil.copy(SRC_ROUTES, os.path.join(node_a_dir, "routes_node_A.xml"))
    # shutil.copy(SRC_VTYPES, os.path.join(node_a_dir, "vtypes_node_A.xml"))

    sizes = []
    real_size = os.path.getsize(SRC_ROUTES)
    sizes.append((("A"), len(vehicles), real_size))

    # --- synthetic nodes ---
    for n in range(2, NUM_SYNTHETIC_NODES + 1):
        node_id = f"{n:03d}"
        vehicle_count = max(20, int(random.lognormvariate(LOGNORMAL_MEAN, LOGNORMAL_SIGMA)))

        node_dir = os.path.join(OUT_DIR, f"node_{node_id}")
        os.makedirs(node_dir)

        routes_xml = generate_node(node_id, vehicle_count, header, vtype_defs, vehicles)
        routes_path = os.path.join(node_dir, f"routes_node_{node_id}.xml")
        with open(routes_path, 'w') as f:
            f.write(routes_xml)

        vtypes_path = os.path.join(node_dir, f"vtypes_node_{node_id}.xml")
        shutil.copy(SRC_VTYPES, vtypes_path)

        size = os.path.getsize(routes_path) + os.path.getsize(vtypes_path)
        sizes.append((node_id, vehicle_count, size))

    # --- summary ---
    total_size = sum(s[2] for s in sizes)
    total_vehicles = sum(s[1] for s in sizes)
    print(f"\nGenerated {len(sizes)} nodes total (1 real + {NUM_SYNTHETIC_NODES} synthetic)")
    print(f"Total vehicles across all nodes: {total_vehicles}")
    print(f"Total size: {total_size / 1024 / 1024:.2f} MB")
    print(f"Min node size: {min(s[2] for s in sizes)/1024:.1f} KB")
    print(f"Max node size: {max(s[2] for s in sizes)/1024:.1f} KB")
    print(f"Mean node size: {(total_size/len(sizes))/1024:.1f} KB")
    print(f"\nOutput: {OUT_DIR}/node_<ID>/")


if __name__ == "__main__":
    main()
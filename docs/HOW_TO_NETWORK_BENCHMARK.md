# Aggregator edge device → Dawn HPC transfer benchmark

## Purpose

The smart-camera SUMO workflow has 103 edge devices (one per traffic-light
junction on the Newcastle map), each expected to send hourly routes/vtypes
XML data toward a central point before it's merged and pushed to Dawn HPC
for simulation. Before building any real automation, this benchmark answers
a narrower question: **how does aggregator edge device → HPC transfer time
scale with the number of edge devices contributing data?**

This matters directly for architecture choice. Two designs are on the
table:

- **A — aggregate then push**: each edge device sends its files to the
  aggregator edge device, which merges everything and pushes one bundle
  to HPC.
- **B — direct push**: each edge device opens its own connection to HPC
  independently.

Architecture A is the current plan. This benchmark exists to check whether
that's a defensible choice, or whether B would work just as well.

## Test data

Real per-node data (`routes_node_A.xml` + `vtypes_node_A.xml`, ~840 KB,
1,895 vehicles across 24 hours) was used as the seed for 102 additional
synthetic node folders, built to match the real file structure and a
plausible busy/quiet traffic distribution (lognormal vehicle counts per
node, real edge-ID sequences resampled from the seed file, so file sizes
and byte counts are realistic even though the specific routes aren't
independently valid per junction). This gives 103 folders total
(`node_000` … `node_102`), matching the real count of traffic-light
junctions on the Newcastle map, sized 124 KB–949 KB each (~45 MB total).

This dataset is for network benchmarking only — it should not be reused
for anything that depends on SUMO simulation correctness.

## Method

Transfers were run aggregator edge device → Dawn HPC login node via
`rsync` over SSH (same key-based auth as normal interactive login), from
Git Bash.

A single benchmark script (`bench_transfer.sh`) supports two modes:

- **Positional-arg mode**: point it at any local folder and a remote
  destination, for one-off transfers (e.g. the original 4-file merged
  config bundle).
- **`NODE_COUNT` mode**: set an environment variable and it selects the
  first *N* sorted `node_*` folders from the edge data inbox and syncs
  only those — e.g. `NODE_COUNT=10 ./bench_transfer.sh` sends
  `node_000`–`node_009`. This is what produced the scaling data below.
  Selection uses `rsync --files-from` against a generated file list, so
  only the chosen subset is touched — no local staging copy is made
  first, keeping local disk I/O out of the timed section.

Each run reports:

- **Wall-clock elapsed time** — a plain `date`-before/`date`-after
  measurement around the whole `rsync` call.
- **rsync-reported throughput** — parsed directly from rsync's own
  `--stats` output (`sent X bytes ... Y bytes/sec`), which rsync times
  internally *after* the SSH session is already authenticated. This
  number structurally excludes SSH handshake and TOTP entry time,
  independent of how the wall-clock timer is set up.

Both numbers are printed so they can be cross-checked against each other,
rather than trusting either one blindly.

Runs were repeated at node counts of 1, 5, 10, 20, 50, and 103, syncing
into a fresh/emptied remote destination each time — rsync's default
change-detection (size + mtime) will otherwise skip re-sending files it
considers unchanged from a previous run, silently turning a "transfer
benchmark" into a "confirm nothing changed" benchmark. `--ignore-times`
is also set in the script as a safeguard, forcing every file to be sent
regardless of what the remote side already holds.

## Commands used

**Generating the 103 node folders** (1 real seed node + 102 synthetic,
matching the real file structure and a realistic busy/quiet size spread):

```bash
python3 generate_synthetic_nodes.py
```

Reads `routes_node_A.xml` + `vtypes_node_A.xml` as the seed, writes
`node_A` (copied as-is) plus `node_001`–`node_102` (synthetic) into an
output folder, each containing a `routes_node_<id>.xml` +
`vtypes_node_<id>.xml` pair. `node_A` was subsequently renamed to
`node_000` to sit alongside the synthetic set at
`edge_data_inbox/node_000` … `node_102`.

**Running the transfer benchmark**, from Git Bash, with the edge data
inbox path set inside `bench_transfer.sh`:

```bash
NODE_COUNT=1 ./bench_transfer.sh
NODE_COUNT=5 ./bench_transfer.sh
NODE_COUNT=10 ./bench_transfer.sh
NODE_COUNT=20 ./bench_transfer.sh
NODE_COUNT=50 ./bench_transfer.sh
NODE_COUNT=103 ./bench_transfer.sh
```

Each invocation selects the first `NODE_COUNT` sorted `node_*` folders
from the edge data inbox and `rsync`s only those to a dedicated remote
subfolder (`~/bench_scaling/n<N>`) over the existing key-based SSH setup,
prompting for TOTP once per run. Output includes both a wall-clock timing
and a throughput figure parsed from rsync's own `--stats` reporting.

**Plotting the results:**

```bash
python3 plot_benchmark.py iteration2_full.csv benchmark_results.png
```

Reads the collated CSV of per-run results (node count, file count, total
bytes, elapsed time, throughput) and produces a two-panel figure:
throughput vs node count, and average time per file vs node count on a
log scale.

## Results

| Node count | Total files | Total size | rsync-reported throughput | Avg time / file |
|---|---|---|---|---|
| 1 | 2 | 836 KB | ~0.11 MB/s | ~3.5 s |
| 5 | 10 | 2.5 MB | ~0.26 MB/s | ~0.97 s |
| 10 | 20 | 4.2 MB | ~0.65 MB/s | ~0.35 s |
| 20 | 40 | 8.8 MB | ~0.92 MB/s | ~0.24 s |
| 50 | 100 | 23 MB | ~1.78 MB/s | ~0.12 s |
| 103 | 206 | 45 MB | ~3.08 MB/s | ~0.07 s |

Wall-clock and rsync-reported throughput track each other closely at every
node count (within a few percent), which rules out TOTP entry time as a
meaningful source of noise in these numbers — both measurements are usable
interchangeably going forward.

**Throughput climbs with node count.** Small transfers (≤10 nodes) are
well under 1 MB/s; by 103 nodes, throughput reaches ~3 MB/s. This is not
primarily a bandwidth effect — Dawn's actual link capacity is far higher
than any of these figures — it's the fixed cost of SSH/rsync connection
setup being amortized over more data per connection.

**Average time per file drops sharply and non-linearly with node count**,
from ~3.5 s/file at N=1 to ~0.07 s/file at N=103 — roughly a 50x
improvement. This is the clearest signal in the data: per-connection
overhead dominates at small file counts, and batching many small files
into a single connection is dramatically more efficient than transferring
them individually.

## Implication for architecture choice

This result is a direct, quantified argument for Architecture A (aggregator
edge device, one connection to HPC) over Architecture B (each edge device
connects to HPC independently), at least for files of this size. If 103
edge devices each opened their own connection instead of being batched
through a single aggregator, the realistic expectation — based on the
N=1 cost observed here — is a transfer profile far closer to "103 ×
slow single-file cost" than to the amortized N=103 cost actually
measured. If Architecture B is revisited later, batching multiple
hourly files per device into fewer connections (rather than one
connection per file) would be worth testing before ruling it out.

## What this benchmark does not cover

- **Concurrency.** All runs here are sequential, single-connection
  transfers from one aggregator edge device. They do not measure what
  happens when multiple sources push to Dawn's login node at the same
  time — contention on the login node's bandwidth, CPU (SSH encryption
  overhead), or connection limits is a separate question, not answered
  here.
- **Realistic edge network conditions.** This was run over the
  aggregator's own network connection to Cambridge. Real edge devices
  may have different (likely worse) network characteristics, which
  would shift absolute numbers even if the overall shape of the curve
  (overhead dominates at low N) is expected to hold.
- **The merge step.** Time taken to merge multiple edge devices' files
  into the 4-file bundle on the aggregator was explicitly out of scope
  for this round and has not been measured.
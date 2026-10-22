# GreenRoute Offline Route Benchmarks

This package compares the production route helpers with a matrix nearest-neighbor
baseline and exact solutions. It runs entirely on deterministic synthetic inputs.
It does not call the route API, PostgreSQL, Redis, MQTT, OSM, or Overpass.

## Reproduce

From the repository root in PowerShell:

```powershell
$env:FLASK_ENV='testing'
$env:TESTING='true'
$env:PYTHONDONTWRITEBYTECODE='1'
.\venv\Scripts\python.exe -m unittest tests.test_route_benchmark -v
```

Print the complete machine-readable result to standard output:

```powershell
$env:FLASK_ENV='testing'
$env:TESTING='true'
$env:PYTHONDONTWRITEBYTECODE='1'
.\venv\Scripts\python.exe -m benchmarks.route_benchmarks.runner
```

Use `--track fixed` or `--track fleet` to run one track. The default runs both.
The default stochastic protocol uses seeds 0 through 29. Empty and one-stop cases
run once because their GA implementations take deterministic edge-case paths.

## Fixtures and recorded data

Fixture schema `synthetic-directed-v1` is implemented in `fixtures.py`. Coordinates,
fills, loads, capacities, and matrices are generated from fixed integer formulas
and explicit lists. Matrix preparation happens before timing. The JSON result records
the canonical fixture SHA-256, matrix provenance, Python/platform details, production
source hashes, parameters, each seed's route, independent score, reported score when
available, runtime, validity or failure, and summary statistics. Track and protocol
metadata set `distance_source` to `synthetic_directed_matrix`.

Per-run `runtime_seconds` measures the timed method operation. For adapter-backed methods
it is the adapter call, including setup and baseline work; for exact methods it is the
exact solver call. It excludes fixture matrix construction and independent scoring. The
legacy summary fields `mean_runtime_seconds` and `median_runtime_seconds` summarize this
per-run elapsed time. `mean_adapter_elapsed_seconds` names its mean explicitly.
For the directed-matrix sequencer, `sequencing_runtime_seconds` and
`mean_sequencing_runtime_seconds` measure only the `sequence_route` call. Matrix
construction, baseline preparation, and independent scoring are outside that timer.
Other Track A methods report `mean_sequencing_runtime_seconds` as null.

The matrices are complete, asymmetric, and synthetic. Their distances are test costs,
not road measurements. They do not come from an OSM graph or another external artifact.
Unreachable-arc behavior is tested separately and excluded from timed comparisons.
Invalid coordinates and duplicate IDs are also tested outside the performance runs.

## Track A: fixed-stop sequencing

The timed instances contain 5, 8, and 10 stops, plus empty and one-stop edge cases.
Every method receives the same depot and stops. The common score is the sum of directed
arcs for `depot -> each stop exactly once -> depot`. The benchmark validates that a
method returned every requested ID exactly once before calculating its cost. The
optimality gap is `(method cost / exact cost - 1) * 100`; it is zero for both zero-cost
empty tours and exact solutions.

Methods include production nearest, priority, cluster, GA, and multiobjective helpers;
the standalone `GeneticAlgorithm`; a matrix nearest-neighbor baseline; and exact
Held-Karp sequencing. The Held-Karp solver uses `O(n^2 * 2^n)` time. Production helpers
return routes but no route cost, so their `algorithm_reported_cost_km` is null. The
standalone GA is given the complete synthetic directed matrix and reports its cost
under that supplied matrix. Benchmark output labels this source
`synthetic_directed_matrix`; the production GA's internal `road_network` label is not
used as benchmark provenance.

The directed_matrix_nearest_neighbor_2opt method exercises the production fixed-stop
sequencer with the same synthetic directed matrix. It retains the input order and
deterministic depot-seeded nearest-neighbor order, then tests strict-improvement directed
2-opt reversals by recomputing each full closed-tour cost. The search is bounded to at
most 8 passes and 2,500 reversal evaluations per route. Its independent score must be no
greater than either valid starting candidate. Each run records the input-order cost,
nearest-neighbor cost, an independently scored no-worse-than-starting-candidates check,
and the sequencer's own elapsed time. The run-level `sequencing_runtime_seconds` and
summary `mean_sequencing_runtime_seconds` contain only the sequencer call. The legacy
`runtime_seconds` and `mean_runtime_seconds` fields include adapter setup and baseline
preparation, but exclude fixture matrix construction and independent scoring.

The production helpers choose routes with their existing coordinate/Haversine and fill
rules. Their routes are scored independently against the same directed matrix, so the
comparison measures route quality under the common synthetic directed-matrix objective
without pretending those helpers optimized that matrix. The standalone GA uses its existing
weighted normalized distance, estimated time, and emissions fitness. Its time and
emissions are derived from distance, so those terms are correlated.

## Track B: selection and fleet feasibility

The three instances have 8, 10, and 12 threshold-eligible bins, 2 or 3 vehicles, and
explicit driver counts. Bins at the threshold are eligible. Fleet08 and fleet10 each
exclude one below-threshold bin; fleet12 has no below-threshold bin. Each bin has a
fixture-only `load_units` value, and each vehicle has a fixture-only `capacity_units`
value. These values are benchmark
assumptions. The benchmark does not convert fill percentage to load and does not claim
that production enforces capacity.

Feasibility checks threshold eligibility, unique assignment, finite coordinates and
IDs, per-route stop limits, available vehicle/driver slots, vehicle capacity, and
finite directed route arcs. Empty routes do not consume driver slots. The exact solver
maximizes total served fill, then minimizes total closed-tour synthetic directed-matrix
cost, then breaks ties by sorted served bin IDs, vehicle IDs, and route IDs. It enumerates vehicle
subsets and uses Held-Karp tours for each feasible bin subset.

The synthetic-matrix greedy baseline processes eligible bins by descending fill and bin
ID, then inserts each bin at the position and vehicle with the smallest increase in
synthetic directed-matrix route cost, subject to stop and capacity limits. Production
routing helpers are assessed under the same feasibility checks. They do not enforce these
fixture capacities. Every run retains raw fill and distance diagnostics, but exact
fill comparisons and their means include feasible runs only. Distance means and regret
include only feasible runs serving the exact fill score, so distance is compared at an
equal first-level objective. Infeasible runs retain their reasons and have null exact-
comparison fields. Summaries report total runs, feasible runs, fill-comparison samples,
and equal-fill distance-comparison samples; empty comparison sets produce null means.
Adaptive routing is omitted because its forecast filter depends on runtime
prediction inputs outside this offline fixture contract.

## Results template

For a report based on a runner output, include:

- Benchmark and fixture schema versions, fixture IDs, and input hashes.
- Python/platform details and production source hashes.
- Per-method parameters, random seeds, valid/feasible counts, runtimes, and failures.
- Track A independent synthetic directed-matrix cost gaps and any reported cost
  discrepancies.
- Track B feasibility, raw served fill and distance, feasible-only fill shortfall,
  equal-fill distance means/regret, sample counts, and infeasibility reasons.
- A clear note that all matrices and capacity/load values are synthetic assumptions.

Do not use these synthetic results as evidence for field performance or production
vehicle-capacity enforcement.

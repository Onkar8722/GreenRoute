# Phase 3B offline graph fixture

`tests/fixtures/phase3b_synthetic_drive_graph.graphml` is a manually authored,
synthetic directed graph for offline integration tests. It contains four nodes
(the depot and three synthetic bins) and a one-way directed cycle with edge
lengths in meters. The coordinates are chosen to match GreenRoute's configured
depot and make the test points easy to snap; they do not represent a surveyed
or OSM road layout. The fixture contains no OpenStreetMap data.

The adjacent `phase3b_road_fixture_manifest.json` records the fixture SHA-256,
node order, graph type/CRS, OSMnx version used to validate GraphML loading, and
read-only fingerprints and metadata for the two pre-existing local Overpass
cache files. Both cache files remain external to the fixture and are not copied
or modified. Their response metadata attributes their data to OpenStreetMap and
states ODbL. This records the local notice only; it makes no conclusion about
redistribution rights.

## Offline checks

From the repository root in PowerShell, use the existing environment:

```powershell
$env:FLASK_ENV='testing'
$env:TESTING='true'
$env:PYTHONDONTWRITEBYTECODE='1'
.\venv\Scripts\python.exe -m unittest tests.test_offline_road_fixture -v
```

The tests inject the local GraphML graph into `src.osmnx_routing.G`. They block
`requests.Session.request`, `osmnx.graph_from_point`, and
`src.osmnx_routing.load_city_graph`; a test fails if any boundary is reached.
The Flask test-client check uses the production `/api/route` handler with local
bin data and fixed stop selection, so it does not use a database or contact a
service.

Run the fixture-only timing harness:

```powershell
$env:FLASK_ENV='testing'
$env:TESTING='true'
$env:PYTHONDONTWRITEBYTECODE='1'
.\venv\Scripts\python.exe -m benchmarks.route_benchmarks.offline_graph_timing --repetitions 100
```

The JSON output reports graph loading, coordinate mapping plus matrix building,
sequencing, path geometry, and total Flask test-client endpoint time separately.
Each stage reports a median and nearest-rank p95 after three warmups. The
endpoint measurement uses fixture-only graph state and patches data loading and
stop selection. These small synthetic-fixture timings do not establish
real-road accuracy, production latency, fuel savings, or emissions savings.
The Phase 3B run is recorded in
`benchmarks/route_benchmarks/phase3b_offline_fixture_timing.json` for review.

## Scope and known limits

The normal `load_city_graph` function uses `osmnx.graph_from_point` and can
contact Overpass; these tests do not call it. OSMnx 2.1.1's local GraphML reader
was used for the authored fixture. The raw JSON cache files were not converted:
they are Overpass responses, not GraphML, and no conversion pipeline has been
validated here.

Fixture coordinates are exactly on graph nodes, so tested snap distances are
zero. Production `build_distance_matrix` calls `ox.nearest_nodes` without a
maximum snap-distance threshold; the harness verifies node mapping and a
simulated mapping failure, but cannot establish safe geographic coverage for
arbitrary field coordinates. A missing mapping creates an incomplete matrix,
which the sequencer rejects. An unavailable directed return arc is represented
as infinity and the sequencer fails when it cannot find a feasible closed tour.

The route response's `distances_km` is scored from the supplied matrix in
opt-in sequencing mode. `path_geometries` are constructed separately by
`calculate_detailed_path`; a test verifies the reported matrix score is
independent of the geometry representation. Real OSM integration, snapping
coverage, cache/license review, and production performance remain Phase 3C
work.

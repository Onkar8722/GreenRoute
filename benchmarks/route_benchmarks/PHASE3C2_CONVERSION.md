# Phase 3C.2: offline Overpass JSON conversion

This experiment converts only the local October Overpass response into a
bounded directed graph. The output is a **conversion-mechanics demonstration**,
not a verified or complete driving graph. The original query and requested
network filter are absent from the cache metadata, so its geographic and road
coverage cannot be established. The April response is fingerprinted but is
not combined with or used by this conversion.

Phase 3C.2.2 strengthens validation of the existing GraphML and updates its
manifest terminology. It does not regenerate or rewrite either source cache or
the GraphML artifact. The manifest preserves generation-time code fingerprints
and records the audit implementation fingerprints separately.

## Reproduction

Use the already-installed project environment; no packages or network access
are needed:

```powershell
$env:FLASK_ENV='testing'
$env:TESTING='true'
$env:PYTHONDONTWRITEBYTECODE='1'
.\venv\Scripts\python.exe -m unittest tests.test_offline_osm_conversion -v
.\venv\Scripts\python.exe -m benchmarks.route_benchmarks.run_offline_osm_conversion
```

The runner checks both recorded source hashes and the October timestamp, element
counts, and bounds before parsing. It blocks HTTP requests, socket connections,
and DNS lookup in-process. It refuses to overwrite output by default. To
revalidate an existing artifact, use `--reverify-existing`; the runner rebuilds
the graph and requires the serialized bytes to match before refreshing only
the manifest. To produce a separate review run, choose a new basename, for
example:

```powershell
.\venv\Scripts\python.exe -m benchmarks.route_benchmarks.run_offline_osm_conversion --output-stem phase3c2_review_run
```

The GraphML and JSON manifest are written under this directory, outside both
source-cache directories. Scratch GraphML is created in the system temporary
directory and removed by the temporary-directory context after round-trip
validation. The raw April and October files are read only.

## Source identity and limits

The October input is
`src/cache/943a2cc7ad43949e49e19c3e9cfcc2576776572e.json`, SHA-256
`23199899b4952ffba3472c602CAFD65CF2E8F8ED1BE3D3FD7CE6FF45D29322DF`, dated
`2026-10-20T09:10:21Z`, with 217,484 nodes, 44,008 ways, no relations, and
bounds `[18.3466226, 73.662884, 18.68269, 74.0418075]` (min latitude,
min longitude, max latitude, max longitude). Its response credits OpenStreetMap
and states ODbL. Its original query/filter and per-element edit provenance are
unknown. The runner refuses to proceed if the identity or recorded metadata
differs.

The converter chooses the largest square, centered on the source-bounds center,
whose selected ways fit the approved limits of 10,000 graph nodes and 25,000
directed edges. A binary search selects the square; the hard caps are checked
again after filtering. The output retains all eligible directed components and
does not add links between them. The square is an experiment boundary, not a
claim that the cache covers that area completely.

## Conservative candidate-access policy

Policy: `greenroute_conservative_candidate_access@1.0.0`. This is a reproducible
candidate filter. It does not establish legal access or suitability for any
particular collection vehicle.

| Rule | Result |
|---|---|
| `highway` is `primary`, `primary_link`, `secondary`, `secondary_link`, `tertiary`, `tertiary_link`, `residential`, `unclassified`, `living_street`, `trunk`, or `trunk_link` | Continue to access checks |
| Other, missing, malformed, semicolon-separated, or unsupported `highway` | Exclude, or flag malformed/ambiguous values |
| A `service` tag is present | Exclude for review; `highway=service` is outside the allowlist |
| `busway` or `bus_stop` facility tag is present | Flag and exclude |
| `access`, `vehicle`, `motor_vehicle`, `hgv`, or `goods` is `yes` or `permissive` | Candidate permission for that tag |
| Any of those tags is `no`, `private`, `military`, or `emergency` | Exclude; if another access tag says `yes`/`permissive`, flag the conflict and exclude |
| A tag is `destination`, `customers`, `delivery`, `agricultural`, `forestry`, `permit`, or `discouraged` | Flag and exclude as context restricted |
| Access tag has another or malformed value, including compound values | Flag and exclude; values are not normalized into permission |
| Access is absent | Continue as a candidate; absence is not proof of vehicle authorization |
| `motorcar` tag | Preserve in source tags, but do not treat it as proof of heavy-vehicle access |
| `maxweight`, `maxheight`, `maxwidth`, `maxlength`, or `maxaxleload` | Flag and exclude because no vehicle profile is specified |
| Access-tag or vehicle-limit `:conditional` tags, or any `oneway:*` tag | Flag and exclude as unsupported conditional/mode-specific rules |
| `oneway=yes`, `true`, or `1` | Forward in source-way order |
| `oneway=-1` | Reverse source-way order |
| `oneway=no`, `false`, or `0` | Both directions, except it conflicts with a roundabout and is flagged/excluded |
| Missing `oneway` on a roundabout | Forward in source-way order, recorded as inferred |
| Missing `oneway` elsewhere | Both directions |
| Any other direction value, including `oneway="two way"` | Flag and exclude; do not reinterpret |

This exclusion-first policy favors unambiguous mechanics over road coverage. It
does not model conditional access, vehicle dimensions, weight, emergency
exceptions, access hierarchies, or jurisdiction-specific rules.

## Conversion and validation

The converter reads only the explicitly supplied local JSON response. It builds
node and edge GeoDataFrames and calls the public `osmnx.graph_from_gdfs` API;
it does not use OSMnx's private JSON parser or graph-acquisition APIs. Node `x`
is longitude, `y` is latitude, and graph CRS is EPSG:4326. Each source way
segment retains its way ID, original segment index, direction, policy reason,
and canonical JSON of the original tags. Parallel arcs receive deterministic
keys. Edge `length` is WGS84 ellipsoidal distance in meters from `pyproj.Geod`,
rounded to one micrometer. Disconnected components remain disconnected.

The source-selection reference is built from the original decoded JSON node
and way records plus the declared selection-area bounds. It has its own policy
oracle and way-sequence validation; it does not call the converter's
`classify_way`, use the converter's selected-node set, or derive expected ways
from the GraphML. Validation compares the complete selected node IDs and
directed edge records `(from node, to node, source way ID, segment index,
direction)` with the artifact. A mismatch reports missing and unexpected
records, including source way and segment identifiers. Policy regression
fixtures assert explicit outcomes against both the converter classifier and
the separate reference oracle.

Converted edge lengths are WGS84 ellipsoidal distances in meters. Reference
edges use spherical Haversine distances in meters between the same source graph
node coordinates. Segment lengths must agree within
`max(0.05 m, 0.6% of the reference segment length)`. The reference route metric
is shortest-path cost on that independently selected source-topology graph; it
is **not** direct coordinate-to-coordinate Haversine distance. Path comparisons
use identical graph-node endpoints and must agree within
`max(1 m, 0.6% of the reference path distance)`. Unreachable sampled pairs
must remain unreachable. Synthetic coordinate-to-node snap offsets are
reported separately and are excluded from path and tour costs.

For the 9,999-node artifact, path validation uses a deterministic smoke sample:
sort node IDs, select 32 evenly spaced rounded index positions, then evaluate
all 1,024 ordered pairs in their Cartesian product, including self-pairs. This
is not a random or spatially stratified sample and does not establish graph-wide
path accuracy or coverage. The manifest records the selected IDs, pair count,
unreachable count, and largest observed errors. Edge/node inventory checks are
complete for the selected graph; the six-tour check is exhaustive only for its
one depot and three synthetic stops.

Four deterministic synthetic coordinates are generated on a grid strictly
inside the selected square, at least 30 m from its boundary. They must snap to
four distinct nodes of the largest strongly connected component, each within
30 m by an independent WGS84 geodesic calculation. The tour validator evaluates
all six permutations for one synthetic depot and three stops, including the
depot departure and return legs, and compares each directed closed-tour cost
against the independent source-derived graph. For compatibility, the manifest
field `independent_haversine_cost_m` is retained; it means shortest-path cost
on the source-topology graph with spherical Haversine segment weights, using
snapped graph-node endpoints and excluding snap offsets. It does not mean a
straight-line Haversine tour between the four synthetic coordinates.

The canonical checksum sorts node IDs and directed `(u, v, key)` edges and
normalizes GraphML type conversions. A second conversion must reproduce it;
the scratch GraphML must also reload to the same checksum and graph size before
the final output is published.

## Measurements and licensing

The manifest reports hashes for the converter and runner code, source reading,
JSON parsing, filtering, length calculation, GeoDataFrame/graph construction,
spatial-index preparation, GraphML serialization, artifact publication, total
elapsed time, and isolated process peak working set. These are measurements
for this environment and bounded sample, not production latency estimates. The
prior 1–2 GiB planning estimate is not a measured requirement; the measured
process peak was 599,461,888 bytes (about 572 MiB), with 84.51 seconds total
elapsed time in the isolated conversion process. The graph contains 9,999 nodes
and 18,105 directed edges. Exact stage timings and other validation results
are in `phase3c2_conversion_manifest.json`.

The manifest records the local OpenStreetMap/ODbL notice. Attribution and
distribution/share-alike review remain pending; this document makes no legal
conclusion. Unknown query provenance, candidate-only access rules, limited
coverage, and pending license review prohibit describing the artifact as a
verified `drive` network or using it as evidence of real-road, fuel, or
emissions performance.

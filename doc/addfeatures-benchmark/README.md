# addFeatures benchmark

Measures `QgsPostgresProvider::addFeatures` before and after the change in
[`../pipeline-mode-plan.md`](../pipeline-mode-plan.md) (libpq pipeline mode for
the per-feature INSERTs). The scripts are independent of that change. Run the
same command before and after it, and compare the two result files.

| File | Purpose |
|---|---|
| `run.sh` | Sets up the environment for a build tree (same as ctest) and starts the benchmark |
| `bench_addfeatures.py` | The benchmark itself. Writes `results/<label>.json` and `results/<label>.md` |
| `latency_proxy.py` | TCP proxy that adds a fixed round trip time and counts round trips |
| `compare.py` | Compares two result files and prints a Markdown table |
| `results/` | Saved runs. `baseline.*` is the code before the change, `pipeline.*` and `pipeline-nopipe.*` the code after it, `compare-*.md` the comparisons |

## Method

**Network latency.** `latency_proxy.py` runs as a child process between QGIS
and the database. It forwards every chunk of data half the configured round
trip time (RTT) after it arrives, in both directions. It never waits for the
other side, so pipelined traffic gets through as fast as it would on a real
long link with unlimited bandwidth. `tc netem` isn't used: it needs root, and
it doesn't delay the loopback traffic to the Docker port.

Each scenario runs once per latency: `direct` (no proxy, the plain local
connection) and +10 ms and +50 ms RTT through the proxy. A local database
shows CPU cost and regressions. 10–50 ms is typical for a cloud database.

**Client turns.** The proxy counts how often the client sends data after it
has received data from the server. A client that waits for every reply has
one turn per statement, so this is the number of round trips QGIS waited for.
The count doesn't depend on timing noise, so it shows the effect of the change
directly. Today `addFeatures` of N features costs N + 4 turns: BEGIN,
PREPARE, one EXECUTE per feature, DEALLOCATE and COMMIT. In pipeline mode,
results can arrive while QGIS is still sending, so the count is approximate
there. It should still be close to the number of chunks plus the fixed
statements.

**What is timed.** Only the insert operation, measured with
`time.perf_counter()`. Untimed: table setup and checking (done with psycopg2
on a direct connection), opening the layer, and building the features.
QGIS shares one RO and one RW connection per conninfo. A "keeper" layer that
stays open for the whole run holds both open, and an untimed one-feature
insert opens the RW one first. So no timed run includes connection setup.

**Scenarios** (1000 features by default):

| Scenario | What it does | Plan section |
|---|---|---|
| `provider_points` | `dataProvider().addFeatures()`: points with 3 attributes and a serial PK. The new ids come back through `RETURNING` | main path |
| `provider_points_fastinsert` | Same with `QgsFeatureSink.FastInsert` (no `RETURNING`) | export / Processing |
| `layer_commit_points` | Layer edit session: `addFeatures()`, then the timed `commitChanges()` | saving edits |
| `exporter_points` | `QgsVectorLayerExporter.exportLayer()` from a memory layer to a new table. Uses FastInsert, and also times about 40 round trips for CREATE TABLE | Export to PostgreSQL |
| `provider_points_defaults` | Extra `text DEFAULT 'new'` column, NULL in every other feature. Each NULL costs a `paramValue()` `SELECT <default>` round trip, which the plan leaves in place | out of scope, §3 |
| `provider_large_polygons` | 64 polygons of 10,000 vertices (about 20 MB of hex WKB), more than the plan's 16M-character chunk cap | size cap, bandwidth |

Attribute values differ between features. Otherwise `addFeatures` writes a
constant value into the INSERT text instead of sending it as a parameter.

**Correctness checks.** After every run, the script checks:

- the row count in the database
- that the returned feature ids are 1..N and equal the returned PK attributes
  (scenarios with `RETURNING`)
- the default/explicit split of `status` in the database and in the returned
  features (`provider_points_defaults`)
- the total vertex count (`provider_large_polygons`)

A run that fails a check is reported as an error, left out of the statistics,
and makes the script exit with status 1.

**Statistics.** 3 runs per scenario and latency. The tables show the median,
with min and max in the JSON and MD files.

**Isolation.** Tables live in the schema `qgis_bench_addfeatures` of the test
database. The script recreates the schema at the start and drops it at the
end. QGIS settings go to a temporary profile (`QGIS_CUSTOM_CONFIG_PATH`), so
the user profile is untouched. `QGIS_DEBUG=0` keeps Debug-build log output
out of the timings.

## Running it

Prerequisites:

1. The PostGIS test database is running (see "PostGIS test database" in
   `CLAUDE.md`). The default connection is `service=qgis_test`. Override it
   with `QGIS_PGTEST_DB` or `--db`.
2. The build is up to date: `cmake --build --preset dev` (8 jobs; don't build
   with more in this WSL VM).
3. The machine is otherwise idle (no build running). Round-trip time
   dominates the proxied rows, but the `direct` rows are sensitive to load.

```bash
# before the change (already done, see results/baseline.*)
doc/addfeatures-benchmark/run.sh --label baseline

# after the change: pipelining on, and the sequential fallback through the setting
doc/addfeatures-benchmark/run.sh --label pipeline
doc/addfeatures-benchmark/run.sh --label pipeline-nopipe --disable-pipelining

python3 doc/addfeatures-benchmark/compare.py doc/addfeatures-benchmark/results/baseline.json \
    doc/addfeatures-benchmark/results/pipeline.json --output doc/addfeatures-benchmark/results/compare-pipeline.md
python3 doc/addfeatures-benchmark/compare.py doc/addfeatures-benchmark/results/baseline.json \
    doc/addfeatures-benchmark/results/pipeline-nopipe.json --output doc/addfeatures-benchmark/results/compare-nopipe.md
```

A full run takes about 20 minutes before the change, most of it in the +50 ms
rows. `--quick` is a one-minute smoke test (100 features, 1 run, direct and
+10 ms). See `--help` for the other options (`--latencies`, `--repeats`,
`--points`, `--scenarios`, ...).

`--disable-pipelining` sets the hidden `PostgreSQL/disablePipelining` setting.
It has no effect before the change, but it is recorded in the results.

`compare.py` marks rows where the second run is more than 10 % slower
(`--tolerance`), and then exits with status 1. It also warns when the runs
aren't comparable: a different method (hash of the two benchmark scripts),
machine, build type, server or feature counts.

### Keeping before and after comparable

- Use the same build directory and build type. After the change, rebuild with
  `cmake --build --preset dev`. Only the provider changes, so the rest of the
  build stays the same.
- Use the same machine and the same options.
- Don't change the benchmark scripts between runs. If they have to change,
  rerun the baseline from the parent commit.
- Each result records the commit, whether `src/providers/postgres` differs
  from HEAD, the provider library's build time, the libpq and server
  versions, and the load at start.

### Expected effect

With 500-feature chunks, 1000 features become 2 batches. `provider_points`,
`provider_points_fastinsert` and `layer_commit_points` should drop from 1004
client turns to about 6–10. At +50 ms that is about 50 s → under 1 s.
`provider_points_defaults` keeps its 500 `paramValue()` round trips (out of
scope), so it should only drop by about two thirds. `exporter_points` keeps
its table creation round trips. `provider_large_polygons` and the `direct`
rows shouldn't get slower.

## Limitations

- The proxy models latency only. It doesn't model bandwidth limits, packet
  loss or TCP slow start on a real WAN.
- The proxy itself adds a little latency and CPU load. That is the same
  before and after, but proxied rows at "+0 ms" wouldn't equal `direct`.
- Debug build. Real-world CPU cost is lower in a release build, but the
  network round trips this change targets are the same.
- WSL2 with Docker Desktop: `direct` goes through Docker's port forwarding,
  and timings vary a few percent between runs.

## Baseline results

`results/baseline.md` (full table) and `results/baseline.json`, run on
2026-10-08:

- master `204b5e4043`, provider unchanged, Debug build
- libpq 18.6, PostgreSQL 11.8 / PostGIS 2.5.4 in Docker Desktop on WSL2
- 3 runs each, all checks passed, min–max within 2 % of the median in the
  proxied rows

Median seconds, client turns in parentheses:

| Scenario | Features | direct | +10 ms | +50 ms |
|---|---:|---:|---:|---:|
| `provider_points` | 1000 | 0.21 | 11.26 (1004) | 52.40 (1004) |
| `provider_points_fastinsert` | 1000 | 0.18 | 11.30 (1004) | 52.38 (1004) |
| `layer_commit_points` | 1000 | 0.21 | 11.23 (1004) | 52.40 (1004) |
| `exporter_points` | 1000 | 0.28 | 11.71 (1044) | 54.47 (1044) |
| `provider_points_defaults` | 1000 | 0.32 | 16.96 (1504) | 78.52 (1504) |
| `provider_large_polygons` | 64 | 1.76 | 2.52 (68) | 5.18 (68) |

What the baseline shows:

- **Insert time is round trips × RTT.** Every point scenario has N + 4
  turns, as the plan says. At +50 ms that comes to 1004 × 50 ms = 50.2 s of
  the 52.4 s measured. The rest is about 0.2 s of local work and 1–2 ms of
  proxy and timer overhead per round trip. So at +50 ms, QGIS spends 96 %
  of the time waiting for the network.
- **FastInsert, `RETURNING` and the edit buffer don't matter.** The provider
  call with and without FastInsert and the layer commit all cost the same.
  They all go through the same per-feature loop.
- **The exporter** adds only its 40 table-creation round trips on top.
- **Defaults cost 50 % more.** The 500 NULLs each do a `paramValue()`
  `SELECT <default>` round trip, which the plan leaves in place. Expect about
  25 s at +50 ms after the change, and that becomes the next bottleneck.
- **Large polygons** are already dominated by data volume (1.76 s local).
  They are the check for the chunk size cap, and they should not get slower.

## Results after the change

`results/pipeline.*` (pipelining on) and `results/pipeline-nopipe.*`
(`--disable-pipelining`), compared with the baseline in
`results/compare-pipeline.md` and `results/compare-nopipe.md`. Run on
2026-10-08:

- same commit `204b5e4043` with the provider change applied, same build,
  libpq and server as the baseline. `compare.py` reports no comparability
  warnings
- 3 runs each, all checks passed, no row more than 10 % slower than the
  baseline
- the pipeline run started right after a rebuild (load average 4.1 over
  5 minutes). That only affects the `direct` rows

Pipelining on, median seconds, client turns in parentheses, speedup against
the baseline:

| Scenario | Features | direct | +10 ms | +50 ms |
|---|---:|---:|---:|---:|
| `provider_points` | 1000 | 0.021 (10×) | 0.088 (6) 128× | 0.334 (6) 157× |
| `provider_points_fastinsert` | 1000 | 0.019 (9×) | 0.086 (6) 132× | 0.335 (6) 157× |
| `layer_commit_points` | 1000 | 0.023 (9×) | 0.091 (6) 123× | 0.338 (6) 155× |
| `exporter_points` | 1000 | 0.046 (6×) | 0.560 (46) 21× | 2.458 (46) 22× |
| `provider_points_defaults` | 1000 | 0.105 (3×) | 5.709 (506) 3.0× | 26.49 (506) 3.0× |
| `provider_large_polygons` | 64 | 1.863 (0.94×) | 1.843 (7) 1.4× | 2.054 (7) 2.5× |

What the results show:

- **The round trips are gone.** The point scenarios drop from 1004 client
  turns to 6, the low end of the expected 6–10. At +50 ms, 6 × 50 ms =
  0.30 s of the 0.334 s measured, so the insert is now close to the network
  floor: about 52 s → a third of a second.
- **The remaining round trips are the ones the plan leaves in place.**
  `provider_points_defaults` keeps its 500 `paramValue()` round trips
  (506 turns, 26.5 s at +50 ms, as expected). It is now by far the slowest
  path and the next candidate. In `exporter_points`, about 40 of the 46
  turns are table creation.
- **Large polygons get faster too.** 68 turns → 7, and 5.18 s → 2.05 s at
  +50 ms. About 20 MB of hex WKB crosses the 16M-character cap, so
  the 64 polygons go in at least two batches. The vertex count check passed.
- **Without the proxy, point inserts are 9–10× faster** (0.21 s → 0.02 s).
  QGIS no longer waits for each local round trip through Docker's port
  forwarding.
- **One small cost to watch:** `provider_large_polygons` direct is about
  6 % slower (1.755 s → 1.863 s). That is within the tolerance, and the
  pipelined minimum (1.696 s) is below the baseline minimum. An earlier
  pipeline run showed the same 0.9×, though, so it is probably a real small
  cost of holding a whole batch of large parameters in memory before sending.

The sequential fallback (`--disable-pipelining`) matches the baseline: the
same turn counts in every proxied row, and times within 0.5 %. In the
`direct` rows it is 1.2–1.6× faster than the baseline (for example 0.211 s →
0.152 s for `provider_points`), and the min–max ranges don't overlap. A likely
reason is that `PQexecPreparedBatch` creates one query log entry per batch
instead of one per feature. This hasn't been verified.

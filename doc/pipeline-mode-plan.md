# Pipelined INSERTs in QgsPostgresProvider::addFeatures

## Context

`QgsPostgresProvider::addFeatures` (`src/providers/postgres/qgspostgresprovider.cpp:2319`) prepares one single-row
INSERT and then calls the blocking `conn->PQexecPrepared()` once per feature (`:2599`). Inserting N features costs
about N + 4 network round trips, which is slow against a cloud database. This applies to saving edits and to
export/Processing (with or without `FastInsert`).

The benchmark in [`addfeatures-benchmark/`](addfeatures-benchmark/README.md) measured this before the change. 1000
features cost 1004 round trips and 52.4 s at +50 ms round trip time, and about 96 % of that is spent waiting for the
network. Saving edits and the provider call with and without `FastInsert` all cost the same.

libpq pipeline mode (libpq >= 14, `LIBPQ_HAS_PIPELINING`) lets the client queue many `PQsendQueryPrepared` calls and
read the results afterwards in the same order. It only changes how the client uses the extended query protocol, so
it works with any PostgreSQL server version. Poolers, proxies and PostgreSQL-compatible databases in between may
not handle it well, which is why there is a setting to turn it off (section 1). The statement, the `RETURNING`
handling and the feature ID mapping stay exactly as they are, because each feature still has its own statement and
its own result. Only the waiting goes away: about one round trip per chunk instead of one per feature.

The local build has libpq with `LIBPQ_HAS_PIPELINING` (`/usr/include/postgresql/libpq-fe.h:38`). Builds against
older libpq keep the current behaviour through `#ifdef`.

## Design

### 1. New connection method: `QgsPostgresConn::PQexecPreparedBatch`

Files: `src/providers/postgres/qgspostgresconn.h` (next to `PQexecPrepared`, line 335) and `qgspostgresconn.cpp`
(after `PQexecPrepared`, ~line 1715).

```cpp
/**
 * Executes the prepared statement \a stmtName once for each entry of \a paramsList and returns
 * the results in the same order.
 * Uses libpq pipeline mode when available, so the whole batch costs about one round trip.
 * Otherwise, or when pipelining is disabled in the settings, statements run one at a time.
 * Execution stops at the first statement that fails. The returned list then ends with an error
 * result for it and can be shorter than \a paramsList. Otherwise it has one result per entry.
 * The connection is always out of pipeline mode when this returns.
 */
std::vector<std::unique_ptr<QgsPostgresResult>> PQexecPreparedBatch( const QString &stmtName, const QList<QStringList> &paramsList,
                                                                     const QString &originatorClass = QString(), const QString &queryOrigin = QString() );
```

The results are returned instead of being passed to a callback while the batch runs. Code that runs while the
connection is in pipeline mode must not run any synchronous query on it, and the provider's result handling calls
`convertValue()`, which can do that (`connectionRO()` → `fromEwkt` → `sridToCrs`). If anything throws while the
connection is still in pipeline mode, the next `LoggedPQexec` gets a NULL result with `CONNECTION_OK`. The wrapper
then calls `::PQreset` (`qgspostgresconn.cpp:1539`), which in transaction mode silently drops the user's open
transaction. The results are small (only the `RETURNING` PK row, or nothing), so holding a chunk of them is cheap.

Implementation:
- `QMutexLocker locker( &mLock );` (recursive mutex, same as the other wrappers).
- Move the param conversion from `PQexecPrepared` (QStringList → `const char **`, null strings → `nullptr`) into a
  small static helper so both methods use it. Convert one entry at a time, right before it is sent. libpq copies
  the values into its output buffer, so only one entry's UTF-8 copy exists at a time.
- Hidden setting `PostgreSQL/disablePipelining` (`QgsSettings::Providers`, like `PostgreSQL/default_timeout` at
  `qgspostgresconn.cpp:259`), read on each call. When it is true, use the sequential path. Tests use it to cover
  that path in CI, and users can use it as a workaround for middleware that has problems with pipelining.
- Errors that don't come with a result: when `PQsendQueryPrepared`, `PQpipelineSync` or `PQexitPipelineMode` fails,
  or a result is missing, create `::PQmakeEmptyPGresult( mConn, PGRES_FATAL_ERROR )` at that point. It copies the
  connection's current error message. Append it after the results that were already received, so the caller
  always gets an error result to report.
- `#ifdef LIBPQ_HAS_PIPELINING`:
  1. If pipelining is disabled, or `::PQpipelineStatus( mConn ) != PQ_PIPELINE_OFF`, or
     `::PQenterPipelineMode( mConn ) == 0`, use the sequential path.
  2. Install a scope guard (e.g. `qScopeGuard`) that drains any remaining results and calls
     `::PQexitPipelineMode( mConn )`. This way every exit path leaves pipeline mode, including exceptions.
  3. For each params entry: `::PQsendQueryPrepared( mConn, name, n, values, nullptr, nullptr, 0 )`. If a send
     fails, create the error result (see above) and stop queuing.
  4. `::PQpipelineSync( mConn )`. It only fails on a write error or out of memory, and then the connection can't be
     used. In that case, create the error result and skip reading (reading could block). Exit is left to the reset
     path.
  5. Read results in order. For query *i*: `res = ::PQgetResult()`. Append OK results. Append the first error
     result. Clear `PGRES_PIPELINE_ABORTED` results, which come for every query after the first error. Each
     result is followed by a `nullptr`, which must be read. A `nullptr` where a result is expected, two
     `nullptr`s in a row, or `PQstatus() == CONNECTION_BAD` means the connection is lost. Stop reading and create
     the error result. Without this check the loop never ends, because `PGRES_PIPELINE_SYNC` never arrives.
  6. Read until `PGRES_PIPELINE_SYNC` (no `nullptr` follows it), then call `::PQexitPipelineMode( mConn )`. All
     results must be read before this, otherwise exiting fails. If exiting fails anyway, log it. The connection is
     broken then, and the existing reset path handles it.
- `#else` / sequential path: loop over `::PQexecPrepared()` (the libpq function directly, not the wrapper, so the
  batch is logged only once). Append each result. Stop at the first error, or at a `nullptr` result, which is
  turned into an error result.
- Logging: one `QgsDatabaseQueryLogWrapper` for the batch, `u"PQexecPreparedBatch(%1) x %2 [pipeline]"_s` or
  `[sequential]`, so the log shows which path ran. On failure call `setError()` with the index of the failing
  statement and its message, matching `PQexecPrepared`.
- Deadlock: QGIS connections are blocking (no `PQsetnonblocking`). In blocking mode, libpq's internal send loop
  (`pqSendSome`) reads incoming data into its input buffer when the socket can't be written. Results here are
  small, and the caller limits batch size (below).

### 2. Restructure the loop in `QgsPostgresProvider::addFeatures`

File: `src/providers/postgres/qgspostgresprovider.cpp:2535-2618`.

Split the per-feature loop into two phases, run per chunk of features. A chunk ends at
`ADD_FEATURES_BATCH_MAX_FEATURES = 500` features or `ADD_FEATURES_BATCH_MAX_CHARS = 16 * 1024 * 1024` total param
string length, whichever comes first (`constexpr` next to the function). Today only one feature's params exist at
a time. Hex WKB held as a `QString` is 4× the raw WKB size, and `QgsVectorLayerExporter` buffers up to 100 MB of
features per `addFeatures` call (`qgsvectorlayerexporter.cpp:298`). Without the size cap, a chunk of large polygons
could take hundreds of MB. Inserts of large geometries are limited by bandwidth anyway, so the cap doesn't cost
speed.

- **Phase A, build params**: the current body of `:2537-2597`, unchanged and moved into a lambda or private
  helper `QStringList addFeatureParams( QgsFeature &feature, const QList<int> &fieldId,
  const QStringList &defaultValues )`. It still rewrites feature attributes as today. **This has to run before
  entering pipeline mode**, because `paramValue()` (`:2144`) runs a synchronous `SELECT <default>` on
  `connectionRO()`. In transaction mode, that is the same connection as `conn` (`connectionRO()`/`connectionRW()`
  both return `mTransaction->connection()`, `:321`/`:370`), and synchronous calls are not allowed during a pipeline.
  Exceptions thrown here (`paramValue` throws `PGException`) happen outside pipeline mode, so the existing catch
  block handles them as today.
- **Phase B, execute**: `const auto results = conn->PQexecPreparedBatch( u"addfeatures"_s, chunkParams, ... );`.
  Pipeline mode has already ended when it returns. Then run the existing `:2601-2617` logic unchanged for each
  `results[i]` on `flist[chunkStart + i]`:
  - set PK attributes from `RETURNING`,
  - `setId( PQoidValue() )` for `PktOid`,
  - `throw PGException( result )` on a bad status. If a statement failed, the last result is its error result, so
    this throws as it does today. No new `PGException` constructor is needed.

The existing catch block (rollback + DEALLOCATE) and the ID update loop after it (`:2620-2647`) stay as they are.

### 3. Out of scope (possible follow-ups)

- The `paramValue()` round trip per NULL/default value per feature stays. It is the next biggest cost when
  columns have defaults. In the benchmark baseline, a column with a default that is NULL in every other feature
  raises 1000 features at +50 ms from 52.4 s to 78.5 s. About 25 s of that remains after the change.
- BEGIN / PREPARE / DEALLOCATE / COMMIT stay as separate round trips (4 total per call).
- Other per-feature loops (`changeAttributeValues`, `changeGeometryValues`, `deleteFeatures`) could use the same
  batch method later.

## Critical files

- `src/providers/postgres/qgspostgresconn.h` / `.cpp`: new `PQexecPreparedBatch`, shared param helper,
  `PostgreSQL/disablePipelining` setting.
- `src/providers/postgres/qgspostgresprovider.cpp`: `addFeatures` loop split into size-capped chunks, phase A and
  phase B.
- `tests/src/providers/testqgspostgresconn.cpp`: new C++ tests for `PQexecPreparedBatch`.
- `tests/src/python/test_provider_postgres.py`: new tests.
- `tests/src/python/test_provider_postgres_latency.py` (exists, uses `tc netem` through `setDelay()`): new timing
  test.

## Tests

### C++ (`tests/src/providers/testqgspostgresconn.cpp`, test `test_provider_postgresconn`)

Call `PQexecPreparedBatch` directly on a read-write connection (`QgsPostgresConn::connectDb( uri, false, false )`)
with a `TEMP` table and an `INSERT ... RETURNING` statement. Run each case with `PostgreSQL/disablePipelining` set
to false and to true:

1. **Success**: 1000 entries give 1000 `PGRES_TUPLES_OK` results in input order (the returned values match the
   inputs).
2. **Server error in the middle**: unique violation at index 700 gives 701 results. The last one is
   `PGRES_FATAL_ERROR` with the violation message.
3. **Client-side send error**: one entry with 65536 params. `PQsendQueryPrepared` rejects it
   ("number of parameters must be between 0 and 65535"). The list ends with an error result carrying that message.

After each case, check that `::PQpipelineStatus( conn->pgConnection() ) == PQ_PIPELINE_OFF` (pipelining builds only)
and that a following `LoggedPQexec( "SELECT 1" )` returns `PGRES_TUPLES_OK`.

### Python (`tests/src/python/test_provider_postgres.py`)

Run tests 1 and 3 with pipelining enabled and disabled
(`QgsSettings().setValue( "PostgreSQL/disablePipelining", ..., QgsSettings.Providers )`, restored afterwards).

1. **Large batch without FastInsert**: add 1201 features (more than 2 chunks) to a table with a serial PK. Include
   a few features with large geometries (e.g. 100k-vertex linestrings), so that one chunk is ended by the size cap.
   Check that `featureCount()` is right, every returned feature's `id()` and PK attribute matches the row in the
   database, and the attributes and geometry round-trip.
2. **Same with `FastInsert`**: count and contents are right.
3. **Error in a batch**: unique constraint, with the duplicate value at feature 700 of 1000 (second chunk), and
   separately at feature 100 (first chunk). Each time, `addFeatures` returns False, the table is unchanged
   (rollback), and `lastError()` contains the violation. A following `addFeatures` on the same provider succeeds,
   which shows the connection left pipeline mode cleanly.
4. **Transaction mode**: project transaction mode, or a `QgsTransaction` shared by 2 layers, and a column with a
   non-null default (so `paramValue` runs on the same connection). The column's values **must differ across
   features**, some NULL and some explicit. If every feature has the same value, the value is written into the
   INSERT text (`:2449-2488`), an all-NULL column becomes a literal `NULL`, and `paramValue()` is never called.
   Add a batch, then commit. NULL rows get the default, and explicit rows keep their values.
5. Existing add-feature tests (`testAddFeature*`, `testPktIntInsert`, `testPktMapInsert`, `testGeographyAddFeature`,
   `testNestedInsert`, `testInsertOnlyFieldIsEditable`) must still pass. They cover OID, composite and fid-map PKs,
   identity columns, generated columns, and unset values.

### Latency (`tests/src/python/test_provider_postgres_latency.py`)

Add a test modelled on `testSaveChangedGeometryToDB`: `setDelay( 100 )`, add about 2000 features through a layer
edit session and commit, `setDelay( 0 )`, then assert the time is well under the per-feature cost (e.g. < 10 s,
against about 400 s before). This file is registered as `PyQgsPostgresProviderLatency` with the POSTGRES label and
is not skipped. Without root, `tc` fails silently and the test passes without any added delay, so the bound must be
one that passes without delay too.

`setDelay()` adds the delay on `eth0` (`:55`). The local Docker Desktop database is reached through `localhost`, not
`eth0`, so even with root this test runs without delay here. It only measures the change in CI, where the test
container has `NET_ADMIN` and reaches the database container through `eth0`. Locally, use the benchmark
(Verification step 4).

## Verification

1. Build: `cmake --build build --target provider_postgres test_provider_postgresconn` (or the full build), plus
   pre-commit/astyle formatting. `provider_postgres` exists only without `FORCE_STATIC_LIBS`, and
   `PyQgsPostgresProvider` is only registered `WITH_GUI`.
2. Run tests against the test database:
   `QGIS_PGTEST_DB="service=qgis_test" ctest --test-dir build -V -R "test_provider_postgresconn|PyQgsPostgresProvider$|PyQgsPostgresTransaction|PyQgsImportIntoPostGIS|PyQgsVectorFileWriterPostgres"`.
   This covers both the pipeline and the sequential path through the setting.
3. Optional: build once without pipelining (temporarily `#undef LIBPQ_HAS_PIPELINING` after the libpq include) and
   run the same tests, to check that the `#else` branch compiles and behaves the same.
4. Latency benchmark: [`addfeatures-benchmark/`](addfeatures-benchmark/README.md). It puts a TCP proxy with a fixed
   round trip time between QGIS and the database, and counts round trips. `tc netem` doesn't work for this locally,
   because the database is reached through `localhost`, not `eth0`. The baseline is already in `results/baseline.*`.
   After the change, run:

   ```bash
   doc/addfeatures-benchmark/run.sh --label pipeline
   doc/addfeatures-benchmark/run.sh --label pipeline-nopipe --disable-pipelining
   python3 doc/addfeatures-benchmark/compare.py doc/addfeatures-benchmark/results/baseline.json \
       doc/addfeatures-benchmark/results/pipeline.json
   ```

   Expected: the point scenarios drop from 1004 round trips to about 6–10 (52 s → under 1 s at +50 ms). The defaults
   scenario keeps its 500 `paramValue()` round trips. No `direct` or large-polygon row gets slower. `pipeline-nopipe`
   matches the baseline.
5. Manual check in QGIS: paste 5000 features into a PostGIS layer and save, and run "Export to PostgreSQL" (against
   the cloud DB to compare times before and after). With the QGIS Debugging/Development Tools query log, check that
   one `PQexecPreparedBatch(addfeatures) x 500 [pipeline]` entry appears per chunk instead of 500 `PQexecPrepared`
   entries.

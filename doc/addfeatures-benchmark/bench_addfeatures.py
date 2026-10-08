#!/usr/bin/env python3
"""Benchmark QgsPostgresProvider::addFeatures over a simulated slow network.

Runs a set of insert scenarios against the QGIS test database, once with a
direct connection and once per added round trip time through latency_proxy.py,
and writes the timings to results/<label>.json and results/<label>.md.
Compare two runs with compare.py.

Run it through run.sh, which sets up the environment for a QGIS build tree.
See README.md for the method.
"""

import argparse
import ctypes
import datetime
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg2

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SCHEMA = "qgis_bench_addfeatures"
SRID = 3857

SCENARIOS = {
    "provider_points": "provider.addFeatures(), points + 3 attributes, serial PK, ids returned",
    "provider_points_fastinsert": "provider.addFeatures( FastInsert ), points + 3 attributes",
    "layer_commit_points": "layer edit session: addFeatures() + commitChanges() (time of commit)",
    "exporter_points": "QgsVectorLayerExporter.exportLayer() memory layer -> new table (FastInsert, includes CREATE TABLE)",
    "provider_points_defaults": "provider.addFeatures(), extra column with DEFAULT, every other value NULL (paramValue() SELECT per NULL)",
    "provider_large_polygons": "provider.addFeatures(), large polygons (bandwidth bound, crosses the 16M char chunk cap)",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--label",
        help="result file name (default: git short hash, plus -dirty / -nopipe)",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("QGIS_PGTEST_DB", "service=qgis_test"),
        help="libpq conninfo of the test database (default: $QGIS_PGTEST_DB or service=qgis_test)",
    )
    parser.add_argument(
        "--latencies",
        default="direct,10,50",
        help="comma separated added round trip times in ms; 'direct' = no proxy (default: direct,10,50)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="timed runs per scenario and latency (default: 3)",
    )
    parser.add_argument(
        "--points",
        type=int,
        default=1000,
        help="features in the point scenarios (default: 1000)",
    )
    parser.add_argument(
        "--large-count",
        type=int,
        default=64,
        help="polygons in provider_large_polygons (default: 64)",
    )
    parser.add_argument(
        "--large-vertices",
        type=int,
        default=10000,
        help="vertices per large polygon (default: 10000)",
    )
    parser.add_argument(
        "--scenarios",
        default=",".join(SCENARIOS),
        help="comma separated subset of: " + ", ".join(SCENARIOS),
    )
    parser.add_argument(
        "--disable-pipelining",
        action="store_true",
        help="set the hidden PostgreSQL/disablePipelining setting (no effect before the pipelining change)",
    )
    parser.add_argument(
        "--build-dir",
        default=os.environ.get("QGIS_BENCH_BUILD_DIR", str(REPO / "build")),
        help="QGIS build directory, for the metadata",
    )
    parser.add_argument(
        "--output-dir",
        default=str(HERE / "results"),
        help="where to write the results (default: results/)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="smoke test: --latencies direct,10 --repeats 1 --points 100 --large-count 4",
    )
    args = parser.parse_args()
    if args.quick:
        args.latencies = "direct,10"
        args.repeats = 1
        args.points = 100
        args.large_count = 4
    args.latencies = [s.strip() for s in args.latencies.split(",") if s.strip()]
    for lat in args.latencies:
        if lat != "direct":
            float(lat)
    args.scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    unknown = [s for s in args.scenarios if s not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios: {', '.join(unknown)}")
    return args


def run_cmd(cmd, cwd=REPO):
    try:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class LatencyProxy:
    """latency_proxy.py running in a child process, controlled through stdin/stdout."""

    def __init__(self, target_host, target_port):
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(HERE / "latency_proxy.py"),
                "--control",
                "--target",
                f"{target_host}:{target_port}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.port = json.loads(self.proc.stdout.readline())["port"]

    def command(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def set_delay(self, rtt_ms):
        self.command(f"delay {rtt_ms}")

    def reset(self):
        self.command("reset")

    def stats(self):
        return self.command("stats")

    def close(self):
        try:
            self.command("quit")
        except (OSError, ValueError):
            pass
        self.proc.wait(timeout=10)


def quote_conninfo_value(value):
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def main():
    args = parse_args()

    admin = psycopg2.connect(args.db)
    admin.autocommit = True
    info = admin.info
    if not info.host or info.host.startswith("/"):
        sys.exit(
            "The benchmark needs a TCP connection to the database (got a unix socket)."
        )

    def qgis_conninfo(host, port):
        parts = [
            f"dbname={quote_conninfo_value(info.dbname)}",
            f"host={host}",
            f"port={port}",
            f"user={quote_conninfo_value(info.user)}",
        ]
        if info.password:
            parts.append(f"password={quote_conninfo_value(info.password)}")
        parts.append("sslmode=disable")
        return " ".join(parts)

    proxy = LatencyProxy(info.host, info.port)

    # Keep the benchmark's settings away from the user's QGIS profile.
    os.environ["QGIS_CUSTOM_CONFIG_PATH"] = tempfile.mkdtemp(
        "", "QGIS-BenchAddFeatures-"
    )

    from qgis.core import (
        Qgis,
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsFeature,
        QgsFeatureSink,
        QgsGeometry,
        QgsPointXY,
        QgsSettings,
        QgsVectorLayer,
        QgsVectorLayerExporter,
    )
    from qgis.PyQt.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("QGIS")
    QCoreApplication.setOrganizationDomain("qgis.org")
    QCoreApplication.setApplicationName("QGIS-BENCH")
    app = QgsApplication([], False)
    app.initQgis()

    QgsSettings().setValue(
        "PostgreSQL/disablePipelining",
        args.disable_pipelining,
        QgsSettings.Section.Providers,
    )

    meta = collect_metadata(args, admin, Qgis)
    label = args.label or default_label(meta, args)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{label}.json"
    md_path = out_dir / f"{label}.md"

    report = {
        "label": label,
        "meta": meta,
        "config": {
            "latencies": args.latencies,
            "repeats": args.repeats,
            "points": args.points,
            "large_count": args.large_count,
            "large_vertices": args.large_vertices,
            "scenarios": args.scenarios,
            "disable_pipelining": args.disable_pipelining,
        },
        "scenario_descriptions": {s: SCENARIOS[s] for s in args.scenarios},
        "results": [],
    }

    def sql(statement):
        with admin.cursor() as cur:
            cur.execute(statement)
            return cur.fetchall() if cur.description else None

    def table_ref(table):
        return f'"{SCHEMA}"."{table}"'

    sql(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    sql(f'CREATE SCHEMA "{SCHEMA}"')

    point_columns = (
        "id serial PRIMARY KEY, name text, value double precision, category integer"
    )
    tables = {
        "keeper": f"{point_columns}, geom geometry(Point, {SRID})",
        "points": f"{point_columns}, geom geometry(Point, {SRID})",
        "points_defaults": f"{point_columns}, status text DEFAULT 'new', geom geometry(Point, {SRID})",
        "large_polygons": f"id serial PRIMARY KEY, name text, geom geometry(Polygon, {SRID})",
    }
    for table, columns in tables.items():
        sql(f"CREATE TABLE {table_ref(table)} ({columns})")

    def layer_uri(conninfo, table, geom_type):
        return f"{conninfo} key='id' srid={SRID} type={geom_type} checkPrimaryKeyUnicity='0' table={table_ref(table)} (geom)"

    def open_layer(conninfo, table, geom_type):
        layer = QgsVectorLayer(layer_uri(conninfo, table, geom_type), table, "postgres")
        if not layer.isValid():
            raise RuntimeError(f"cannot open layer {table}")
        return layer

    def point_features(fields, n, with_status=False):
        features = []
        for i in range(n):
            f = QgsFeature(fields)
            f.setAttribute("name", f"feature {i}")
            f.setAttribute("value", i * 0.5)
            f.setAttribute("category", i % 10)
            if with_status:
                f.setAttribute("status", None if i % 2 == 0 else "explicit")
            f.setGeometry(
                QgsGeometry.fromPointXY(
                    QgsPointXY(
                        2_700_000 + (i % 100) * 10.0, 8_400_000 + (i // 100) * 10.0
                    )
                )
            )
            features.append(f)
        return features

    large_geometries = []

    def large_polygon_features(fields):
        if not large_geometries:
            import math

            for i in range(args.large_count):
                ring = [
                    QgsPointXY(
                        2_700_000
                        + i * 1000
                        + 400 * math.cos(2 * math.pi * k / args.large_vertices),
                        8_400_000
                        + 400 * math.sin(2 * math.pi * k / args.large_vertices),
                    )
                    for k in range(args.large_vertices)
                ]
                ring.append(ring[0])
                large_geometries.append(QgsGeometry.fromPolygonXY([ring]))
        features = []
        for i, geom in enumerate(large_geometries):
            f = QgsFeature(fields)
            f.setAttribute("name", f"polygon {i}")
            f.setGeometry(geom)
            features.append(f)
        return features

    def check_returned_ids(out, n):
        ids = [f.id() for f in out]
        pks = [f["id"] for f in out]
        if sorted(ids) != list(range(1, n + 1)):
            return f"returned feature ids are not 1..{n}"
        if pks != ids:
            return "returned PK attributes do not match the feature ids"
        return None

    def count_rows(table):
        return sql(f"SELECT count(*) FROM {table_ref(table)}")[0][0]

    # One scenario = prepare(conninfo) -> state, then per run: reset(state), run(state) -> (ok, n, error)
    def scenario_provider_points(conninfo, flags=None, check_ids=True):
        layer = open_layer(conninfo, "points", "Point")

        def reset():
            sql(f"TRUNCATE {table_ref('points')} RESTART IDENTITY")
            return point_features(layer.fields(), args.points)

        def run(features):
            if flags is None:
                ok, out = layer.dataProvider().addFeatures(features)
            else:
                ok, out = layer.dataProvider().addFeatures(features, flags)
            return ok, out

        def verify(result):
            ok, out = result
            if not ok:
                return "addFeatures returned False: " + "; ".join(
                    layer.dataProvider().errors()
                )
            if count_rows("points") != args.points:
                return f"expected {args.points} rows, got {count_rows('points')}"
            return check_returned_ids(out, args.points) if check_ids else None

        return layer, reset, run, verify, args.points

    def scenario_layer_commit(conninfo):
        layer = open_layer(conninfo, "points", "Point")

        def reset():
            sql(f"TRUNCATE {table_ref('points')} RESTART IDENTITY")
            layer.reload()
            if not layer.startEditing():
                raise RuntimeError("cannot start editing")
            added = layer.addFeatures(point_features(layer.fields(), args.points))
            if not (added[0] if isinstance(added, tuple) else added):
                raise RuntimeError("cannot add features to the edit buffer")
            return None

        def run(_):
            return layer.commitChanges()

        def verify(ok):
            if not ok:
                return "commitChanges returned False: " + "; ".join(
                    layer.commitErrors()
                )
            if count_rows("points") != args.points:
                return f"expected {args.points} rows, got {count_rows('points')}"
            return None

        return layer, reset, run, verify, args.points

    def scenario_exporter(conninfo):
        source = QgsVectorLayer(
            f"Point?crs=EPSG:{SRID}&field=name:string&field=value:double&field=category:integer",
            "source",
            "memory",
        )
        source.dataProvider().addFeatures(point_features(source.fields(), args.points))
        uri = f"{conninfo} key='id' table={table_ref('exported')} (geom)"
        crs = QgsCoordinateReferenceSystem(f"EPSG:{SRID}")

        def reset():
            sql(f"DROP TABLE IF EXISTS {table_ref('exported')}")
            return None

        def run(_):
            return QgsVectorLayerExporter.exportLayer(
                source, uri, "postgres", crs, False, {"overwrite": True}
            )

        def verify(result):
            code, message = result
            if code != Qgis.VectorExportResult.Success:
                return f"export failed: {code} {message}"
            if count_rows("exported") != args.points:
                return f"expected {args.points} rows, got {count_rows('exported')}"
            return None

        return source, reset, run, verify, args.points

    def scenario_defaults(conninfo):
        layer = open_layer(conninfo, "points_defaults", "Point")

        def reset():
            sql(f"TRUNCATE {table_ref('points_defaults')} RESTART IDENTITY")
            return point_features(layer.fields(), args.points, with_status=True)

        def run(features):
            return layer.dataProvider().addFeatures(features)

        def verify(result):
            ok, out = result
            if not ok:
                return "addFeatures returned False: " + "; ".join(
                    layer.dataProvider().errors()
                )
            counts = sql(
                "SELECT count(*) FILTER (WHERE status = 'new'), count(*) FILTER (WHERE status = 'explicit'), count(*) "
                f"FROM {table_ref('points_defaults')}"
            )[0]
            expected = ((args.points + 1) // 2, args.points // 2, args.points)
            if tuple(counts) != expected:
                return f"expected (default, explicit, total) = {expected}, got {tuple(counts)}"
            if [f["status"] for f in out] != [
                "new" if i % 2 == 0 else "explicit" for i in range(args.points)
            ]:
                return "returned status attributes do not match"
            return check_returned_ids(out, args.points)

        return layer, reset, run, verify, args.points

    def scenario_large_polygons(conninfo):
        layer = open_layer(conninfo, "large_polygons", "Polygon")

        def reset():
            sql(f"TRUNCATE {table_ref('large_polygons')} RESTART IDENTITY")
            return large_polygon_features(layer.fields())

        def run(features):
            return layer.dataProvider().addFeatures(features)

        def verify(result):
            ok, out = result
            if not ok:
                return "addFeatures returned False: " + "; ".join(
                    layer.dataProvider().errors()
                )
            count, points = sql(
                f"SELECT count(*), sum(ST_NPoints(geom)) FROM {table_ref('large_polygons')}"
            )[0]
            if (count, points) != (
                args.large_count,
                args.large_count * (args.large_vertices + 1),
            ):
                return f"unexpected row count / vertex count: {count} / {points}"
            return check_returned_ids(out, args.large_count)

        return layer, reset, run, verify, args.large_count

    scenario_factories = {
        "provider_points": scenario_provider_points,
        "provider_points_fastinsert": lambda c: scenario_provider_points(
            c, QgsFeatureSink.Flag.FastInsert, check_ids=False
        ),
        "layer_commit_points": scenario_layer_commit,
        "exporter_points": scenario_exporter,
        "provider_points_defaults": scenario_defaults,
        "provider_large_polygons": scenario_large_polygons,
    }

    # Layers that stay open for the whole run. Connections are shared per conninfo and closed when
    # the last provider using them goes away, so these keep the RO and RW connections open and the
    # connection setup out of the timed sections.
    keepers = {}

    def keep_connections_open(conninfo):
        if conninfo in keepers:
            return
        layer = open_layer(conninfo, "keeper", "Point")
        ok, _ = layer.dataProvider().addFeatures(point_features(layer.fields(), 1))
        if not ok:
            raise RuntimeError(
                "warm-up insert failed: " + "; ".join(layer.dataProvider().errors())
            )
        keepers[conninfo] = layer

    def save():
        json_path.write_text(json.dumps(report, indent=2) + "\n")
        md_path.write_text(markdown_report(report) + "\n")

    failures = 0
    try:
        for latency in args.latencies:
            if latency == "direct":
                conninfo = qgis_conninfo(info.host, info.port)
                proxied = False
            else:
                proxy.set_delay(float(latency))
                conninfo = qgis_conninfo("127.0.0.1", proxy.port)
                proxied = True
            keep_connections_open(conninfo)

            for name in args.scenarios:
                _layer, reset, run, verify, n_features = scenario_factories[name](
                    conninfo
                )
                runs = []
                for rep in range(args.repeats):
                    run_input = reset()
                    if proxied:
                        proxy.reset()
                    start = time.perf_counter()
                    result = run(run_input)
                    seconds = time.perf_counter() - start
                    stats = proxy.stats() if proxied else None
                    error = verify(result)
                    if error:
                        failures += 1
                    runs.append(
                        {"seconds": round(seconds, 4), "error": error, "proxy": stats}
                    )
                    turns = f", {stats['client_turns']} client turns" if stats else ""
                    status = f" ERROR: {error}" if error else ""
                    print(
                        f"[{latency_label(latency)}] {name} {rep + 1}/{args.repeats}: {seconds:.3f} s{turns}{status}",
                        flush=True,
                    )
                report["results"].append(summarize(name, latency, n_features, runs))
                save()
    finally:
        keepers.clear()
        proxy.close()
        sql(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        admin.close()

    print(f"\nWrote {json_path}\nWrote {md_path}")
    if failures:
        print(f"{failures} run(s) failed verification", file=sys.stderr)
    app.exitQgis()
    return 1 if failures else 0


def latency_label(latency):
    return "direct" if latency == "direct" else f"+{latency} ms"


def summarize(name, latency, n_features, runs):
    times = [r["seconds"] for r in runs if not r["error"]]
    turns = [r["proxy"]["client_turns"] for r in runs if r["proxy"] and not r["error"]]
    median = statistics.median(times) if times else None
    return {
        "scenario": name,
        "latency": latency,
        "features": n_features,
        "runs": runs,
        "ok": len(times) == len(runs),
        "median_s": round(median, 4) if median is not None else None,
        "min_s": min(times) if times else None,
        "max_s": max(times) if times else None,
        "ms_per_feature": round(1000 * median / n_features, 3)
        if median is not None
        else None,
        "client_turns": int(statistics.median(turns)) if turns else None,
    }


def collect_metadata(args, admin, Qgis):
    build_dir = Path(args.build_dir)
    build_type = None
    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        m = re.search(
            r"^CMAKE_BUILD_TYPE:STRING=(.*)$", cache.read_text(errors="replace"), re.M
        )
        build_type = m.group(1) if m else None

    provider_lib = (
        build_dir / "output" / "lib" / "qgis" / "plugins" / "libprovider_postgres.so"
    )
    libpq_path = None
    libpq_version = None
    ldd = run_cmd(["ldd", str(provider_lib)]) if provider_lib.exists() else None
    if ldd:
        m = re.search(r"libpq\.so\S*\s+=>\s+(\S+)", ldd)
        libpq_path = m.group(1) if m else None
    try:
        libpq_version = ctypes.CDLL(libpq_path or "libpq.so.5").PQlibVersion()
    except OSError:
        pass

    with admin.cursor() as cur:
        cur.execute("SELECT version(), postgis_lib_version()")
        server_version, postgis_version = cur.fetchone()

    provider_diff = run_cmd(
        ["git", "diff", "HEAD", "--stat", "--", "src/providers/postgres"]
    )
    method = hashlib.sha1()
    for script in ("bench_addfeatures.py", "latency_proxy.py"):
        method.update((HERE / script).read_bytes())
    return {
        "method_sha1": method.hexdigest()[:12],
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "host": platform.node(),
        "platform": platform.platform(),
        "cpus": os.cpu_count(),
        "load_avg_at_start": [round(x, 2) for x in os.getloadavg()],
        "python": platform.python_version(),
        "git_commit": run_cmd(["git", "rev-parse", "HEAD"]),
        "git_branch": run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "provider_diff_stat": provider_diff,
        "provider_dirty": bool(provider_diff),
        "qgis_version": Qgis.version(),
        "build_dir": str(build_dir),
        "build_type": build_type,
        "provider_lib_mtime": (
            datetime.datetime.fromtimestamp(provider_lib.stat().st_mtime).isoformat(
                timespec="seconds"
            )
            if provider_lib.exists()
            else None
        ),
        "libpq_path": libpq_path,
        "libpq_version": libpq_version,
        "server_version": server_version,
        "postgis_version": postgis_version,
        "db": f"{admin.info.host}:{admin.info.port}/{admin.info.dbname}",
    }


def default_label(meta, args):
    label = (meta["git_commit"] or "unknown")[:10]
    if meta["provider_dirty"]:
        label += "-dirty"
    if args.disable_pipelining:
        label += "-nopipe"
    return label


def fmt(value, digits=3):
    return "–" if value is None else f"{value:.{digits}f}"


def markdown_report(report):
    meta = report["meta"]
    cfg = report["config"]
    lines = [
        f"# addFeatures benchmark: {report['label']}",
        "",
        f"- Date: {meta['timestamp']} on {meta['host']} ({meta['platform']}, {meta['cpus']} CPUs, load at start {meta['load_avg_at_start']})",
        f"- Commit: {meta['git_commit']} ({meta['git_branch']}), provider changes vs HEAD: {'yes' if meta['provider_dirty'] else 'none'}",
        f"- QGIS {meta['qgis_version']}, {meta['build_type']} build, provider built {meta['provider_lib_mtime']}",
        f"- libpq {meta['libpq_version']} ({meta['libpq_path']}), server: {meta['server_version'].split(',')[0]}, PostGIS {meta['postgis_version']}",
        f"- {cfg['repeats']} runs per row, median shown. PostgreSQL/disablePipelining = {cfg['disable_pipelining']}",
        "",
        "| scenario | latency | features | median s | min s | max s | ms / feature | client turns |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in report["results"]:
        status = "" if r["ok"] else " (FAILED)"
        lines.append(
            f"| {r['scenario']}{status} | {latency_label(r['latency'])} | {r['features']} | {fmt(r['median_s'])} | "
            f"{fmt(r['min_s'])} | {fmt(r['max_s'])} | {fmt(r['ms_per_feature'], 2)} | {r['client_turns'] if r['client_turns'] is not None else '–'} |"
        )
    lines += ["", "Scenarios:", ""]
    lines += [
        f"- `{name}`: {desc}" for name, desc in report["scenario_descriptions"].items()
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())

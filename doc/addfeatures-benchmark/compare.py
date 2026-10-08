#!/usr/bin/env python3
"""Compare two bench_addfeatures.py result files.

    python3 compare.py results/baseline.json results/after.json [--output results/compare.md]

Prints a Markdown table with the median time of each scenario and latency in
both runs, the speedup (before / after) and the client turns (round trips the
client waited for). Rows where the second run is more than --tolerance slower
are marked as possible regressions.
"""

import argparse
import json
import sys


def latency_label(latency):
    return "direct" if latency == "direct" else f"+{latency} ms"


def fmt(value, digits=3):
    return "–" if value is None else f"{value:.{digits}f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--output", help="also write the table to this file")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.10,
        help="slowdown marked as regression (default: 0.10 = 10%%)",
    )
    args = parser.parse_args()

    with open(args.before) as f:
        before = json.load(f)
    with open(args.after) as f:
        after = json.load(f)

    lines = [f"# addFeatures benchmark: {before['label']} vs {after['label']}", ""]
    for name, report in (("before", before), ("after", after)):
        meta, cfg = report["meta"], report["config"]
        lines.append(
            f"- **{name}** `{report['label']}`: {meta['git_commit'][:10]} ({meta['git_branch']}), "
            f"provider changes: {'yes' if meta['provider_dirty'] else 'none'}, {meta['build_type']} build, "
            f"libpq {meta['libpq_version']}, disablePipelining={cfg['disable_pipelining']}, "
            f"{cfg['repeats']} runs, {meta['timestamp']} on {meta['host']}"
        )

    warnings = []
    for key in ("points", "large_count", "large_vertices"):
        if before["config"][key] != after["config"][key]:
            warnings.append(
                f"`{key}` differs: {before['config'][key]} vs {after['config'][key]}"
            )
    for key in ("method_sha1", "host", "build_type", "server_version"):
        if before["meta"][key] != after["meta"][key]:
            warnings.append(
                f"`{key}` differs: {before['meta'][key]} vs {after['meta'][key]}"
            )
    if warnings:
        lines += ["", "**Not directly comparable:** " + "; ".join(warnings)]

    lines += [
        "",
        "| scenario | latency | before s | after s | speedup | before turns | after turns | note |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    after_rows = {(r["scenario"], r["latency"]): r for r in after["results"]}
    regressions = 0
    for b in before["results"]:
        a = after_rows.pop((b["scenario"], b["latency"]), None)
        if a is None:
            continue
        note = []
        if not b["ok"]:
            note.append("before FAILED")
        if not a["ok"]:
            note.append("after FAILED")
        speedup = None
        if b["median_s"] and a["median_s"]:
            speedup = b["median_s"] / a["median_s"]
            if a["median_s"] > b["median_s"] * (1 + args.tolerance):
                note.append("slower")
                regressions += 1
        lines.append(
            f"| {b['scenario']} | {latency_label(b['latency'])} | {fmt(b['median_s'])} | {fmt(a['median_s'])} | "
            f"{fmt(speedup, 1) + '×' if speedup else '–'} | {b['client_turns'] if b['client_turns'] is not None else '–'} | "
            f"{a['client_turns'] if a['client_turns'] is not None else '–'} | {', '.join(note)} |"
        )
    if after_rows:
        lines += [
            "",
            "Only in after: "
            + ", ".join(f"{s} ({latency_label(l)})" for s, l in after_rows),
        ]

    text = "\n".join(lines)
    print(text)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text + "\n")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())

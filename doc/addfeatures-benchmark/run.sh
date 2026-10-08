#!/usr/bin/env bash
# Runs bench_addfeatures.py against a QGIS build tree, with the same environment ctest uses for
# the Python tests. Arguments are passed on to bench_addfeatures.py (see --help).
#
#   doc/addfeatures-benchmark/run.sh --label baseline
#   QGIS_BUILD_DIR=/path/to/build doc/addfeatures-benchmark/run.sh --label after
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUILD="${QGIS_BUILD_DIR:-$REPO/build}"

if [ ! -e "$BUILD/output/lib/qgis/plugins/libprovider_postgres.so" ]; then
  echo "No postgres provider in $BUILD/output (set QGIS_BUILD_DIR)" >&2
  exit 1
fi

export QGIS_PREFIX_PATH="$BUILD/output"
export LD_LIBRARY_PATH="$BUILD/output/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$BUILD/output/python:$BUILD/output/python/plugins${PYTHONPATH:+:$PYTHONPATH}"
export QGIS_BENCH_BUILD_DIR="$BUILD"
export QT_QPA_PLATFORM=offscreen
# Debug builds print QgsDebugMsg output at level 1 by default, which would be timed too.
export QGIS_DEBUG=0

exec python3 "$HERE/bench_addfeatures.py" "$@"

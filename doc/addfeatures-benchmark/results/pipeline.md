# addFeatures benchmark: pipeline

- Date: 2026-10-08T08:53:37+00:00 on GISPO-LK02 (Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.43, 22 CPUs, load at start [0.75, 4.11, 5.05])
- Commit: 204b5e404307ca9363c3e056f3f62bc7d3f1cea0 (master), provider changes vs HEAD: yes
- QGIS 4.3.0-Master, Debug build, provider built 2026-10-08T11:41:26
- libpq 180006 (/usr/lib/x86_64-linux-gnu/libpq.so.5), server: PostgreSQL 11.8 (Debian 11.8-1.pgdg100+1) on x86_64-pc-linux-gnu, PostGIS 2.5.4
- 3 runs per row, median shown. PostgreSQL/disablePipelining = False

| scenario | latency | features | median s | min s | max s | ms / feature | client turns |
|---|---|---:|---:|---:|---:|---:|---:|
| provider_points | direct | 1000 | 0.021 | 0.017 | 0.023 | 0.02 | – |
| provider_points_fastinsert | direct | 1000 | 0.019 | 0.019 | 0.020 | 0.02 | – |
| layer_commit_points | direct | 1000 | 0.023 | 0.023 | 0.024 | 0.02 | – |
| exporter_points | direct | 1000 | 0.046 | 0.043 | 0.047 | 0.05 | – |
| provider_points_defaults | direct | 1000 | 0.105 | 0.102 | 0.123 | 0.10 | – |
| provider_large_polygons | direct | 64 | 1.863 | 1.696 | 1.867 | 29.11 | – |
| provider_points | +10 ms | 1000 | 0.088 | 0.085 | 0.088 | 0.09 | 6 |
| provider_points_fastinsert | +10 ms | 1000 | 0.086 | 0.083 | 0.086 | 0.09 | 6 |
| layer_commit_points | +10 ms | 1000 | 0.091 | 0.091 | 0.093 | 0.09 | 6 |
| exporter_points | +10 ms | 1000 | 0.560 | 0.558 | 0.562 | 0.56 | 46 |
| provider_points_defaults | +10 ms | 1000 | 5.709 | 5.690 | 5.720 | 5.71 | 506 |
| provider_large_polygons | +10 ms | 64 | 1.843 | 1.830 | 1.876 | 28.80 | 7 |
| provider_points | +50 ms | 1000 | 0.334 | 0.333 | 0.336 | 0.33 | 6 |
| provider_points_fastinsert | +50 ms | 1000 | 0.335 | 0.332 | 0.340 | 0.34 | 6 |
| layer_commit_points | +50 ms | 1000 | 0.338 | 0.336 | 0.338 | 0.34 | 6 |
| exporter_points | +50 ms | 1000 | 2.458 | 2.445 | 2.463 | 2.46 | 46 |
| provider_points_defaults | +50 ms | 1000 | 26.494 | 26.490 | 26.523 | 26.49 | 506 |
| provider_large_polygons | +50 ms | 64 | 2.054 | 2.034 | 2.055 | 32.09 | 7 |

Scenarios:

- `provider_points`: provider.addFeatures(), points + 3 attributes, serial PK, ids returned
- `provider_points_fastinsert`: provider.addFeatures( FastInsert ), points + 3 attributes
- `layer_commit_points`: layer edit session: addFeatures() + commitChanges() (time of commit)
- `exporter_points`: QgsVectorLayerExporter.exportLayer() memory layer -> new table (FastInsert, includes CREATE TABLE)
- `provider_points_defaults`: provider.addFeatures(), extra column with DEFAULT, every other value NULL (paramValue() SELECT per NULL)
- `provider_large_polygons`: provider.addFeatures(), large polygons (bandwidth bound, crosses the 16M char chunk cap)

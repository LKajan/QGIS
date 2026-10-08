# addFeatures benchmark: baseline

- Date: 2026-10-08T07:26:07+00:00 on GISPO-LK02 (Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.43, 22 CPUs, load at start [0.29, 0.37, 1.61])
- Commit: 204b5e404307ca9363c3e056f3f62bc7d3f1cea0 (master), provider changes vs HEAD: none
- QGIS 4.3.0-Master, Debug build, provider built 2026-10-08T09:39:52
- libpq 180006 (/usr/lib/x86_64-linux-gnu/libpq.so.5), server: PostgreSQL 11.8 (Debian 11.8-1.pgdg100+1) on x86_64-pc-linux-gnu, PostGIS 2.5.4
- 3 runs per row, median shown. PostgreSQL/disablePipelining = False

| scenario | latency | features | median s | min s | max s | ms / feature | client turns |
|---|---|---:|---:|---:|---:|---:|---:|
| provider_points | direct | 1000 | 0.211 | 0.200 | 0.216 | 0.21 | – |
| provider_points_fastinsert | direct | 1000 | 0.179 | 0.177 | 0.195 | 0.18 | – |
| layer_commit_points | direct | 1000 | 0.206 | 0.198 | 0.209 | 0.21 | – |
| exporter_points | direct | 1000 | 0.277 | 0.216 | 0.313 | 0.28 | – |
| provider_points_defaults | direct | 1000 | 0.316 | 0.287 | 0.379 | 0.32 | – |
| provider_large_polygons | direct | 64 | 1.755 | 1.747 | 1.937 | 27.43 | – |
| provider_points | +10 ms | 1000 | 11.261 | 11.245 | 11.370 | 11.26 | 1004 |
| provider_points_fastinsert | +10 ms | 1000 | 11.301 | 11.242 | 11.413 | 11.30 | 1004 |
| layer_commit_points | +10 ms | 1000 | 11.234 | 11.213 | 11.284 | 11.23 | 1004 |
| exporter_points | +10 ms | 1000 | 11.710 | 11.687 | 11.831 | 11.71 | 1044 |
| provider_points_defaults | +10 ms | 1000 | 16.961 | 16.836 | 16.966 | 16.96 | 1504 |
| provider_large_polygons | +10 ms | 64 | 2.522 | 2.498 | 2.543 | 39.41 | 68 |
| provider_points | +50 ms | 1000 | 52.399 | 52.378 | 52.426 | 52.40 | 1004 |
| provider_points_fastinsert | +50 ms | 1000 | 52.376 | 52.356 | 52.400 | 52.38 | 1004 |
| layer_commit_points | +50 ms | 1000 | 52.396 | 52.380 | 52.461 | 52.40 | 1004 |
| exporter_points | +50 ms | 1000 | 54.468 | 54.462 | 54.470 | 54.47 | 1044 |
| provider_points_defaults | +50 ms | 1000 | 78.519 | 78.462 | 78.551 | 78.52 | 1504 |
| provider_large_polygons | +50 ms | 64 | 5.175 | 5.169 | 5.256 | 80.86 | 68 |

Scenarios:

- `provider_points`: provider.addFeatures(), points + 3 attributes, serial PK, ids returned
- `provider_points_fastinsert`: provider.addFeatures( FastInsert ), points + 3 attributes
- `layer_commit_points`: layer edit session: addFeatures() + commitChanges() (time of commit)
- `exporter_points`: QgsVectorLayerExporter.exportLayer() memory layer -> new table (FastInsert, includes CREATE TABLE)
- `provider_points_defaults`: provider.addFeatures(), extra column with DEFAULT, every other value NULL (paramValue() SELECT per NULL)
- `provider_large_polygons`: provider.addFeatures(), large polygons (bandwidth bound, crosses the 16M char chunk cap)

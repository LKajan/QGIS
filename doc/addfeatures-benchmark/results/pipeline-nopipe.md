# addFeatures benchmark: pipeline-nopipe

- Date: 2026-10-08T08:56:24+00:00 on GISPO-LK02 (Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.43, 22 CPUs, load at start [0.53, 2.42, 4.23])
- Commit: 204b5e404307ca9363c3e056f3f62bc7d3f1cea0 (master), provider changes vs HEAD: yes
- QGIS 4.3.0-Master, Debug build, provider built 2026-10-08T11:41:26
- libpq 180006 (/usr/lib/x86_64-linux-gnu/libpq.so.5), server: PostgreSQL 11.8 (Debian 11.8-1.pgdg100+1) on x86_64-pc-linux-gnu, PostGIS 2.5.4
- 3 runs per row, median shown. PostgreSQL/disablePipelining = True

| scenario | latency | features | median s | min s | max s | ms / feature | client turns |
|---|---|---:|---:|---:|---:|---:|---:|
| provider_points | direct | 1000 | 0.152 | 0.151 | 0.162 | 0.15 | – |
| provider_points_fastinsert | direct | 1000 | 0.148 | 0.141 | 0.160 | 0.15 | – |
| layer_commit_points | direct | 1000 | 0.149 | 0.146 | 0.166 | 0.15 | – |
| exporter_points | direct | 1000 | 0.168 | 0.162 | 0.172 | 0.17 | – |
| provider_points_defaults | direct | 1000 | 0.237 | 0.224 | 0.247 | 0.24 | – |
| provider_large_polygons | direct | 64 | 1.768 | 1.722 | 1.789 | 27.62 | – |
| provider_points | +10 ms | 1000 | 11.232 | 11.220 | 11.242 | 11.23 | 1004 |
| provider_points_fastinsert | +10 ms | 1000 | 11.229 | 11.229 | 11.231 | 11.23 | 1004 |
| layer_commit_points | +10 ms | 1000 | 11.222 | 11.219 | 11.227 | 11.22 | 1004 |
| exporter_points | +10 ms | 1000 | 11.678 | 11.675 | 11.689 | 11.68 | 1044 |
| provider_points_defaults | +10 ms | 1000 | 16.879 | 16.871 | 16.892 | 16.88 | 1504 |
| provider_large_polygons | +10 ms | 64 | 2.521 | 2.475 | 2.671 | 39.40 | 68 |
| provider_points | +50 ms | 1000 | 52.556 | 52.530 | 52.566 | 52.56 | 1004 |
| provider_points_fastinsert | +50 ms | 1000 | 52.503 | 52.451 | 52.516 | 52.50 | 1004 |
| layer_commit_points | +50 ms | 1000 | 52.486 | 52.466 | 52.509 | 52.49 | 1004 |
| exporter_points | +50 ms | 1000 | 54.640 | 54.634 | 54.647 | 54.64 | 1044 |
| provider_points_defaults | +50 ms | 1000 | 78.613 | 78.513 | 78.695 | 78.61 | 1504 |
| provider_large_polygons | +50 ms | 64 | 5.399 | 5.311 | 5.501 | 84.36 | 68 |

Scenarios:

- `provider_points`: provider.addFeatures(), points + 3 attributes, serial PK, ids returned
- `provider_points_fastinsert`: provider.addFeatures( FastInsert ), points + 3 attributes
- `layer_commit_points`: layer edit session: addFeatures() + commitChanges() (time of commit)
- `exporter_points`: QgsVectorLayerExporter.exportLayer() memory layer -> new table (FastInsert, includes CREATE TABLE)
- `provider_points_defaults`: provider.addFeatures(), extra column with DEFAULT, every other value NULL (paramValue() SELECT per NULL)
- `provider_large_polygons`: provider.addFeatures(), large polygons (bandwidth bound, crosses the 16M char chunk cap)

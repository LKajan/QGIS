# addFeatures benchmark: baseline vs pipeline-nopipe

- **before** `baseline`: 204b5e4043 (master), provider changes: none, Debug build, libpq 180006, disablePipelining=False, 3 runs, 2026-10-08T07:26:07+00:00 on GISPO-LK02
- **after** `pipeline-nopipe`: 204b5e4043 (master), provider changes: yes, Debug build, libpq 180006, disablePipelining=True, 3 runs, 2026-10-08T08:56:24+00:00 on GISPO-LK02

| scenario | latency | before s | after s | speedup | before turns | after turns | note |
|---|---|---:|---:|---:|---:|---:|---|
| provider_points | direct | 0.211 | 0.152 | 1.4× | – | – |  |
| provider_points_fastinsert | direct | 0.179 | 0.148 | 1.2× | – | – |  |
| layer_commit_points | direct | 0.206 | 0.149 | 1.4× | – | – |  |
| exporter_points | direct | 0.277 | 0.168 | 1.6× | – | – |  |
| provider_points_defaults | direct | 0.316 | 0.237 | 1.3× | – | – |  |
| provider_large_polygons | direct | 1.755 | 1.768 | 1.0× | – | – |  |
| provider_points | +10 ms | 11.261 | 11.232 | 1.0× | 1004 | 1004 |  |
| provider_points_fastinsert | +10 ms | 11.301 | 11.229 | 1.0× | 1004 | 1004 |  |
| layer_commit_points | +10 ms | 11.234 | 11.222 | 1.0× | 1004 | 1004 |  |
| exporter_points | +10 ms | 11.710 | 11.678 | 1.0× | 1044 | 1044 |  |
| provider_points_defaults | +10 ms | 16.961 | 16.879 | 1.0× | 1504 | 1504 |  |
| provider_large_polygons | +10 ms | 2.522 | 2.521 | 1.0× | 68 | 68 |  |
| provider_points | +50 ms | 52.399 | 52.556 | 1.0× | 1004 | 1004 |  |
| provider_points_fastinsert | +50 ms | 52.376 | 52.503 | 1.0× | 1004 | 1004 |  |
| layer_commit_points | +50 ms | 52.396 | 52.486 | 1.0× | 1004 | 1004 |  |
| exporter_points | +50 ms | 54.468 | 54.640 | 1.0× | 1044 | 1044 |  |
| provider_points_defaults | +50 ms | 78.519 | 78.613 | 1.0× | 1504 | 1504 |  |
| provider_large_polygons | +50 ms | 5.175 | 5.399 | 1.0× | 68 | 68 |  |

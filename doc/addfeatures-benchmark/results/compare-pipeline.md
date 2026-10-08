# addFeatures benchmark: baseline vs pipeline

- **before** `baseline`: 204b5e4043 (master), provider changes: none, Debug build, libpq 180006, disablePipelining=False, 3 runs, 2026-10-08T07:26:07+00:00 on GISPO-LK02
- **after** `pipeline`: 204b5e4043 (master), provider changes: yes, Debug build, libpq 180006, disablePipelining=False, 3 runs, 2026-10-08T08:53:37+00:00 on GISPO-LK02

| scenario | latency | before s | after s | speedup | before turns | after turns | note |
|---|---|---:|---:|---:|---:|---:|---|
| provider_points | direct | 0.211 | 0.021 | 10.0× | – | – |  |
| provider_points_fastinsert | direct | 0.179 | 0.019 | 9.2× | – | – |  |
| layer_commit_points | direct | 0.206 | 0.023 | 8.9× | – | – |  |
| exporter_points | direct | 0.277 | 0.046 | 6.0× | – | – |  |
| provider_points_defaults | direct | 0.316 | 0.105 | 3.0× | – | – |  |
| provider_large_polygons | direct | 1.755 | 1.863 | 0.9× | – | – |  |
| provider_points | +10 ms | 11.261 | 0.088 | 128.1× | 1004 | 6 |  |
| provider_points_fastinsert | +10 ms | 11.301 | 0.086 | 132.2× | 1004 | 6 |  |
| layer_commit_points | +10 ms | 11.234 | 0.091 | 123.0× | 1004 | 6 |  |
| exporter_points | +10 ms | 11.710 | 0.560 | 20.9× | 1044 | 46 |  |
| provider_points_defaults | +10 ms | 16.961 | 5.709 | 3.0× | 1504 | 506 |  |
| provider_large_polygons | +10 ms | 2.522 | 1.843 | 1.4× | 68 | 7 |  |
| provider_points | +50 ms | 52.399 | 0.334 | 156.8× | 1004 | 6 |  |
| provider_points_fastinsert | +50 ms | 52.376 | 0.335 | 156.6× | 1004 | 6 |  |
| layer_commit_points | +50 ms | 52.396 | 0.338 | 155.0× | 1004 | 6 |  |
| exporter_points | +50 ms | 54.468 | 2.458 | 22.2× | 1044 | 46 |  |
| provider_points_defaults | +50 ms | 78.519 | 26.494 | 3.0× | 1504 | 506 |  |
| provider_large_polygons | +50 ms | 5.175 | 2.054 | 2.5× | 68 | 7 |  |

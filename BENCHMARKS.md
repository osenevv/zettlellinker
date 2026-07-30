# ZettelLinker Benchmarks

## HNSW warm-query baseline

Measured on the development machine with `benchmarks/benchmark_hnsw.py`:

| Measurement | Result |
|---|---:|
| Platform | macOS 15.0.1, Apple arm64 |
| Python | 3.12.13 |
| Synthetic notes/vectors | 10,000 |
| Dimensions | 384 |
| Index build | 2.5632 seconds |
| Persisted index load | 0.0134 seconds |
| Median query | 0.3783 ms |
| 95th percentile query | 0.6072 ms |
| Index size | 16.847 MB |

Seed: `42`. Each of 200 queries requested 32 neighbors.

This isolates HNSW performance. It does **not** include vault traversal, changed-file
hashing, embedding inference, or automatic anchor selection. Run the benchmark on
another platform with:

```bash
python benchmarks/benchmark_hnsw.py
```

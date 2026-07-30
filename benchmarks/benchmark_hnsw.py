"""Reproducible warm HNSW benchmark for the 10,000-note MVP target."""

from __future__ import annotations

import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np

from zettellinker.config import SemanticConfig
from zettellinker.semantic import USearchIndex, normalize


COUNT = 10_000
DIMENSIONS = 384
QUERIES = 200
SEED = 42


def main() -> None:
    random = np.random.default_rng(SEED)
    vectors = random.normal(size=(COUNT, DIMENSIONS)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    keys = np.arange(COUNT, dtype=np.uint64)
    config = SemanticConfig()

    started = time.perf_counter()
    index = USearchIndex(config)
    index.build(keys, vectors)
    build_seconds = time.perf_counter() - started

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "benchmark.usearch"
        index.save(path)
        index_bytes = path.stat().st_size
        started = time.perf_counter()
        loaded = USearchIndex(config)
        loaded.load(path, DIMENSIONS)
        load_seconds = time.perf_counter() - started

        latencies = []
        for vector in vectors[:QUERIES]:
            started = time.perf_counter()
            loaded.search(normalize(vector), 32)
            latencies.append((time.perf_counter() - started) * 1000)

    latencies.sort()
    payload = {
        "notes": COUNT,
        "dimensions": DIMENSIONS,
        "queries": QUERIES,
        "seed": SEED,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "build_seconds": round(build_seconds, 4),
        "load_seconds": round(load_seconds, 4),
        "index_megabytes": round(index_bytes / 1_000_000, 3),
        "query_ms_median": round(statistics.median(latencies), 4),
        "query_ms_p95": round(latencies[int(len(latencies) * 0.95) - 1], 4),
        "scope": "HNSW only; excludes file scanning and embedding inference",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

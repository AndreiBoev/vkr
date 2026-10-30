#!/usr/bin/env python3
"""Generate BookSim anynet files for complete and circulant cluster graphs."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "experiments" / "topologies"


def _canonical_generators(n: int, generators: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for raw in generators:
        g = raw % n
        if g == 0:
            continue
        g = min(g, n - g)
        if g not in seen:
            seen.add(g)
            result.append(g)
    return result


def circulant_undirected_edges(n: int, generators: list[int]) -> list[tuple[int, int]]:
    gens = _canonical_generators(n, generators)
    edges: set[tuple[int, int]] = set()
    for i in range(n):
        for g in gens:
            j = (i + g) % n
            edges.add((min(i, j), max(i, j)))
    return sorted(edges)


def complete_undirected_edges(n: int) -> list[tuple[int, int]]:
    return [(i, j) for i in range(n) for j in range(i + 1, n)]


def chord_length(n: int, u: int, v: int) -> int:
    d = abs(u - v)
    return min(d, n - d)


def degree_from_edges(n: int, edges: list[tuple[int, int]]) -> int:
    counts = [0] * n
    for u, v in edges:
        counts[u] += 1
        counts[v] += 1
    if not counts:
        return 0
    if min(counts) != max(counts):
        raise ValueError(f"graph is not regular: {counts}")
    return counts[0]


def bfs_distance(n: int, adj: list[list[int]], src: int) -> list[int]:
    dist = [-1] * n
    dist[src] = 0
    q: deque[int] = deque([src])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if dist[v] < 0:
                dist[v] = dist[u] + 1
                q.append(v)
    if any(d < 0 for d in dist):
        raise ValueError("graph is disconnected")
    return dist


def graph_metrics(n: int, edges: list[tuple[int, int]]) -> dict:
    adj: list[list[int]] = [[] for _ in range(n)]
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    all_dist: list[int] = []
    eccentricity: list[int] = []
    for src in range(n):
        dist = bfs_distance(n, adj, src)
        eccentricity.append(max(dist))
        all_dist.extend(d for d in dist if d > 0)
    hop_sum = sum(all_dist)
    hop_pairs = len(all_dist)
    return {
        "degree": degree_from_edges(n, edges),
        "undirected_links": len(edges),
        "directed_channels": 2 * len(edges),
        "diameter": max(eccentricity),
        "average_shortest_path": hop_sum / hop_pairs if hop_pairs else 0.0,
        "router_radix": degree_from_edges(n, edges) + 1,
    }


def edge_latency(n: int, u: int, v: int, mode: str) -> int:
    if mode == "uniform":
        return 1
    if mode == "hetero":
        # Короткие хорды — быстрые/широкие каналы, длинные — более «дешёвые» и медленные.
        # BookSim anynet не умеет задавать ширину канала, поэтому неоднородность
        # пропускной способности моделируется задержкой сериализации.
        chord = chord_length(n, u, v)
        return 1 if chord == 1 else 2
    raise ValueError(f"unknown latency mode: {mode}")


def write_anynet(path: Path, n: int, edges: list[tuple[int, int]], mode: str) -> dict:
    directed: dict[int, list[tuple[int, int]]] = {i: [] for i in range(n)}
    weighted_cost = 0.0
    for u, v in edges:
        lat = edge_latency(n, u, v, mode)
        directed[u].append((v, lat))
        directed[v].append((u, lat))
        weighted_cost += 1.0 / lat

    lines = []
    for router in range(n):
        parts = [f"router {router}", f"node {router}"]
        for neighbor, lat in sorted(directed[router]):
            parts.append(f"router {neighbor} {lat}")
        lines.append(" ".join(parts))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    metrics = graph_metrics(n, edges)
    metrics["latency_mode"] = mode
    metrics["hardware_cost_links"] = metrics["undirected_links"]
    metrics["hardware_cost_bandwidth"] = weighted_cost
    return metrics


def define_topologies() -> list[dict]:
    specs = [
        {
            "name": "k8_complete",
            "family": "complete",
            "n": 8,
            "generators": None,
            "latency_mode": "uniform",
            "description": "Полносвязный кластер из 8 узлов (эталон Boardfly-группы)",
        },
        {
            "name": "c8_1_2",
            "family": "circulant",
            "n": 8,
            "generators": [1, 2],
            "latency_mode": "uniform",
            "description": "Циркулянт C(8; 1,2), степень 4",
        },
        {
            "name": "c8_1_4",
            "family": "circulant",
            "n": 8,
            "generators": [1, 4],
            "latency_mode": "uniform",
            "description": "Циркулянт C(8; 1,4), степень 3",
        },
        {
            "name": "c8_1_2_hetero",
            "family": "circulant",
            "n": 8,
            "generators": [1, 2],
            "latency_mode": "hetero",
            "description": "C(8; 1,2) с неоднородными каналами: хорда 1 быстрее хорды 2",
        },
        {
            "name": "k16_complete",
            "family": "complete",
            "n": 16,
            "generators": None,
            "latency_mode": "uniform",
            "description": "Полносвязный кластер из 16 узлов",
        },
        {
            "name": "c16_1_4",
            "family": "circulant",
            "n": 16,
            "generators": [1, 4],
            "latency_mode": "uniform",
            "description": "Циркулянт C(16; 1,4), степень 4",
        },
        {
            "name": "c16_1_2_4",
            "family": "circulant",
            "n": 16,
            "generators": [1, 2, 4],
            "latency_mode": "uniform",
            "description": "Циркулянт C(16; 1,2,4), степень 6",
        },
        {
            "name": "c16_1_2_4_hetero",
            "family": "circulant",
            "n": 16,
            "generators": [1, 2, 4],
            "latency_mode": "hetero",
            "description": "C(16; 1,2,4) с неоднородными каналами",
        },
    ]
    return specs


def generate() -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog = []
    for spec in define_topologies():
        n = spec["n"]
        if spec["family"] == "complete":
            edges = complete_undirected_edges(n)
        else:
            edges = circulant_undirected_edges(n, spec["generators"])
        anynet_path = OUT_DIR / f"{spec['name']}.anynet"
        metrics = write_anynet(anynet_path, n, edges, spec["latency_mode"])
        record = {
            **spec,
            **metrics,
            "anynet_file": str(anynet_path.relative_to(ROOT)),
        }
        catalog.append(record)
        print(
            f"{record['name']:22} n={n:2} deg={record['degree']:2} "
            f"links={record['undirected_links']:3} diam={record['diameter']} "
            f"asp={record['average_shortest_path']:.3f} "
            f"cost={record['hardware_cost_bandwidth']:.1f} "
            f"mode={record['latency_mode']}"
        )
    catalog_path = OUT_DIR / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return catalog


def main() -> None:
    catalog = generate()
    # Самопроверка: K_n имеет n(n-1)/2 рёбер, диаметр 1.
    for rec in catalog:
        n = rec["n"]
        if rec["family"] == "complete":
            expected = n * (n - 1) // 2
            if rec["undirected_links"] != expected or rec["diameter"] != 1:
                raise SystemExit(f"complete-graph check failed for {rec['name']}")
        if rec["family"] == "circulant":
            gens = _canonical_generators(n, rec["generators"])
            expected_deg = sum(1 if g == n // 2 and n % 2 == 0 else 2 for g in gens)
            if rec["degree"] != expected_deg:
                raise SystemExit(
                    f"circulant degree check failed for {rec['name']}: "
                    f"{rec['degree']} != {expected_deg}"
                )
    print(f"Wrote {len(catalog)} topologies to {OUT_DIR}")


if __name__ == "__main__":
    main()

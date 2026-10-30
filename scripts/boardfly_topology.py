#!/usr/bin/env python3
"""Boardfly 4×8×36 and the 8×8×16 torus Google compares it against.

Реконструкция публичной иерархии TPU 8i:
  building block = 4 полностью связанных чипа;
  group         = 8 блоков, полносвязных по меди;
  pod           = 36 групп, полносвязных через OCS.

Назначение портов — реализация, согласованная с опубликованными счётчиками
(16 внешних линков блока, 11 меди, 5 оптики) и воспроизводящая диаметр 7.
Канонический граф (1 медный канал на пару блоков) совпадает с открытой
реконструкцией Shiftfly: max degree 7, mean degree ≈ 5.84.
Вариант ports добавляет вторые chip-to-chip каналы, чтобы блок имел 11 меди.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "experiments" / "topologies"

BB_CHIPS = 4
GROUP_BBS = 8
GROUP_CHIPS = BB_CHIPS * GROUP_BBS  # 32
POD_GROUPS = 36
POD_CHIPS = GROUP_CHIPS * POD_GROUPS  # 1152
BB_EXTERNAL_LINKS = 16
BB_INTRA_GROUP_LINKS = 11
BB_OPTICAL_LINKS = BB_EXTERNAL_LINKS - BB_INTRA_GROUP_LINKS  # 5
GROUP_OPTICAL_PORTS = GROUP_BBS * BB_OPTICAL_LINKS  # 40

TORUS_DIMS = (8, 8, 16)
TORUS_CHIPS = 8 * 8 * 16  # 1024
TORUS_DIAMETER = 8 // 2 + 8 // 2 + 16 // 2  # 16


def chip_id(group: int, bb: int, c: int) -> int:
    return (group * GROUP_BBS + bb) * BB_CHIPS + c


def decode_chip(nid: int) -> tuple[int, int, int]:
    c = nid % BB_CHIPS
    rest = nid // BB_CHIPS
    bb = rest % GROUP_BBS
    group = rest // GROUP_BBS
    return group, bb, c


def _link(edges: set[tuple[int, int, str]], a: int, b: int, kind: str) -> None:
    if a == b:
        raise ValueError("self-loop")
    lo, hi = (a, b) if a < b else (b, a)
    edges.add((lo, hi, kind))


def boardfly_edges(pod_groups: int = POD_GROUPS, extra_copper: bool = False) -> list[tuple[int, int, str]]:
    edges: set[tuple[int, int, str]] = set()
    for g in range(pod_groups):
        for bb in range(GROUP_BBS):
            for a in range(BB_CHIPS):
                for b in range(a + 1, BB_CHIPS):
                    _link(edges, chip_id(g, bb, a), chip_id(g, bb, b), "board")
        for i in range(GROUP_BBS):
            for j in range(i + 1, GROUP_BBS):
                _link(
                    edges,
                    chip_id(g, i, j % BB_CHIPS),
                    chip_id(g, j, i % BB_CHIPS),
                    "copper",
                )
    for p in range(pod_groups):
        for q in range(p + 1, pod_groups):
            _link(
                edges,
                chip_id(p, q % GROUP_BBS, (q // GROUP_BBS) % BB_CHIPS),
                chip_id(q, p % GROUP_BBS, (p // GROUP_BBS) % BB_CHIPS),
                "optical",
            )
    if extra_copper:
        # Вторые chip-to-chip каналы по C(8;1,2): +4 меди на блок → 11.
        # Ставим после оптики и размазываем по наименее загруженным чипам.
        degree: dict[int, int] = {}
        existing: set[tuple[int, int]] = set()
        for u, v, _k in edges:
            existing.add((u, v))
            existing.add((v, u))
            degree[u] = degree.get(u, 0) + 1
            degree[v] = degree.get(v, 0) + 1
        for g in range(pod_groups):
            for i in range(GROUP_BBS):
                for j in range(i + 1, GROUP_BBS):
                    ring = min(j - i, GROUP_BBS - (j - i))
                    if ring not in (1, 2):
                        continue
                    best = None
                    best_key = None
                    for ci in range(BB_CHIPS):
                        for cj in range(BB_CHIPS):
                            a = chip_id(g, i, ci)
                            b = chip_id(g, j, cj)
                            if a == b or (a, b) in existing:
                                continue
                            key = (
                                degree.get(a, 0) + degree.get(b, 0),
                                max(degree.get(a, 0), degree.get(b, 0)),
                            )
                            if best_key is None or key < best_key:
                                best = (a, b)
                                best_key = key
                    if best is None:
                        raise RuntimeError(f"no free copper chip-pair between BB {i} and {j} in group {g}")
                    a, b = best
                    _link(edges, a, b, "copper")
                    existing.add((a, b))
                    existing.add((b, a))
                    degree[a] = degree.get(a, 0) + 1
                    degree[b] = degree.get(b, 0) + 1
    return sorted(edges)


def torus_edges(dims: tuple[int, ...] = TORUS_DIMS) -> list[tuple[int, int, str]]:
    sizes = list(dims)
    n = 1
    for k in sizes:
        n *= k
    strides, acc = [], 1
    for k in sizes:
        strides.append(acc)
        acc *= k

    def coords(i: int) -> list[int]:
        return [(i // s) % k for s, k in zip(strides, sizes)]

    def index(c: list[int]) -> int:
        return sum((ci % k) * s for ci, k, s in zip(c, sizes, strides))

    edges: set[tuple[int, int, str]] = set()
    for i in range(n):
        c = coords(i)
        for axis, k in enumerate(sizes):
            if k < 2:
                continue
            cc = list(c)
            cc[axis] = (cc[axis] + 1) % k
            j = index(cc)
            _link(edges, i, j, "torus")
    return sorted(edges)


def adjacency(n: int, edges: list[tuple[int, int, str]]) -> list[list[int]]:
    adj: list[list[int]] = [[] for _ in range(n)]
    for u, v, _kind in edges:
        adj[u].append(v)
        adj[v].append(u)
    return adj


def bfs_from(adj: list[list[int]], src: int) -> list[int]:
    n = len(adj)
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
        missing = dist.count(-1)
        raise ValueError(f"graph disconnected from {src}: {missing} unreachable")
    return dist


def analyze(n: int, edges: list[tuple[int, int, str]], sample_sources: int | None = None) -> dict:
    adj = adjacency(n, edges)
    degrees = [len(a) for a in adj]
    kinds = Counter(kind for _u, _v, kind in edges)
    sources = range(n) if sample_sources is None else range(0, n, max(1, n // sample_sources))
    hop_hist: Counter[int] = Counter()
    ecc: list[int] = []
    hop_sum = 0
    hop_pairs = 0
    for src in sources:
        dist = bfs_from(adj, src)
        ecc.append(max(dist))
        for d in dist:
            if d > 0:
                hop_hist[d] += 1
                hop_sum += d
                hop_pairs += 1
    intra_bb = intra_group = inter_group = 0
    intra_bb_h = intra_group_h = inter_group_h = 0
    if n == POD_CHIPS:
        # Считаем hops по классам пар на полной выборке источников.
        for src in sources:
            sg, sb, _sc = decode_chip(src)
            dist = bfs_from(adj, src)
            for dst, d in enumerate(dist):
                if d <= 0:
                    continue
                dg, db, _dc = decode_chip(dst)
                if sg == dg and sb == db:
                    intra_bb += 1
                    intra_bb_h += d
                elif sg == dg:
                    intra_group += 1
                    intra_group_h += d
                else:
                    inter_group += 1
                    inter_group_h += d
    return {
        "n": n,
        "undirected_links": len(edges),
        "directed_channels": 2 * len(edges),
        "min_degree": min(degrees),
        "max_degree": max(degrees),
        "mean_degree": sum(degrees) / n,
        "links_by_kind": dict(kinds),
        "diameter": max(ecc),
        "average_shortest_path": hop_sum / hop_pairs if hop_pairs else 0.0,
        "hop_histogram": {str(k): hop_hist[k] for k in sorted(hop_hist)},
        "intra_bb_asp": (intra_bb_h / intra_bb) if intra_bb else None,
        "intra_group_asp": (intra_group_h / intra_group) if intra_group else None,
        "inter_group_asp": (inter_group_h / inter_group) if inter_group else None,
    }


def copper_ports_per_bb(edges: list[tuple[int, int, str]], pod_groups: int = POD_GROUPS) -> dict[int, int]:
    counts = [0] * (pod_groups * GROUP_BBS)
    for u, v, kind in edges:
        if kind != "copper":
            continue
        gu, bu, _ = decode_chip(u)
        gv, bv, _ = decode_chip(v)
        counts[gu * GROUP_BBS + bu] += 1
        counts[gv * GROUP_BBS + bv] += 1
    return {
        "min": min(counts),
        "max": max(counts),
        "mean": sum(counts) / len(counts),
    }


def write_anynet(path: Path, n: int, edges: list[tuple[int, int, str]], latency: dict[str, int] | None = None) -> None:
    lat = latency or {"board": 1, "copper": 1, "optical": 1, "torus": 1}
    neigh: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for u, v, kind in edges:
        w = lat.get(kind, 1)
        neigh[u].append((v, w))
        neigh[v].append((u, w))
    lines = []
    for router in range(n):
        parts = [f"router {router}", f"node {router}"]
        for other, w in sorted(neigh[router]):
            parts.append(f"router {other} {w}")
        lines.append(" ".join(parts))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate() -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog: list[dict] = []

    canonical_edges = boardfly_edges(extra_copper=False)
    ports_edges = boardfly_edges(extra_copper=True)
    torus = torus_edges()

    specs = [
        {
            "name": "boardfly_4x8x36",
            "family": "boardfly",
            "description": "Канонический Boardfly: K4 блоков, K8 групп по меди, K36 через OCS",
            "n": POD_CHIPS,
            "edges": canonical_edges,
            "google_diameter": 7,
            "google_max_degree": 7,
        },
        {
            "name": "boardfly_4x8x36_ports",
            "family": "boardfly",
            "description": "Boardfly с 11 медными линками на блок (вторая chip-пара между соседями)",
            "n": POD_CHIPS,
            "edges": ports_edges,
            "google_diameter": 7,
            "google_max_degree": 7,
        },
        {
            "name": "torus_8x8x16",
            "family": "torus",
            "description": "3D-тор 8×8×16 (1024 чипа) — эталон диаметра 16 у Google",
            "n": TORUS_CHIPS,
            "edges": torus,
            "google_diameter": TORUS_DIAMETER,
            "google_max_degree": 6,
        },
    ]

    for spec in specs:
        metrics = analyze(spec["n"], spec["edges"])
        if spec["family"] == "boardfly":
            metrics["copper_ports_per_bb"] = copper_ports_per_bb(spec["edges"])
        anynet = OUT_DIR / f"{spec['name']}.anynet"
        write_anynet(anynet, spec["n"], spec["edges"])
        record = {
            "name": spec["name"],
            "family": spec["family"],
            "description": spec["description"],
            "hierarchy": (
                {"chips_per_block": BB_CHIPS, "blocks_per_group": GROUP_BBS, "groups": POD_GROUPS}
                if spec["family"] == "boardfly"
                else {"dims": list(TORUS_DIMS)}
            ),
            "google_diameter": spec["google_diameter"],
            "google_max_degree": spec["google_max_degree"],
            "anynet_file": str(anynet.relative_to(ROOT)),
            **metrics,
        }
        catalog.append(record)
        print(
            f"{record['name']:28} n={record['n']:4} "
            f"deg={record['min_degree']}-{record['max_degree']} "
            f"mean={record['mean_degree']:.3f} "
            f"diam={record['diameter']} asp={record['average_shortest_path']:.3f} "
            f"links={record['undirected_links']}"
        )
        if record["diameter"] != spec["google_diameter"]:
            raise SystemExit(
                f"{spec['name']}: diameter {record['diameter']} != Google {spec['google_diameter']}"
            )

    # Инварианты канонического Boardfly (открытая реконструкция).
    canon = catalog[0]
    if canon["max_degree"] != 7:
        raise SystemExit(f"canonical max degree {canon['max_degree']} != 7")
    if abs(canon["mean_degree"] - 5.84) > 0.02:
        raise SystemExit(f"canonical mean degree {canon['mean_degree']} != 5.84")
    ports = catalog[1]
    copper = ports["copper_ports_per_bb"]
    if copper["min"] != BB_INTRA_GROUP_LINKS or copper["max"] != BB_INTRA_GROUP_LINKS:
        raise SystemExit(f"ports variant copper per BB {copper}, expected {BB_INTRA_GROUP_LINKS}")

    torus_rec = catalog[2]
    if torus_rec["max_degree"] != 6:
        raise SystemExit(f"torus degree {torus_rec['max_degree']} != 6")

    asp_b = canon["average_shortest_path"]
    asp_t = torus_rec["average_shortest_path"]
    hop_ratio = asp_b / asp_t
    google = {
        "source": "Google Cloud Blog, 22 Apr 2026, TPU 8t/8i technical deep dive",
        "chips_connected": POD_CHIPS,
        "chips_active": 1024,
        "hierarchy": "4 TPU × 8 blocks × 36 groups",
        "diameter": 7,
        "torus_reference": "8×8×16, diameter 16",
        "diameter_reduction": "56%",
        "all_to_all_latency_improvement": "up to 50%",
        "ici_bandwidth_tbps": 19.2,
        "model_hop_ratio_vs_torus": hop_ratio,
        "model_latency_cut_vs_torus_pct": 100.0 * (1.0 - hop_ratio),
    }
    out = {
        "google": google,
        "topologies": catalog,
    }
    path = OUT_DIR / "boardfly_catalog.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"hop ratio Boardfly/torus = {hop_ratio:.3f}  (цель Google ≈ 0.50 по задержке)")
    print(f"Wrote {path}")
    return catalog


if __name__ == "__main__":
    generate()

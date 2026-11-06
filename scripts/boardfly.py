#!/usr/bin/env python3
"""Boardfly 4×8×36: граф → anynet → BookSim."""

from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOKSIM = ROOT / "booksim2" / "src" / "booksim"
OUT = ROOT / "experiments"

BB, GROUP_BBS, GROUPS = 4, 8, 36
N = BB * GROUP_BBS * GROUPS  # 1152
INJECTION_RATE = 0.02

CFG = """\
topology = anynet;
routing_function = min;
traffic = uniform;
use_read_write = 0;
sim_type = latency;
warmup_periods = 1;
sample_period = 400;
max_samples = 3;
sim_count = 1;
seed = 1;
num_vcs = 16;
vc_buf_size = 8;
wait_for_tail_credit = 0;
vc_allocator = islip;
sw_allocator = islip;
alloc_iters = 2;
credit_delay = 1;
routing_delay = 0;
vc_alloc_delay = 1;
sw_alloc_delay = 1;
st_final_delay = 1;
input_speedup = 1;
output_speedup = 1;
internal_speedup = 1.0;
packet_size = 1;
priority = none;
print_csv_results = 1;
deadlock_warn_timeout = 4096;
latency_thres = 2000.0;
include_queuing = 1;
pair_stats = 0;
network_file = {network_file};
injection_rate = {injection_rate};
"""


def chip(group: int, bb: int, c: int) -> int:
    return (group * GROUP_BBS + bb) * BB + c


def boardfly_edges() -> set[tuple[int, int]]:
    edges: set[tuple[int, int]] = set()

    def link(a: int, b: int) -> None:
        if a == b:
            raise ValueError("self-loop")
        edges.add((a, b) if a < b else (b, a))

    for g in range(GROUPS):
        for bb in range(GROUP_BBS):
            for a in range(BB):
                for b in range(a + 1, BB):
                    link(chip(g, bb, a), chip(g, bb, b))
        for i in range(GROUP_BBS):
            for j in range(i + 1, GROUP_BBS):
                link(chip(g, i, j % BB), chip(g, j, i % BB))
    for p in range(GROUPS):
        for q in range(p + 1, GROUPS):
            link(
                chip(p, q % GROUP_BBS, (q // GROUP_BBS) % BB),
                chip(q, p % GROUP_BBS, (p // GROUP_BBS) % BB),
            )
    return edges


def metrics(n: int, edges: set[tuple[int, int]]) -> dict:
    adj: list[list[int]] = [[] for _ in range(n)]
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    degrees = [len(row) for row in adj]
    hop_sum = hop_pairs = diameter = 0
    for src in range(n):
        dist = [-1] * n
        dist[src] = 0
        q: deque[int] = deque([src])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if dist[v] < 0:
                    dist[v] = dist[u] + 1
                    q.append(v)
        if min(dist) < 0:
            raise SystemExit(f"граф несвязный от {src}")
        diameter = max(diameter, max(dist))
        for d in dist:
            if d > 0:
                hop_sum += d
                hop_pairs += 1
    return {
        "n": n,
        "links": len(edges),
        "min_degree": min(degrees),
        "max_degree": max(degrees),
        "mean_degree": sum(degrees) / n,
        "diameter": diameter,
        "asp": hop_sum / hop_pairs,
    }


def write_anynet(path: Path, n: int, edges: set[tuple[int, int]]) -> None:
    neigh: list[list[int]] = [[] for _ in range(n)]
    for u, v in edges:
        neigh[u].append(v)
        neigh[v].append(u)
    lines = []
    for router in range(n):
        parts = [f"router {router}", f"node {router}"]
        for other in sorted(neigh[router]):
            parts.append(f"router {other} 1")
        lines.append(" ".join(parts))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_stats(output: str) -> dict:
    marker = "====== Overall Traffic Statistics ======"
    text = output.split(marker, 1)[1] if marker in output else output

    def grab(label: str) -> float | None:
        match = re.search(rf"{re.escape(label)}\s*=\s*([0-9.eE+-]+)", text)
        return float(match.group(1)) if match else None

    return {
        "latency": grab("Packet latency average"),
        "hops": grab("Hops average"),
        "accepted": grab("Accepted flit rate average"),
        "completed": marker in output,
    }


def main() -> None:
    if not BOOKSIM.exists():
        raise SystemExit(f"Нет {BOOKSIM}. Соберите: make booksim")

    graph = boardfly_edges()
    m = metrics(N, graph)
    print(
        f"n={m['n']} deg={m['min_degree']}-{m['max_degree']} "
        f"mean={m['mean_degree']:.3f} diam={m['diameter']} "
        f"asp={m['asp']:.3f} links={m['links']}",
        flush=True,
    )
    if m["diameter"] != 7 or m["max_degree"] != 7 or abs(m["mean_degree"] - 5.84) > 0.02:
        raise SystemExit("инварианты Boardfly не сошлись (ожидались diam=7, maxdeg=7, mean≈5.84)")

    OUT.mkdir(parents=True, exist_ok=True)
    anynet = OUT / "boardfly.anynet"
    write_anynet(anynet, N, graph)

    cfg = OUT / "booksim.cfg"
    cfg.write_text(
        CFG.format(network_file=anynet, injection_rate=INJECTION_RATE),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [str(BOOKSIM), str(cfg)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    log = OUT / "booksim.log"
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    log.write_text(output, encoding="utf-8")
    stats = parse_stats(output)
    if not stats["completed"] or stats["latency"] is None:
        raise SystemExit(f"BookSim не завершился, см. {log}")
    print(
        f"inj={INJECTION_RATE:.2f} latency={stats['latency']:.3f} "
        f"hops={stats['hops']:.3f} accepted={stats['accepted']:.3f}",
        flush=True,
    )


if __name__ == "__main__":
    main()

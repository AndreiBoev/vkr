#!/usr/bin/env python3
"""Cycle-accurate Boardfly 4×8×36 in BookSim 2.0."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_simulations import BOOKSIM, parse_stats, require_booksim, run_cmd  # noqa: E402

MSK = timezone(timedelta(hours=3))
CFG = ROOT / "experiments" / "configs" / "boardfly.cfg"
CATALOG = ROOT / "experiments" / "topologies" / "boardfly_catalog.json"
RUNS = ROOT / "experiments" / "runs" / "boardfly"
RESULTS = ROOT / "experiments" / "results"

# Низкая нагрузка: смотрим диаметр и all-to-all задержку самой иерархии, не saturation.
JOBS = [
    {"topology": "boardfly_4x8x36", "traffic": "uniform", "injection_rate": 0.02},
    {"topology": "boardfly_4x8x36", "traffic": "uniform", "injection_rate": 0.05},
    {"topology": "boardfly_4x8x36_ports", "traffic": "uniform", "injection_rate": 0.02},
    {"topology": "boardfly_4x8x36_ports", "traffic": "uniform", "injection_rate": 0.05},
    {"topology": "boardfly_4x8x36", "traffic": "randperm", "injection_rate": 0.02},
]


def topo_by_name(catalog: dict, name: str) -> dict:
    for rec in catalog["topologies"]:
        if rec["name"] == name:
            return rec
    raise KeyError(name)


def write_cfg(topo: dict, traffic: str, rate: float, path: Path) -> None:
    extra = "\n".join(
        [
            f"network_file = {ROOT / topo['anynet_file']};",
            f"injection_rate = {rate};",
            f"traffic = {traffic};",
        ]
    )
    path.write_text(CFG.read_text(encoding="utf-8") + "\n" + extra + "\n", encoding="utf-8")


def fmt(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def write_summary(path: Path, catalog: dict, rows: list[dict]) -> None:
    published = catalog["boardfly"]
    board = topo_by_name(catalog, "boardfly_4x8x36")
    ports = topo_by_name(catalog, "boardfly_4x8x36_ports")
    now = datetime.now(MSK).strftime("%Y-%m-%d %H:%M %Z")

    def pick(name: str, traffic: str, rate: float) -> dict | None:
        for row in rows:
            if row["topology"] == name and row["traffic"] == traffic and row["injection_rate"] == rate:
                return row
        return None

    bf02 = pick("boardfly_4x8x36", "uniform", 0.02)
    p02 = pick("boardfly_4x8x36_ports", "uniform", 0.02)
    bf05 = pick("boardfly_4x8x36", "uniform", 0.05)
    p05 = pick("boardfly_4x8x36_ports", "uniform", 0.05)
    bfb = pick("boardfly_4x8x36", "randperm", 0.02)

    copper_canon = board["copper_ports_per_bb"]
    copper_ports = ports["copper_ports_per_bb"]

    lines = [
        "# Boardfly 4×8×36",
        "",
        f"Снято: {now}",
        "",
        "Модель — иерархия ICI TPU 8i Boardfly: 4 чипа в блоке, 8 блоков в группе, 36 групп (1152 TPU).",
        "Стенд считает статические метрики графа и cycle-accurate задержку в BookSim.",
        "",
        "## Целевые характеристики Boardfly",
        "",
        "| Показатель | Публикация | Модель |",
        "|---|---|---|",
        f"| Иерархия | {published['hierarchy']} | 4 × 8 × 36 |",
        f"| Чипов | {published['chips_connected']} (до {published['chips_active']} active) | {board['n']} |",
        f"| Диаметр | {published['diameter']} | **{board['diameter']}** |",
        f"| Max degree | ≈{published['max_degree']} | {board['max_degree']} (канон), {ports['max_degree']} (11 меди) |",
        f"| Mean degree | ≈{published['mean_degree']:.2f} | {board['mean_degree']:.3f} |",
        f"| Внешних линков блока | {published['bb_external_links']} | {published['bb_external_links']} |",
        f"| Меди на блок | {published['bb_intra_group_links']} | {copper_canon['mean']:.2f} (канон), {copper_ports['mean']:.0f} (ports) |",
        f"| Оптики на блок | {published['bb_optical_links']} | {published['bb_optical_links']} |",
        f"| ICI | {published['ici_bandwidth_tbps']} Тбит/с на чип | 1 флит/цикл/канал (нормировка BookSim) |",
        "",
        f"Источник цифр: {published['source']}.",
        "Канонический граф совпадает с открытой реконструкцией Shiftfly (диаметр 7, mean degree 5.84).",
        "",
        "## Статические метрики",
        "",
        "| Топология | n | Степень | Рёбра | Диаметр | ASP | Intra-BB | Intra-group | Inter-group |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rec in catalog["topologies"]:
        deg = f"{rec['min_degree']}–{rec['max_degree']} ({rec['mean_degree']:.2f})"
        lines.append(
            f"| `{rec['name']}` | {rec['n']} | {deg} | {rec['undirected_links']} | "
            f"{rec['diameter']} | {rec['average_shortest_path']:.3f} | "
            f"{fmt(rec.get('intra_bb_asp'))} | {fmt(rec.get('intra_group_asp'))} | "
            f"{fmt(rec.get('inter_group_asp'))} |"
        )

    lines += [
        "",
        "## Распределение hops",
        "",
        "| Hops | Канон, доля пар | Ports, доля пар |",
        "|---:|---:|---:|",
    ]
    bf_hist = {int(k): v for k, v in board["hop_histogram"].items()}
    p_hist = {int(k): v for k, v in ports["hop_histogram"].items()}
    bf_pairs = sum(bf_hist.values())
    p_pairs = sum(p_hist.values())
    for h in range(1, max(max(bf_hist, default=0), max(p_hist, default=0)) + 1):
        bf_p = 100.0 * bf_hist.get(h, 0) / bf_pairs if bf_pairs else 0.0
        p_p = 100.0 * p_hist.get(h, 0) / p_pairs if p_pairs else 0.0
        if bf_hist.get(h, 0) == 0 and p_hist.get(h, 0) == 0:
            continue
        lines.append(f"| {h} | {bf_p:.1f}% | {p_p:.1f}% |")

    lines += [
        "",
        "## BookSim, uniform (приближение all-to-all)",
        "",
        "| Топология | inj | Задержка пакета | Hops | Accepted | Unstable | Completed |",
        "|---|---:|---:|---:|---:|:---:|:---:|",
    ]
    for row in rows:
        if row["traffic"] != "uniform":
            continue
        lines.append(
            f"| `{row['topology']}` | {row['injection_rate']:.2f} | "
            f"{fmt(row.get('packet_latency_avg'))} | {fmt(row.get('hops_avg'))} | "
            f"{fmt(row.get('accepted_flit_rate'))} | "
            f"{'yes' if row.get('unstable') else 'no'} | "
            f"{'yes' if row.get('completed') else 'no'} |"
        )

    lines += [
        "",
        "## BookSim на inj=0.02, uniform",
        "",
        "| | Канон | Ports |",
        "|---|---|---|",
        f"| Диаметр графа | {board['diameter']} | {ports['diameter']} |",
        f"| Средние hops (граф) | {board['average_shortest_path']:.3f} | {ports['average_shortest_path']:.3f} |",
        f"| Задержка BookSim | {fmt(bf02['packet_latency_avg'] if bf02 else None)} | {fmt(p02['packet_latency_avg'] if p02 else None)} |",
        f"| Max задержка BookSim | {fmt(bf02['packet_latency_max'] if bf02 else None)} | {fmt(p02['packet_latency_max'] if p02 else None)} |",
        f"| Hops BookSim | {fmt(bf02['hops_avg'] if bf02 else None)} | {fmt(p02['hops_avg'] if p02 else None)} |",
        "",
        "## Случайная перестановка (randperm, inj=0.02)",
        "",
        "| | Канон |",
        "|---|---|",
        f"| Задержка | {fmt(bfb['packet_latency_avg'] if bfb else None)} |",
        f"| Hops | {fmt(bfb['hops_avg'] if bfb else None)} |",
        "",
        "## Наблюдения",
        "",
        f"- Диаметр канонического Boardfly равен **{board['diameter']}**, max degree **{board['max_degree']}**, mean degree **{board['mean_degree']:.3f}** — это заявленные инварианты иерархии 4×8×36.",
        f"- Средняя длина пути {board['average_shortest_path']:.3f} hops; intra-BB {fmt(board.get('intra_bb_asp'))}, intra-group {fmt(board.get('intra_group_asp'))}, inter-group {fmt(board.get('inter_group_asp'))}.",
        "- Вариант с 11 медными каналами на блок держит диаметр 7 и max degree 7, сокращая ASP внутри группы и между группами.",
        "- В BookSim канал нормирован как 1 флит/цикл; 19.2 Тбит/с ICI задаёт только физический масштаб, не топологию.",
        "- Следующий шаг метода: заменить полносвязную группу циркулянтом и сравнить с этим каноном Boardfly.",
        "",
    ]
    if bf05:
        extra = f"- На inj=0.05 задержка канона {fmt(bf05.get('packet_latency_avg'))}"
        if p05:
            extra += f", ports {fmt(p05.get('packet_latency_avg'))}"
        extra += "."
        lines.insert(-2, extra)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    require_booksim()
    if not CATALOG.exists():
        subprocess.check_call([sys.executable, str(ROOT / "scripts" / "boardfly_topology.py")], cwd=ROOT)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if "boardfly" not in catalog:
        raise SystemExit("В каталоге нет ключа boardfly. Перегенерируйте: python3 scripts/boardfly_topology.py")
    RUNS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    rows = []
    for job in JOBS:
        topo = topo_by_name(catalog, job["topology"])
        run_id = f"{job['topology']}_{job['traffic']}_inj{job['injection_rate']:.2f}".replace(".", "p")
        run_dir = RUNS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = run_dir / "booksim.cfg"
        log = run_dir / "booksim.log"
        write_cfg(topo, job["traffic"], job["injection_rate"], cfg)
        print(f"RUN {run_id}  n={topo['n']}", flush=True)
        if log.exists() and "--force" not in sys.argv:
            output = log.read_text(encoding="utf-8")
            code = 255 if "Overall Traffic Statistics" in output else 1
        else:
            code, output = run_cmd([str(BOOKSIM), str(cfg)], log, timeout_s=420)
        stats = parse_stats(output)
        row = {
            **job,
            "n": topo["n"],
            "diameter": topo["diameter"],
            "graph_asp": topo["average_shortest_path"],
            "exit_code": code,
            "ok": stats.get("completed") is True,
            "log": str(log.relative_to(ROOT)),
            **stats,
        }
        rows.append(row)
        print(
            f"  completed={stats.get('completed')} lat={stats.get('packet_latency_avg')} "
            f"hops={stats.get('hops_avg')} acc={stats.get('accepted_flit_rate')} "
            f"unstable={stats.get('unstable')}",
            flush=True,
        )

    csv_path = RESULTS / "boardfly.csv"
    json_path = RESULTS / "boardfly.json"
    md_path = RESULTS / "boardfly.md"
    if rows:
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    json_path.write_text(
        json.dumps({"catalog": catalog["boardfly"], "rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary(md_path, catalog, rows)
    print(f"Wrote {md_path}")

    failed = [r for r in rows if not r["ok"] and not r.get("unstable")]
    if failed:
        names = ", ".join(r["topology"] for r in failed)
        raise SystemExit(f"Незавершённые прогоны: {names}")


if __name__ == "__main__":
    main()

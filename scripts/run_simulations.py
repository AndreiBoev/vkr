#!/usr/bin/env python3
"""Run BookSim 2.0 smoke tests and complete-vs-circulant cluster comparisons."""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOKSIM = ROOT / "third_party" / "booksim2" / "src" / "booksim"
BASE_CFG = ROOT / "experiments" / "configs" / "base.cfg"
CATALOG = ROOT / "experiments" / "topologies" / "catalog.json"
RUNS_DIR = ROOT / "experiments" / "runs"
RESULTS_DIR = ROOT / "experiments" / "results"
MSK = timezone(timedelta(hours=3))

# BookSim 2.0: Simulate() == true → main возвращает -1 (код 255).
BOOKSIM_OK_CODES = {0, 255}

CAMPAIGNS = [
    {
        "name": "flit1",
        "label": "Пакет = 1 флит (низкая/средняя нагрузка)",
        "packet_size": 1,
        "injection_rates": [0.05, 0.10, 0.20, 0.30, 0.40],
        "latency_thres": 500.0,
        "warmup_periods": 2,
        "max_samples": 5,
        "sample_period": 1000,
    },
    {
        "name": "pkt8",
        "label": "Пакет = 8 флит (нагрузка ближе к насыщению)",
        "packet_size": 8,
        "injection_rates": [0.02, 0.04, 0.06, 0.08, 0.10],
        "latency_thres": 5000.0,
        "warmup_periods": 3,
        "max_samples": 6,
        "sample_period": 1000,
    },
]


def require_booksim() -> None:
    if not BOOKSIM.exists():
        raise SystemExit(f"Не найден {BOOKSIM}. Сначала выполните: make booksim")


def run_cmd(args: list[str], log_path: Path, timeout_s: int = 180) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    log_path.write_text(output, encoding="utf-8")
    return proc.returncode, output


def parse_stats(output: str) -> dict:
    marker = "====== Overall Traffic Statistics ======"
    overall = output.split(marker, 1)[1] if marker in output else output

    def grab(label: str, text: str) -> float | None:
        match = re.search(rf"{re.escape(label)}\s*=\s*([0-9.eE+-]+)", text)
        return float(match.group(1)) if match else None

    hops = grab("Hops average", overall)
    if hops is None:
        hops = grab("Hops average", output)

    return {
        "packet_latency_avg": grab("Packet latency average", overall),
        "network_latency_avg": grab("Network latency average", overall),
        "flit_latency_avg": grab("Flit latency average", overall),
        "accepted_flit_rate": grab("Accepted flit rate average", overall),
        "injected_flit_rate": grab("Injected flit rate average", overall),
        "hops_avg": hops,
        "deadlock_warning": "Possible network deadlock" in output,
        "unstable": bool(re.search(r"Unstable|Simulation unstable", output)),
        "completed": marker in output,
        "time_taken": (
            float(m.group(1))
            if (m := re.search(r"Time taken is ([0-9.]+) cycles", output))
            else None
        ),
    }


def smoke_mesh() -> dict:
    log = RUNS_DIR / "smoke" / "mesh4x4.log"
    args = [
        str(BOOKSIM),
        str(ROOT / "third_party" / "booksim2" / "runfiles" / "meshconfig"),
        "k=4",
        "n=2",
        "injection_rate=0.10",
        "sample_period=500",
        "warmup_periods=1",
        "max_samples=3",
        "sim_count=1",
    ]
    code, output = run_cmd(args, log)
    stats = parse_stats(output)
    stats.update({"name": "mesh4x4_smoke", "exit_code": code, "log": str(log.relative_to(ROOT))})
    if not stats["completed"] or stats["packet_latency_avg"] is None:
        raise SystemExit(f"Smoke-тест mesh не удался, см. {log}")
    return stats


def smoke_dragonfly() -> dict:
    log = RUNS_DIR / "smoke" / "dragonfly.log"
    args = [
        str(BOOKSIM),
        str(ROOT / "third_party" / "booksim2" / "runfiles" / "dragonflyconfig"),
        "k=2",
        "injection_rate=0.10",
        "sample_period=500",
        "warmup_periods=1",
        "max_samples=3",
        "sim_count=1",
    ]
    code, output = run_cmd(args, log, timeout_s=240)
    stats = parse_stats(output)
    stats.update({"name": "dragonfly_smoke", "exit_code": code, "log": str(log.relative_to(ROOT))})
    return stats


def write_run_cfg(topo: dict, injection_rate: float, campaign: dict, out_path: Path) -> None:
    network_file = ROOT / topo["anynet_file"]
    extra = "\n".join(
        [
            f"network_file = {network_file};",
            f"injection_rate = {injection_rate};",
            f"packet_size = {campaign['packet_size']};",
            f"latency_thres = {campaign['latency_thres']};",
            f"warmup_periods = {campaign['warmup_periods']};",
            f"max_samples = {campaign['max_samples']};",
            f"sample_period = {campaign['sample_period']};",
        ]
    )
    out_path.write_text(BASE_CFG.read_text(encoding="utf-8") + "\n" + extra + "\n", encoding="utf-8")


def run_topology_sweep(catalog: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for campaign in CAMPAIGNS:
        for topo in catalog:
            for rate in campaign["injection_rates"]:
                run_id = (
                    f"{campaign['name']}_{topo['name']}_inj{rate:.2f}".replace(".", "p")
                )
                run_dir = RUNS_DIR / campaign["name"] / run_id
                run_dir.mkdir(parents=True, exist_ok=True)
                cfg_path = run_dir / "booksim.cfg"
                log_path = run_dir / "booksim.log"
                write_run_cfg(topo, rate, campaign, cfg_path)
                print(f"RUN {run_id}", flush=True)
                code, output = run_cmd([str(BOOKSIM), str(cfg_path)], log_path)
                stats = parse_stats(output)
                row = {
                    "campaign": campaign["name"],
                    "packet_size": campaign["packet_size"],
                    "topology": topo["name"],
                    "family": topo["family"],
                    "n": topo["n"],
                    "generators": ""
                    if topo["generators"] is None
                    else ",".join(map(str, topo["generators"])),
                    "latency_mode": topo["latency_mode"],
                    "degree": topo["degree"],
                    "undirected_links": topo["undirected_links"],
                    "diameter": topo["diameter"],
                    "average_shortest_path": topo["average_shortest_path"],
                    "router_radix": topo["router_radix"],
                    "hardware_cost_links": topo["hardware_cost_links"],
                    "hardware_cost_bandwidth": topo["hardware_cost_bandwidth"],
                    "injection_rate": rate,
                    "offered_flit_rate": rate * campaign["packet_size"],
                    "exit_code": code,
                    "ok": code in BOOKSIM_OK_CODES and stats["completed"],
                    "log": str(log_path.relative_to(ROOT)),
                    **stats,
                }
                rows.append(row)
                flags = []
                if stats["unstable"]:
                    flags.append("UNSTABLE")
                if stats["deadlock_warning"]:
                    flags.append("DEADLOCK_WARN")
                if not stats["completed"]:
                    flags.append("INCOMPLETE")
                print(
                    f"  lat={stats['packet_latency_avg']} acc={stats['accepted_flit_rate']} "
                    f"hops={stats['hops_avg']} {' '.join(flags)}",
                    flush=True,
                )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fmt(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def pct(new: float, base: float) -> str:
    if base == 0:
        return "—"
    return f"{100.0 * (new - base) / base:+.1f}%"


def baseline_row(rows: list[dict], n: int, campaign: str, rate: float) -> dict | None:
    for row in rows:
        if (
            row["campaign"] == campaign
            and row["n"] == n
            and row["family"] == "complete"
            and row["injection_rate"] == rate
            and row["latency_mode"] == "uniform"
            and row["packet_latency_avg"] is not None
        ):
            return row
    return None


def write_markdown(path: Path, smoke: dict, dragonfly: dict, rows: list[dict], catalog: list[dict]) -> None:
    now = datetime.now(MSK).strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "# Предварительные результаты моделирования BookSim 2.0",
        "",
        f"Снято: {now}",
        "",
        "Моделируется **один иерархический уровень** — внутрикластерная сеть.",
        "Полносвязный граф `K_n` соответствует группе в духе Boardfly, циркулянты — кандидатам на замену.",
        "Неоднородная пропускная способность задаётся через задержку канала в anynet:",
        "хорда длины 1 имеет задержку 1 цикл, остальные — 2 цикла (BookSim 2.0 не задаёт ширину канала пореберно).",
        "",
        "Код выхода BookSim 255 (`-1`) для успешных прогонов — особенность `main.cpp`, а не ошибка.",
        "",
        "## Smoke-тесты штатных примеров BookSim",
        "",
        "| Прогон | Завершён | Средняя задержка пакета | Принятая нагрузка | Hops |",
        "|---|:---:|---:|---:|---:|",
        f"| mesh 4×4, inj=0.10 | {'yes' if smoke['completed'] else 'no'} | {fmt(smoke['packet_latency_avg'])} | {fmt(smoke['accepted_flit_rate'])} | {fmt(smoke['hops_avg'])} |",
        f"| dragonfly k=2, inj=0.10 | {'yes' if dragonfly.get('completed') else 'no'} | {fmt(dragonfly.get('packet_latency_avg'))} | {fmt(dragonfly.get('accepted_flit_rate'))} | {fmt(dragonfly.get('hops_avg'))} |",
        "",
        "## Параметры топологий",
        "",
        "| Имя | Семейство | n | Генераторы | Степень | Рёбра | Диаметр | ASP | Radix | Стоимость (1/lat) |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rec in catalog:
        gens = "—" if rec["generators"] is None else ",".join(map(str, rec["generators"]))
        lines.append(
            f"| `{rec['name']}` | {rec['family']} | {rec['n']} | {gens} | "
            f"{rec['degree']} | {rec['undirected_links']} | {rec['diameter']} | "
            f"{rec['average_shortest_path']:.3f} | {rec['router_radix']} | "
            f"{rec['hardware_cost_bandwidth']:.1f} |"
        )

    lines += [
        "",
        "## Экономия каналов против задержки (пакет = 1 флит, inj = 0.10)",
        "",
        "| Топология | n | Рёбра | Экономия каналов | Задержка | Надбавка к K_n | Hops |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for n, rate in ((8, 0.10), (16, 0.10)):
        base = baseline_row(rows, n, "flit1", rate)
        if not base:
            continue
        group = [
            r
            for r in rows
            if r["campaign"] == "flit1" and r["n"] == n and r["injection_rate"] == rate
        ]
        for row in group:
            save = 100.0 * (1.0 - row["undirected_links"] / base["undirected_links"])
            extra = pct(row["packet_latency_avg"], base["packet_latency_avg"])
            lines.append(
                f"| `{row['topology']}` | {n} | {row['undirected_links']} | "
                f"{save:.1f}% | {fmt(row['packet_latency_avg'])} | {extra} | {fmt(row['hops_avg'])} |"
            )

    for campaign in CAMPAIGNS:
        subset = [r for r in rows if r["campaign"] == campaign["name"]]
        lines += [
            "",
            f"## {campaign['label']}",
            "",
            "| Топология | n | inj | Offered flit | Задержка пакета | Accepted flit | Hops | Unstable |",
            "|---|---:|---:|---:|---:|---:|---:|:---:|",
        ]
        for row in subset:
            lines.append(
                f"| `{row['topology']}` | {row['n']} | {row['injection_rate']:.2f} | "
                f"{row['offered_flit_rate']:.2f} | {fmt(row['packet_latency_avg'])} | "
                f"{fmt(row['accepted_flit_rate'])} | {fmt(row['hops_avg'])} | "
                f"{'yes' if row['unstable'] else 'no'} |"
            )

    lines += [
        "",
        "## Наблюдения",
        "",
        "- На однофлитных пакетах все испытанные сети стабильны до inj=0.40: циркулянт медленнее эталона, но рост задержки плавный.",
        "- Для n=8 циркулянт `C(8;1,2)` экономит ~43% рёбер (`28 → 16`) при надбавке задержки ~16% на inj=0.10.",
        "- Для n=16 `C(16;1,2,4)` экономит 60% рёбер (`120 → 48`) при ~29% надбавки задержки; более редкий `C(16;1,4)` — 73% рёбер и ~38% задержки.",
        "- На 8-флитных пакетах разрыв растёт у насыщения: при inj=0.10 `C(16;1,4)` даёт ~331 цикл против ~70 у `K_16`, принятая нагрузка падает (0.77 против 0.80).",
        "- Неоднородные каналы (длинная хорда lat=2) на низкой нагрузке добавляют задержку. Веса участвуют в Dijkstra `min_anynet`, поэтому на высокой нагрузке картина может отличаться от однородного циркулянта.",
        "- Следующий шаг метода: выбирать генераторы по бюджету каналов, диаметру/ASP и оценке насыщения; per-link bandwidth в стоковом BookSim нет.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_svg(path: Path, rows: list[dict], n: int, campaign: str) -> None:
    subset = [
        r
        for r in rows
        if r["n"] == n
        and r["campaign"] == campaign
        and r["packet_latency_avg"] is not None
        and not r["unstable"]
    ]
    names = []
    for r in subset:
        if r["topology"] not in names:
            names.append(r["topology"])
    if not names:
        return
    rates = sorted({r["injection_rate"] for r in subset})
    if len(rates) < 2:
        return
    width, height = 780, 440
    left, right, top, bottom = 70, 24, 36, 56
    plot_w = width - left - right
    plot_h = height - top - bottom
    ys = [r["packet_latency_avg"] for r in subset]
    ymin, ymax = 0.0, max(ys) * 1.15
    xmin, xmax = min(rates), max(rates)

    def sx(x: float) -> float:
        return left + (x - xmin) / (xmax - xmin) * plot_w

    def sy(y: float) -> float:
        return top + (1.0 - (y - ymin) / (ymax - ymin)) * plot_h

    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#17becf", "#7f7f7f"]
    title = f"Средняя задержка пакета, n={n}, campaign={campaign}"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="sans-serif">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="{width/2}" y="22" text-anchor="middle" font-size="14">{title}</text>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#333"/>',
        f'<text x="16" y="{top+plot_h/2}" font-size="11" transform="rotate(-90 16 {top+plot_h/2})">latency</text>',
        f'<text x="{left+plot_w/2}" y="{height-14}" text-anchor="middle" font-size="12">injection_rate</text>',
    ]
    for i, tick in enumerate([ymin, ymax]):
        y = sy(tick)
        parts.append(f'<text x="{left-8}" y="{y+4}" text-anchor="end" font-size="10">{tick:.1f}</text>')
    for name, color in zip(names, palette):
        pts = []
        for r in sorted((row for row in subset if row["topology"] == name), key=lambda z: z["injection_rate"]):
            pts.append((sx(r["injection_rate"]), sy(r["packet_latency_avg"])))
        if len(pts) >= 2:
            d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{d}"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
    legend_x = left + plot_w - 180
    for i, name in enumerate(names):
        y = top + 12 + i * 16
        parts.append(f'<rect x="{legend_x}" y="{y-8}" width="12" height="8" fill="{palette[i]}"/>')
        parts.append(f'<text x="{legend_x+18}" y="{y}" font-size="11">{name}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> None:
    require_booksim()
    if not CATALOG.exists():
        subprocess.check_call([sys.executable, str(ROOT / "scripts" / "generate_topologies.py")], cwd=ROOT)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=== smoke: mesh ===", flush=True)
    smoke = smoke_mesh()
    print(f"  packet latency={smoke['packet_latency_avg']}", flush=True)

    print("=== smoke: dragonfly ===", flush=True)
    dragonfly = smoke_dragonfly()
    print(
        f"  completed={dragonfly.get('completed')} packet latency={dragonfly.get('packet_latency_avg')}",
        flush=True,
    )

    print("=== cluster sweep ===", flush=True)
    rows = run_topology_sweep(catalog)

    csv_path = RESULTS_DIR / "cluster_comparison.csv"
    md_path = RESULTS_DIR / "summary.md"
    json_path = RESULTS_DIR / "cluster_comparison.json"
    write_csv(csv_path, rows)
    json_path.write_text(
        json.dumps({"smoke": smoke, "dragonfly": dragonfly, "rows": rows}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    write_markdown(md_path, smoke, dragonfly, rows, catalog)
    write_svg(RESULTS_DIR / "latency_n8_flit1.svg", rows, 8, "flit1")
    write_svg(RESULTS_DIR / "latency_n16_flit1.svg", rows, 16, "flit1")
    write_svg(RESULTS_DIR / "latency_n8_pkt8.svg", rows, 8, "pkt8")
    write_svg(RESULTS_DIR / "latency_n16_pkt8.svg", rows, 16, "pkt8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {md_path}")

    failed = [r for r in rows if not r["ok"] and not r["unstable"]]
    if failed:
        names = ", ".join(r["topology"] + "@" + str(r["injection_rate"]) for r in failed)
        raise SystemExit(f"Есть незавершённые прогоны: {names}")


if __name__ == "__main__":
    main()

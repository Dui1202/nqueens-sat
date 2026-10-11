"""Result schema, CSV persistence, and experiment environment metadata."""
from __future__ import annotations

import csv
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import psutil


@dataclass
class BenchmarkResult:
    n: int
    method: str
    encoding: str | None
    solver: str
    run: int

    status: str

    original_variables: int | None
    auxiliary_variables: int | None
    total_variables: int | None
    clauses: int | None

    build_time: float | None
    solve_time: float | None
    total_time: float | None

    peak_memory_mb: float | None = None
    error: str | None = None


def append_result(
    result: BenchmarkResult,
    results_dir: Path,
) -> None:
    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw_results_file = results_dir / "raw_results.csv"
    exists = raw_results_file.exists()

    with raw_results_file.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                field.name for field in BenchmarkResult.__dataclass_fields__.values()
            ],
        )

        if not exists:
            writer.writeheader()

        writer.writerow(asdict(result))


def cpu_model_name() -> str:
    """Read the hardware model when platform.processor() is empty or generic."""
    try:
        system = platform.system()
        if system == "Linux":
            fallback = ""
            with Path("/proc/cpuinfo").open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    key, separator, value = line.partition(":")
                    if separator and value.strip():
                        if key.strip() == "model name":
                            return value.strip()
                        if key.strip() in ("Hardware", "Processor"):
                            fallback = value.strip()
            if fallback:
                return fallback
        elif system == "Darwin":
            model = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True, stderr=subprocess.DEVNULL, timeout=2,
            ).strip()
            if model:
                return model
        elif system == "Windows":
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                model, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                if model.strip():
                    return model.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return platform.processor().strip() or "unknown"


def save_environment(
    results_dir: Path,
    *,
    n_values: list[int],
    sat_solver_name: str,
    timeout_seconds: float,
    repetitions: int,
    warmup_runs: int,
    memory_sample_interval: float,
) -> None:
    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    physical_ram_bytes = psutil.virtual_memory().total
    content = [
        f"experiment_id={results_dir.name}",
        f"created_utc={datetime.now(timezone.utc).isoformat()}",
        f"n_values={n_values}",
        f"platform={platform.platform()}",
        f"machine={platform.machine()}",
        f"cpu_model={cpu_model_name()}",
        f"physical_ram_bytes={physical_ram_bytes}",
        f"physical_ram_gib={physical_ram_bytes / 1024**3:.3f}",
        f"python={sys.version}",
        f"cpu_count={os.cpu_count()}",
        f"sat_solver={sat_solver_name}",
        "solver_threads=1",
        "solver_import_policy=lazy_selected_backend_and_dependencies",
        f"timeout_seconds={timeout_seconds}",
        f"repetitions={repetitions}",
        f"warmup_runs={warmup_runs}",
        f"memory_sample_interval_seconds={memory_sample_interval}",
        "peak_memory_metric=sampled worker-plus-descendants RSS in decimal MB",
    ]

    for package in ("python-sat", "ortools", "gurobipy", "cplex", "docplex", "psutil"):
        try:
            content.append(f"{package}={version(package)}")
        except PackageNotFoundError:
            content.append(f"{package}=unavailable")

    (results_dir / "environment.txt").write_text(
        "\n".join(content),
        encoding="utf-8",
    )

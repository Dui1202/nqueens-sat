"""Configure and orchestrate the N-Queens benchmark matrix."""

from __future__ import annotations

import multiprocessing as mp
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from experiments.cases import benchmark_configurations
from experiments.reporting import BenchmarkResult, append_result, save_environment
from experiments.runner import run_with_timeout
from src import solver as sat_solver

N_VALUES = [8, 16, 20, 24, 28, 32, 64, 128, 256]
REPETITIONS = 5
WARMUP_RUNS = 1
TIMEOUT_SECONDS = 60.0
MEMORY_SAMPLE_INTERVAL_SECONDS = 0.01
RESULTS_DIR = Path("results")


def main() -> None:
    # Import before running solvers so a missing export dependency fails early.
    from experiments.export_xlsx import export_csv_to_xlsx

    experiment_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex
    )
    results_dir = RESULTS_DIR / experiment_id
    results_dir.mkdir(parents=True, exist_ok=False)
    save_environment(
        results_dir,
        n_values=N_VALUES,
        sat_solver_name=sat_solver.SAT_SOLVER,
        timeout_seconds=TIMEOUT_SECONDS,
        repetitions=REPETITIONS,
        warmup_runs=WARMUP_RUNS,
        memory_sample_interval=MEMORY_SAMPLE_INTERVAL_SECONDS,
    )

    configurations = list(benchmark_configurations())

    total_runs = len(N_VALUES) * len(configurations) * REPETITIONS

    current_run = 0

    print(f"Starting {total_runs} measured benchmark runs")
    print(f"Warm-ups: {WARMUP_RUNS} per board/configuration (not saved)")

    print(f"Timeout: {TIMEOUT_SECONDS:.0f}s/run")

    print()

    for n in N_VALUES:
        for method, encoding, solver_name in configurations:
            configuration_name = f"SAT/{encoding}" if method == "sat" else method
            for warmup in range(1, WARMUP_RUNS + 1):
                print(
                    f"Warm-up N={n:<4} {configuration_name:<20} run={warmup}",
                    end=" ",
                    flush=True,
                )
                warmup_result = run_with_timeout(
                    n,
                    method,
                    encoding,
                    TIMEOUT_SECONDS,
                    memory_sample_interval=MEMORY_SAMPLE_INTERVAL_SECONDS,
                )
                print(f"-> {warmup_result['status']}")
                if warmup_result["error"]:
                    print(f"    {warmup_result['status']}: {warmup_result['error']}")

            for repetition in range(
                1,
                REPETITIONS + 1,
            ):
                current_run += 1

                print(
                    f"[{current_run}/{total_runs}] "
                    f"N={n:<4} "
                    f"{configuration_name:<20} "
                    f"run={repetition}",
                    end=" ",
                    flush=True,
                )

                raw = run_with_timeout(
                    n=n,
                    method=method,
                    encoding=encoding,
                    timeout=TIMEOUT_SECONDS,
                    memory_sample_interval=MEMORY_SAMPLE_INTERVAL_SECONDS,
                )

                result = BenchmarkResult(
                    n=n,
                    method=method,
                    encoding=encoding,
                    solver=solver_name,
                    run=repetition,
                    status=raw["status"],
                    original_variables=raw["original_variables"],
                    auxiliary_variables=raw["auxiliary_variables"],
                    total_variables=raw["total_variables"],
                    clauses=raw["clauses"],
                    build_time=raw["build_time"],
                    solve_time=raw["solve_time"],
                    total_time=raw["total_time"],
                    peak_memory_mb=raw["peak_memory_mb"],
                    error=raw["error"],
                )

                append_result(result, results_dir)

                if result.total_time is None:
                    time_text = "-"
                else:
                    time_text = f"{result.total_time:.6f}s"

                print(f"-> {result.status} " f"({time_text})")

                if result.error is not None:
                    print(f"    {result.status}: {result.error}")

    print()
    print(f"Results written to {results_dir / 'raw_results.csv'}")
    if current_run:
        workbook_path = export_csv_to_xlsx(results_dir / "raw_results.csv")
        print(f"Excel workbook written to {workbook_path}")


if __name__ == "__main__":
    mp.freeze_support()
    main()

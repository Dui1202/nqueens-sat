from __future__ import annotations

import csv
import multiprocessing as mp
import os
import platform
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Callable
from uuid import uuid4

import psutil
from src import solver as sat_solver

from src.variables import var_id

from src.baselines.result import SolveResult
from src.baselines.errors import solver_error_status

# ============================================================
# CONFIGURATION
# ============================================================

N_VALUES = [8, 16, 32, 64, 128, 256]

REPETITIONS = 5
WARMUP_RUNS = 1

TIMEOUT_SECONDS = 60.0
MEMORY_SAMPLE_INTERVAL_SECONDS = 0.01

RESULTS_DIR = Path("results")


# ============================================================
# RESULT MODEL
# ============================================================


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


# ============================================================
# SAT ENCODERS
# ============================================================


SAT_ENCODERS = {
    "pairwise": ("src.sat_encodings.pairwise", "PairWiseEncoder"),
    "binary": ("src.sat_encodings.binary", "BinaryEncoder"),
    "sequential": ("src.sat_encodings.sequential", "SequentialEncoder"),
    "commander": ("src.sat_encodings.commander", "CommanderEncoder"),
    "product": ("src.sat_encodings.product", "ProductEncoder"),
}


def load_sat_encoder(name: str):
    module_name, class_name = SAT_ENCODERS[name]
    return getattr(import_module(module_name), class_name)


# ============================================================
# N-QUEENS SAT BUILDER
# ============================================================


def build_sat_nqueens(
    n: int,
    encoder_type,
) -> tuple[list[list[int]], int]:
    """
    Return the N-Queens CNF and total allocated variable count.

    Board variables:
        1 .. n^2

    Auxiliary variables:
        n^2 + 1 ..
    """

    from pysat.formula import IDPool

    pool = IDPool(start_from=n * n + 1)
    encoder = encoder_type(pool)

    clauses: list[list[int]] = []

    # Exactly one queen per row.
    for row in range(n):
        literals = [var_id(row, col, n) for col in range(n)]

        clauses.extend(encoder.exactly_one(literals))

    # Exactly one queen per column.
    for col in range(n):
        literals = [var_id(row, col, n) for row in range(n)]

        clauses.extend(encoder.exactly_one(literals))

    # At most one queen per diagonal.
    diagonals: dict[
        tuple[str, int],
        list[int],
    ] = {}

    for row in range(n):
        for col in range(n):
            variable = var_id(row, col, n)

            diagonals.setdefault(
                ("sum", row + col),
                [],
            ).append(variable)

            diagonals.setdefault(
                ("difference", row - col),
                [],
            ).append(variable)

    for literals in diagonals.values():
        if len(literals) > 1:
            clauses.extend(encoder.at_most_one(literals))

    # The pool starts at n² even when no auxiliary variables are allocated.
    return clauses, pool.top


# ============================================================
# SOLUTION VERIFIER
# ============================================================


def verify_columns_solution(
    solution: list[int],
    n: int,
) -> None:
    """
    solution[row] = column of the queen.
    """

    if len(solution) != n:
        raise AssertionError(f"Expected {n} queens, got {len(solution)}")

    if any(column < 0 or column >= n for column in solution):
        raise AssertionError("Queen column outside board.")

    if len(set(solution)) != n:
        raise AssertionError("Column collision.")

    main_diagonals = {row - column for row, column in enumerate(solution)}

    if len(main_diagonals) != n:
        raise AssertionError("Main diagonal collision.")

    anti_diagonals = {row + column for row, column in enumerate(solution)}

    if len(anti_diagonals) != n:
        raise AssertionError("Anti-diagonal collision.")


def sat_model_to_columns(
    model: list[int],
    n: int,
) -> list[int]:
    """
    Convert SAT board variables into:
        solution[row] = column

    Auxiliary variables are ignored.
    """

    positive = {literal for literal in model if 0 < literal <= n * n}

    solution: list[int] = []

    for row in range(n):
        columns = [col for col in range(n) if var_id(row, col, n) in positive]

        if len(columns) != 1:
            raise AssertionError(f"Row {row} has {len(columns)} queens.")

        solution.append(columns[0])

    return solution


# ============================================================
# SAT BENCHMARK
# ============================================================


def benchmark_sat_once(
    n: int,
    encoding_name: str,
    solver_name: str | None = None,
) -> dict:
    encoder_type = load_sat_encoder(encoding_name)
    # Load the selected backend before starting solve/build timers, just as
    # baseline adapters are loaded before their total-time measurement.
    import_module("pysat.solvers")

    total_start = time.perf_counter()

    # ---------------------------
    # Build CNF
    # ---------------------------

    build_start = time.perf_counter()

    clauses, total_variables = build_sat_nqueens(
        n,
        encoder_type,
    )

    build_time = time.perf_counter() - build_start

    # ---------------------------
    # Solve
    # ---------------------------

    solve_start = time.perf_counter()

    model = sat_solver.solve_cnf(clauses, solver_name=solver_name)
    is_sat = model is not None

    if is_sat:
        solution = sat_model_to_columns(
            model,
            n,
        )

    solve_time = time.perf_counter() - solve_start
    total_time = time.perf_counter() - total_start

    # Diagnostics and verification are outside all reported timings.
    original_variables = n * n

    auxiliary_variables = total_variables - original_variables

    if is_sat:
        verify_columns_solution(solution, n)

    return {
        "status": "SAT" if is_sat else "UNSAT",
        "original_variables": original_variables,
        "auxiliary_variables": auxiliary_variables,
        "total_variables": total_variables,
        "clauses": len(clauses),
        "build_time": build_time,
        "solve_time": solve_time,
        "total_time": total_time,
    }


# ============================================================
# NON-SAT BASELINES
# ============================================================


BASELINE_SOLVERS: dict[
    str,
    tuple[str, tuple[str, str]],
] = {
    "cp-sat": (
        "ortools-cp-sat",
        ("src.baselines.cp_sat", "solve_nqueens_cp_sat"),
    ),
    "gurobi": (
        "gurobi",
        ("src.baselines.gurobi_mip", "solve_nqueens_gurobi"),
    ),
    "cplex-mip": (
        "cplex-mip",
        ("src.baselines.cplex_mip", "solve_nqueens_cplex_mip"),
    ),
    "cplex-cp": (
        "cplex-cp-optimizer",
        ("src.baselines.cplex_cp", "solve_nqueens_cplex_cp"),
    ),
}


def load_baseline_solver(method: str) -> Callable[[int, float | None], SolveResult]:
    _, (module_name, function_name) = BASELINE_SOLVERS[method]
    return getattr(import_module(module_name), function_name)


def benchmark_baseline_once(
    n: int,
    method: str,
    timeout: float | None = None,
) -> dict:
    solve_function = load_baseline_solver(method)

    start = time.perf_counter()

    result = solve_function(n, timeout)

    total_time = time.perf_counter() - start

    # Do not include verification time.
    if result.status == "SAT":
        if result.solution is None:
            raise AssertionError("Baseline returned SAT without a solution.")
        verify_columns_solution(
            result.solution,
            n,
        )

    return {
        "status": result.status,
        "error": result.error,
        # Not directly comparable to CNF metrics.
        "original_variables": None,
        "auxiliary_variables": None,
        "total_variables": None,
        "clauses": None,
        # Existing baseline functions currently build
        # and solve internally, so only total time is
        # measured fairly here.
        "build_time": None,
        "solve_time": None,
        "total_time": total_time,
    }


# ============================================================
# TIMEOUT WORKER
# ============================================================


def worker(
    connection: Connection,
    n: int,
    method: str,
    encoding: str | None,
    timeout: float,
    solver_name: str,
) -> None:
    try:
        # Descendants (including CP Optimizer) inherit this private group.
        if os.name == "posix":
            os.setsid()
        connection.send({"ready": True})
        if method == "sat":
            assert encoding is not None
            result = benchmark_sat_once(n, encoding, solver_name)
        else:
            result = benchmark_baseline_once(n, method, timeout)
        if result["status"] == "ERROR" and result.get("error"):
            result["status"] = solver_error_status(result["error"])
        connection.send({"ok": True, "result": result})
    except Exception as exception:
        connection.send(
            {
                "ok": False,
                "status": solver_error_status(exception),
                "error": f"{type(exception).__name__}: {exception}",
            }
        )
    finally:
        connection.close()


def stop_worker(process: mp.Process, group_ready: bool) -> None:
    """Bound cleanup and stop external solvers as well as the Python worker."""
    if os.name == "posix" and group_ready:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.join(0.2)
        # Always escalate for descendants, even if their parent already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif os.name == "nt" and process.is_alive():
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        if result.returncode and process.is_alive():
            raise RuntimeError(f"Could not stop worker tree: {result.stderr!r}")
    elif process.is_alive():
        process.terminate()
    process.join(0.5)
    if process.is_alive():
        process.kill()
        process.join(1)
    if process.is_alive():
        raise RuntimeError("Could not stop benchmark worker")


def failed_result(status: str, *, total_time=None, error=None, peak_memory_mb=None) -> dict:
    return {
        "status": status,
        "original_variables": None,
        "auxiliary_variables": None,
        "total_variables": None,
        "clauses": None,
        "build_time": None,
        "solve_time": None,
        "total_time": total_time,
        "peak_memory_mb": peak_memory_mb,
        "error": error,
    }


def process_tree_rss(process: psutil.Process) -> int | None:
    """Sample resident bytes for the worker and currently observable descendants."""
    try:
        rss = process.memory_info().rss
        children = process.children(recursive=True)
        for child in children:
            try:
                rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                # External solvers can finish between enumeration and sampling.
                continue
        return rss
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return None


def run_with_timeout(
    n: int,
    method: str,
    encoding: str | None,
    timeout: float,
) -> dict:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    # Fresh interpreters avoid inheriting native solver state and make startup
    # consistent across Python versions and platforms.
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=worker,
        args=(sender, n, method, encoding, timeout, sat_solver.SAT_SOLVER),
    )
    group_ready = False
    started = False
    peak_memory_mb = None
    deadline = time.monotonic() + timeout
    try:
        process.start()
        started = True
        sender.close()
        try:
            monitored_process = psutil.Process(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            monitored_process = None
        # Read while the worker is alive so a large error cannot block its exit.
        while True:
            rss = process_tree_rss(monitored_process) if monitored_process is not None else None
            if rss is not None:
                peak_memory_mb = max(peak_memory_mb or 0, rss / 1_000_000)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not receiver.poll(min(MEMORY_SAMPLE_INTERVAL_SECONDS, remaining)):
                continue
            try:
                response = receiver.recv()
            except EOFError:
                process.join(0.2)
                return failed_result(
                    "ERROR", error=f"Worker exited with code {process.exitcode}",
                    peak_memory_mb=peak_memory_mb,
                )
            if response.get("ready"):
                group_ready = True
                continue
            if not response["ok"]:
                return failed_result(
                    response["status"], error=response["error"], peak_memory_mb=peak_memory_mb,
                )
            return {"error": None, **response["result"], "peak_memory_mb": peak_memory_mb}
        return failed_result("TIMEOUT", total_time=timeout, peak_memory_mb=peak_memory_mb)
    finally:
        try:
            if started:
                process.join(0.1)
                stop_worker(process, group_ready)
        finally:
            receiver.close()
            sender.close()
            if not process.is_alive():
                process.close()


# ============================================================
# CSV
# ============================================================


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


# ============================================================
# ENVIRONMENT
# ============================================================


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


def save_environment(results_dir: Path) -> None:
    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    physical_ram_bytes = psutil.virtual_memory().total
    content = [
        f"experiment_id={results_dir.name}",
        f"created_utc={datetime.now(timezone.utc).isoformat()}",
        f"n_values={N_VALUES}",
        f"platform={platform.platform()}",
        f"machine={platform.machine()}",
        f"cpu_model={cpu_model_name()}",
        f"physical_ram_bytes={physical_ram_bytes}",
        f"physical_ram_gib={physical_ram_bytes / 1024**3:.3f}",
        f"python={sys.version}",
        f"cpu_count={os.cpu_count()}",
        f"sat_solver={sat_solver.SAT_SOLVER}",
        "solver_threads=1",
        "solver_import_policy=lazy_selected_backend_and_dependencies",
        f"timeout_seconds={TIMEOUT_SECONDS}",
        f"repetitions={REPETITIONS}",
        f"warmup_runs={WARMUP_RUNS}",
        f"memory_sample_interval_seconds={MEMORY_SAMPLE_INTERVAL_SECONDS}",
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


# ============================================================
# BENCHMARK MATRIX
# ============================================================


def benchmark_configurations():
    # SAT configurations.
    for encoding in SAT_ENCODERS:
        yield (
            "sat",
            encoding,
            sat_solver.SAT_SOLVER,
        )

    # Other exact approaches.
    for method, (
        solver_name,
        _,
    ) in BASELINE_SOLVERS.items():
        yield (
            method,
            None,
            solver_name,
        )


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    # Import before running solvers so a missing export dependency fails early.
    from experiments.export_xlsx import export_csv_to_xlsx

    experiment_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex
    )
    results_dir = RESULTS_DIR / experiment_id
    results_dir.mkdir(parents=True, exist_ok=False)
    save_environment(results_dir)

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
                    end=" ", flush=True,
                )
                warmup_result = run_with_timeout(n, method, encoding, TIMEOUT_SECONDS)
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
    # Important for multiprocessing,
    # especially on Windows/macOS.
    mp.freeze_support()

    main()

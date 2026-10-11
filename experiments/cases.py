"""Lazy solver registry and measurements for individual benchmark cases."""
from __future__ import annotations

import time
from importlib import import_module
from typing import Callable

from src import solver as sat_solver
from src.baselines.result import SolveResult
from src.nqueens import build_sat_nqueens, sat_model_to_columns, verify_columns_solution


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

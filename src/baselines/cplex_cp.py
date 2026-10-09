from docplex.cp.model import CpoModel
from docplex.cp.solution import (
    SOLVE_STATUS_FEASIBLE,
    SOLVE_STATUS_INFEASIBLE,
    SOLVE_STATUS_JOB_FAILED,
    SOLVE_STATUS_OPTIMAL,
)

from src.baselines.result import SolveResult


def solve_nqueens_cplex_cp(n: int, timeout: float | None = None) -> SolveResult:
    if n <= 0:
        raise ValueError("n must be positive")

    model = CpoModel()

    queens = model.integer_var_list(
        n,
        0,
        n - 1,
        "q",
    )

    model.add(model.all_diff(queens))

    model.add(model.all_diff([queens[row] - row for row in range(n)]))

    model.add(model.all_diff([queens[row] + row for row in range(n)]))

    options = {"LogVerbosity": "Quiet"}
    if timeout is not None:
        options["TimeLimit"] = timeout
    result = model.solve(**options)

    status = result.get_solve_status()
    if status == SOLVE_STATUS_INFEASIBLE:
        return SolveResult("UNSAT")
    if status == SOLVE_STATUS_JOB_FAILED:
        return SolveResult("ERROR", error="CP Optimizer reported JobFailed")
    if status not in (SOLVE_STATUS_FEASIBLE, SOLVE_STATUS_OPTIMAL):
        return SolveResult("UNKNOWN")

    return SolveResult("SAT", [int(result[queen]) for queen in queens])

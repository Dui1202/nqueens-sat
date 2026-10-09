from ortools.sat.python import cp_model

from src.baselines.result import SolveResult


def solve_nqueens_cp_sat(n: int, timeout: float | None = None) -> SolveResult:
    if n <= 0:
        raise ValueError("n must be positive")

    model = cp_model.CpModel()

    queens = [model.new_int_var(0, n - 1, f"q_{row}") for row in range(n)]

    model.add_all_different(queens)

    model.add_all_different([queens[row] - row for row in range(n)])

    model.add_all_different([queens[row] + row for row in range(n)])

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    if timeout is not None:
        solver.parameters.max_time_in_seconds = timeout

    status = solver.solve(model)

    if status == cp_model.INFEASIBLE:
        return SolveResult("UNSAT")
    if status == cp_model.MODEL_INVALID:
        return SolveResult("ERROR", error="CP-SAT reported MODEL_INVALID")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveResult("UNKNOWN")

    return SolveResult("SAT", [solver.value(queens[row]) for row in range(n)])

import cplex

from src.baselines.result import SolveResult


def solve_nqueens_cplex_mip(n: int, timeout: float | None = None) -> SolveResult:
    if n <= 0:
        raise ValueError("n must be positive")

    model = cplex.Cplex()

    model.set_log_stream(None)
    model.set_error_stream(None)
    model.set_warning_stream(None)
    model.set_results_stream(None)
    model.parameters.threads.set(1)
    if timeout is not None:
        model.parameters.timelimit.set(timeout)

    variable_names = [f"q_{row}_{col}" for row in range(n) for col in range(n)]

    model.variables.add(
        names=variable_names,
        types=[model.variables.type.binary for _ in variable_names],
    )

    def variable_index(row: int, col: int) -> int:
        return row * n + col

    for row in range(n):
        indices = [variable_index(row, col) for col in range(n)]

        model.linear_constraints.add(
            lin_expr=[
                cplex.SparsePair(
                    ind=indices,
                    val=[1.0] * n,
                )
            ],
            senses=["E"],
            rhs=[1.0],
        )

    for col in range(n):
        indices = [variable_index(row, col) for row in range(n)]

        model.linear_constraints.add(
            lin_expr=[
                cplex.SparsePair(
                    ind=indices,
                    val=[1.0] * n,
                )
            ],
            senses=["E"],
            rhs=[1.0],
        )

    diagonals: dict[
        tuple[str, int],
        list[int],
    ] = {}

    for row in range(n):
        for col in range(n):
            index = variable_index(row, col)

            diagonals.setdefault(
                ("sum", row + col),
                [],
            ).append(index)

            diagonals.setdefault(
                ("difference", row - col),
                [],
            ).append(index)

    for indices in diagonals.values():
        if len(indices) <= 1:
            continue

        model.linear_constraints.add(
            lin_expr=[
                cplex.SparsePair(
                    ind=indices,
                    val=[1.0] * len(indices),
                )
            ],
            senses=["L"],
            rhs=[1.0],
        )

    model.solve()

    status = model.solution.get_status()

    infeasible_statuses = {
        model.solution.status.MIP_infeasible,
        model.solution.status.infeasible,
    }

    if status in infeasible_statuses:
        return SolveResult("UNSAT")

    if not model.solution.is_primal_feasible():
        if status == model.solution.status.mem_limit_infeasible:
            return SolveResult("OOM", error="CPLEX reached its memory limit")
        if status in (model.solution.status.MIP_time_limit_infeasible,
                      model.solution.status.abort_time_limit):
            return SolveResult("TIMEOUT")
        return SolveResult("UNKNOWN")

    values = model.solution.get_values()

    solution: list[int] = []

    for row in range(n):
        queen_column = next(
            col for col in range(n) if values[variable_index(row, col)] > 0.5
        )

        solution.append(queen_column)

    return SolveResult("SAT", solution)

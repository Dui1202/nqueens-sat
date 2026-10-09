# Shared by direct solves and benchmarks. The CaDiCaL backend is sequential.
SAT_SOLVER = "cadical195"


def solve_cnf(
    clauses: list[list[int]],
    *,
    solver_name: str | None = None,
) -> list[int] | None:
    """Solve using the shared SAT configuration; None means proven UNSAT."""
    from pysat.solvers import Solver

    with Solver(
        name=SAT_SOLVER if solver_name is None else solver_name,
        bootstrap_with=clauses,
    ) as solver:

        if not solver.solve():
            return None

        model = solver.get_model()
        if model is None:
            raise AssertionError("SAT solver returned SAT without model.")
        return model

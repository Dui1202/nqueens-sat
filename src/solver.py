from pysat.solvers import Glucose3


def solve_cnf(
    clauses: list[list[int]],
) -> list[int] | None:
    with Glucose3(
        bootstrap_with=clauses
    ) as solver:

        if not solver.solve():
            return None

        return solver.get_model()
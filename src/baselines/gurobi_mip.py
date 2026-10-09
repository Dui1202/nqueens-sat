import gurobipy as gp
from gurobipy import GRB

from src.baselines.result import SolveResult


def solve_nqueens_gurobi(n: int, timeout: float | None = None) -> SolveResult:
    if n <= 0:
        raise ValueError("n must be positive")

    model = gp.Model("n_queens")

    model.Params.OutputFlag = 0
    model.Params.Threads = 1
    if timeout is not None:
        model.Params.TimeLimit = timeout

    queens = model.addVars(
        n,
        n,
        vtype=GRB.BINARY,
        name="q",
    )

    for row in range(n):
        model.addConstr(
            gp.quicksum(queens[row, col] for col in range(n)) == 1,
            name=f"row_{row}",
        )

    for col in range(n):
        model.addConstr(
            gp.quicksum(queens[row, col] for row in range(n)) == 1,
            name=f"column_{col}",
        )

    for difference in range(-(n - 1), n):
        diagonal = [
            queens[row, col]
            for row in range(n)
            for col in range(n)
            if row - col == difference
        ]

        if len(diagonal) > 1:
            model.addConstr(
                gp.quicksum(diagonal) <= 1,
                name=f"main_diagonal_{difference}",
            )

    for total in range(1, 2 * n - 2):
        diagonal = [
            queens[row, col]
            for row in range(n)
            for col in range(n)
            if row + col == total
        ]

        if len(diagonal) > 1:
            model.addConstr(
                gp.quicksum(diagonal) <= 1,
                name=f"anti_diagonal_{total}",
            )

    model.optimize()

    if model.Status == GRB.INFEASIBLE:
        return SolveResult("UNSAT")

    if model.SolCount == 0:
        if model.Status == GRB.TIME_LIMIT:
            return SolveResult("TIMEOUT")
        return SolveResult("UNKNOWN")

    solution: list[int] = []

    for row in range(n):
        queen_column = next(col for col in range(n) if queens[row, col].X > 0.5)

        solution.append(queen_column)

    return SolveResult("SAT", solution)

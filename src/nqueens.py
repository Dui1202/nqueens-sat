"""N-Queens CNF construction, model conversion, and solution validation."""
from __future__ import annotations

from src.variables import var_id


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

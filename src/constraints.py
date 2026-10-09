from pysat.formula import IDPool

from src.variables import var_id
from src.sat_encodings.pairwise import PairWiseEncoder


def build_nqueens_cnf(n: int) -> list[list[int]]:
    encoder = PairWiseEncoder(IDPool(start_from=n * n + 1))
    clauses: list[list[int]] = []

    for row in range(n):
        row_variables: list[int] = []

        for col in range(n):
            row_variables.append(var_id(row, col, n))

        clauses.extend(encoder.exactly_one(row_variables))

    for col in range(n):
        column_variables: list[int] = []
        for row in range(n):
            column_variables.append(var_id(row, col, n))

        clauses.extend(encoder.exactly_one(column_variables))

    for diagonal in range(-(n - 1), n):
        diagonal_variables = [
            var_id(row, col, n)
            for row in range(n)
            for col in range(n)
            if row - col == diagonal
        ]

        if len(diagonal_variables) > 1:
            clauses.extend(encoder.at_most_one(diagonal_variables))

    for diagonal in range(1, 2 * n - 2):
        diagonal_variables = [
            var_id(row, col, n)
            for row in range(n)
            for col in range(n)
            if row + col == diagonal
        ]

        if len(diagonal_variables) > 1:
            clauses.extend(encoder.at_most_one(diagonal_variables))

    return clauses

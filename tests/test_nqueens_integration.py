"""Run with: .venv/bin/python -m unittest discover -s tests -v."""

import unittest

from pysat.formula import IDPool

from src.decode import decode_board
from src.sat_encodings.binary import BinaryEncoder
from src.sat_encodings.commander import CommanderEncoder
from src.sat_encodings.pairwise import PairWiseEncoder
from src.sat_encodings.product import ProductEncoder
from src.sat_encodings.sequential import SequentialEncoder
from src.solver import solve_cnf
from src.variables import var_id


ENCODERS = (
    PairWiseEncoder,
    BinaryEncoder,
    SequentialEncoder,
    CommanderEncoder,
    ProductEncoder,
)


def encode_board(n, encoder_type):
    """Compose real encoders; reserve board IDs before allocating auxiliaries.

    This fixture tests encoder integration independently of the benchmark
    builder. Each board gets one pool and encoder.
    """
    encoder = encoder_type(IDPool(start_from=n * n + 1))
    clauses = []
    diagonals = {}
    for row in range(n):
        clauses.extend(encoder.exactly_one([
            var_id(row, col, n) for col in range(n)
        ]))
    for col in range(n):
        clauses.extend(encoder.exactly_one([
            var_id(row, col, n) for row in range(n)
        ]))
    for row in range(n):
        for col in range(n):
            for key in (("sum", row + col), ("difference", row - col)):
                diagonals.setdefault(key, []).append(var_id(row, col, n))
    for literals in diagonals.values():
        if len(literals) > 1:
            clauses.extend(encoder.at_most_one(literals))
    return clauses


class NQueensIntegrationTests(unittest.TestCase):
    def assert_valid_board(self, model, n):
        self.assertIsNotNone(model, f"Expected a solution for n={n}")
        board = decode_board(model, n)
        self.assertEqual(len(board), n)
        for row in board:
            self.assertEqual(len(row), n)
            self.assertTrue(all(cell in ("Q", ".") for cell in row))
        queens = [(r, c) for r, row in enumerate(board)
                  for c, cell in enumerate(row) if cell == "Q"]
        self.assertEqual(len(queens), n)
        self.assertEqual(len({r for r, c in queens}), n, "Row collision")
        self.assertEqual(len({c for r, c in queens}), n, "Column collision")
        self.assertEqual(len({r - c for r, c in queens}), n, "Diagonal collision")
        self.assertEqual(len({r + c for r, c in queens}), n, "Diagonal collision")

    def test_satisfiable_sizes_produce_nonattacking_boards(self):
        for encoder_type in ENCODERS:
            for n in (1, 4, 5, 8):
                with self.subTest(encoder=encoder_type.__name__, n=n):
                    self.assert_valid_board(solve_cnf(encode_board(n, encoder_type)), n)

    def test_sizes_two_and_three_are_unsatisfiable(self):
        for encoder_type in ENCODERS:
            for n in (2, 3):
                with self.subTest(encoder=encoder_type.__name__, n=n):
                    self.assertIsNone(solve_cnf(encode_board(n, encoder_type)))

    def test_all_small_board_solutions_are_preserved(self):
        # Block board placements, not auxiliary assignments: multiple SAT
        # models may represent the same arrangement of queens.
        for encoder_type in ENCODERS:
            for n, expected_count in ((4, 2), (5, 10)):
                with self.subTest(encoder=encoder_type.__name__, n=n):
                    clauses = encode_board(n, encoder_type)
                    count = 0
                    while (model := solve_cnf(clauses)) is not None:
                        self.assert_valid_board(model, n)
                        count += 1
                        self.assertLessEqual(count, expected_count)
                        clauses.append([-literal for literal in model
                                        if 0 < literal <= n * n])
                    self.assertEqual(count, expected_count)

    def test_benchmark_builder_produces_valid_eight_queens_board(self):
        # Keep this import local so a broken application import does not
        # prevent the independent encoder cases from running.
        from src.nqueens import build_sat_nqueens

        clauses, total_variables = build_sat_nqueens(8, PairWiseEncoder)
        self.assertEqual(total_variables, 64)
        self.assert_valid_board(solve_cnf(clauses), 8)


if __name__ == "__main__":
    unittest.main()

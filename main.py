from src.constraints import build_nqueens_cnf
from src.solver import solve_cnf
from src.decode import decode_board


def main():
    n = 8

    clauses = build_nqueens_cnf(n)

    print(f"N = {n}")
    print(f"Primary variables = {n * n}")
    print(f"Clauses = {len(clauses)}")

    model = solve_cnf(clauses)

    if model is None:
        print("UNSAT")
        return

    print("SAT")

    board = decode_board(
        model,
        n,
    )

    for row in board:
        print(" ".join(row))


if __name__ == "__main__":
    main()
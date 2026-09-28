from src.variables import var_id


def decode_board(
    model: list[int],
    n: int,
) -> list[list[str]]:
    positive_variables = {
        variable
        for variable in model
        if variable > 0
    }

    board: list[list[str]] = []

    for row in range(n):
        board_row: list[str] = []

        for col in range(n):
            variable = var_id(
                row,
                col,
                n,
            )

            if variable in positive_variables:
                board_row.append("Q")
            else:
                board_row.append(".")

        board.append(board_row)

    return board
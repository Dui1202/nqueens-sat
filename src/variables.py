def var_id(row: int, col: int, n: int) -> int:
    """
    Convert a board position (row, col) into a SAT variable ID.

    row, col: 0-based
    SAT IDs: 1-based
    """
    return row * n + col + 1
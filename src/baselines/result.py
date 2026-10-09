from dataclasses import dataclass
from typing import Literal


@dataclass
class SolveResult:
    """Only UNSAT represents a proof of infeasibility."""

    status: Literal["SAT", "UNSAT", "UNKNOWN", "TIMEOUT", "LICENSE_LIMIT", "ERROR"]
    solution: list[int] | None = None
    error: str | None = None

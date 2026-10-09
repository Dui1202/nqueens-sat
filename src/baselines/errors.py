"""Classify solver failures without hiding their original diagnostic messages."""

import re

from cplex.exceptions import CplexSolverError
from gurobipy import GRB, GurobiError


def solver_error_status(error: Exception | str) -> str:
    if isinstance(error, GurobiError) and error.errno in (
        GRB.Error.NO_LICENSE, GRB.Error.SIZE_LIMIT_EXCEEDED,
    ):
        return "LICENSE_LIMIT"
    if isinstance(error, CplexSolverError) and 1016 in error.args:
        return "LICENSE_LIMIT"

    message = str(error).lower()
    # DOcplex wraps external CP Optimizer diagnostics in text exceptions.
    if re.search(r"\blicen[cs](?:e[ds]?|ing)\b", message):
        return "LICENSE_LIMIT"
    if "community edition" in message and "problem size limit" in message:
        return "LICENSE_LIMIT"
    if re.search(r"\bcplex error\s+1016\b", message):
        return "LICENSE_LIMIT"
    return "ERROR"

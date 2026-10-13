"""Classify solver failures without hiding their original diagnostic messages."""

import errno
import re
import sys


def solver_error_status(error: Exception | str) -> str:
    if isinstance(error, MemoryError) or (
        isinstance(error, OSError) and error.errno == errno.ENOMEM
    ):
        return "OOM"
    # Inspect only libraries already loaded by the selected solver. Error
    # handling must not import another solver or require its installation.
    gurobi = sys.modules.get("gurobipy")
    cplex_errors = sys.modules.get("cplex.exceptions")
    if isinstance(error, getattr(gurobi, "GurobiError", ())) and error.errno == gurobi.GRB.Error.OUT_OF_MEMORY:
        return "OOM"
    if isinstance(error, getattr(cplex_errors, "CplexSolverError", ())) and 1001 in error.args:
        return "OOM"
    if isinstance(error, getattr(gurobi, "GurobiError", ())) and error.errno in (
        gurobi.GRB.Error.NO_LICENSE, gurobi.GRB.Error.SIZE_LIMIT_EXCEEDED,
    ):
        return "LICENSE_LIMIT"
    if isinstance(error, getattr(cplex_errors, "CplexSolverError", ())) and 1016 in error.args:
        return "LICENSE_LIMIT"

    message = str(error).lower()
    if any(marker in message for marker in (
        "out of memory", "out-of-memory", "cannot allocate memory",
        "not enough memory", "std::bad_alloc", "memoryerror",
        "failed to allocate memory", "memory allocation failed",
        "memory allocation still failed",
    )) or re.search(r"\bcplex error\s+1001\b", message):
        return "OOM"
    # DOcplex wraps external CP Optimizer diagnostics in text exceptions.
    if re.search(r"\blicen[cs](?:e[ds]?|ing)\b", message):
        return "LICENSE_LIMIT"
    if "community edition" in message and "problem size limit" in message:
        return "LICENSE_LIMIT"
    if re.search(r"\bcplex error\s+1016\b", message):
        return "LICENSE_LIMIT"
    return "ERROR"

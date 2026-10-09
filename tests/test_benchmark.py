import contextlib
import io
import multiprocessing as mp
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
from importlib import import_module

import cplex
from gurobipy import GRB
from ortools.sat.python import cp_model

from experiments import benchmark as benchmark
from src import solver as sat_solver


def spawn_external_solver(path, timeout=None):
    # A stand-in for CP Optimizer: keeps running even if its Python parent dies.
    child = subprocess.Popen([
        sys.executable, "-c",
        "import os, signal, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)",
        str(path),
    ])
    child.wait()


class BenchmarkRegressionTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux", "Uses /proc to distinguish zombies")
    def test_timeout_stops_external_solver_even_if_it_ignores_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "solver.pid"
            try:
                with patch.object(benchmark.mp, "get_context", return_value=mp.get_context("fork")), \
                     patch.object(benchmark, "load_baseline_solver", return_value=spawn_external_solver):
                    result = benchmark.run_with_timeout(pid_file, "external", None, 1.0)
                self.assertEqual(result["status"], "TIMEOUT")
                self.assertGreater(result["peak_memory_mb"], 0)
                self.assertTrue(pid_file.exists(), "The external solver must have started")
                pid = int(pid_file.read_text())
                deadline = time.monotonic() + 1
                state = Path(f"/proc/{pid}/stat")
                while state.exists() and state.read_text().split()[2] != "Z" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(not state.exists() or state.read_text().split()[2] == "Z",
                                "Timed-out external solver is still running")
            finally:
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_cp_sat_unknown_is_not_unsat(self):
        with patch.object(cp_model.CpSolver, "solve", return_value=cp_model.UNKNOWN):
            result = benchmark.benchmark_baseline_once(8, "cp-sat")
        self.assertEqual(result["status"], "UNKNOWN")

    def test_invalid_cp_sat_model_is_an_error(self):
        with patch.object(cp_model.CpSolver, "solve", return_value=cp_model.MODEL_INVALID):
            result = benchmark.benchmark_baseline_once(8, "cp-sat")
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["error"])

    def test_cp_optimizer_unknown_is_not_unsat(self):
        result = MagicMock()
        result.__bool__.return_value = False
        result.get_solve_status.return_value = "Unknown"
        with patch("src.baselines.cplex_cp.CpoModel.solve", return_value=result):
            row = benchmark.benchmark_baseline_once(8, "cplex-cp")
        self.assertEqual(row["status"], "UNKNOWN")

    def test_cplex_mip_preserves_inconclusive_and_infeasible_statuses(self):
        with cplex.Cplex() as real_model:
            statuses = real_model.solution.status
        for status, expected in (
            (statuses.MIP_abort_infeasible, "UNKNOWN"),
            (statuses.MIP_time_limit_infeasible, "TIMEOUT"),
            (statuses.MIP_infeasible, "UNSAT"),
        ):
            with self.subTest(status=status):
                model = MagicMock()
                model.solution.status = statuses
                model.solution.get_status.return_value = status
                model.solution.is_primal_feasible.return_value = False
                with patch("src.baselines.cplex_mip.cplex.Cplex", return_value=model):
                    row = benchmark.benchmark_baseline_once(1, "cplex-mip", 2)
                self.assertEqual(row["status"], expected)
                model.parameters.timelimit.set.assert_called_once_with(2)
                model.parameters.threads.set.assert_called_once_with(1)

    def test_gurobi_preserves_inconclusive_and_infeasible_statuses(self):
        for status, expected in (
            (GRB.INTERRUPTED, "UNKNOWN"), (GRB.TIME_LIMIT, "TIMEOUT"),
            (GRB.INFEASIBLE, "UNSAT"),
        ):
            with self.subTest(status=status):
                model = MagicMock(Status=status, SolCount=0)
                with patch("src.baselines.gurobi_mip.gp.Model", return_value=model), \
                     patch("src.baselines.gurobi_mip.gp.quicksum", return_value=0):
                    row = benchmark.benchmark_baseline_once(1, "gurobi", 2)
                self.assertEqual(row["status"], expected)
                self.assertEqual(model.Params.TimeLimit, 2)
                self.assertEqual(model.Params.Threads, 1)

    def test_cp_adapters_apply_native_time_limits_and_one_worker(self):
        solver = MagicMock()
        solver.solve.return_value = cp_model.UNKNOWN
        with patch("src.baselines.cp_sat.cp_model.CpSolver", return_value=solver):
            benchmark.benchmark_baseline_once(4, "cp-sat", 2)
        self.assertEqual(solver.parameters.max_time_in_seconds, 2)
        self.assertEqual(solver.parameters.num_search_workers, 1)
        result = MagicMock()
        result.get_solve_status.return_value = "Unknown"
        with patch("src.baselines.cplex_cp.CpoModel.solve", return_value=result) as solve:
            benchmark.benchmark_baseline_once(4, "cplex-cp", 2)
        self.assertEqual(solve.call_args.kwargs["TimeLimit"], 2)
        self.assertEqual(solve.call_args.kwargs["Workers"], 1)

    def test_runner_returns_sat_unsat_and_errors(self):
        # A clean interpreter must not load solver libraries just to import
        # the dispatcher or classify a missing-dependency error.
        subprocess.run([sys.executable, "-c", """
import sys
from experiments import benchmark
from src.baselines.errors import solver_error_status
assert solver_error_status(ImportError('missing package')) == 'ERROR'
for name in ('pysat', 'ortools', 'gurobipy', 'cplex', 'docplex', 'openpyxl'):
    assert name not in sys.modules, name
"""], check=True)
        for method, expected in (("sat", "pysat"), ("cp-sat", "ortools"),
                                 ("gurobi", "gurobipy"), ("cplex-mip", "cplex"),
                                 ("cplex-cp", "docplex")):
            with self.subTest(imports=method):
                subprocess.run([sys.executable, "-c", """
import sys
from importlib import import_module
from experiments import benchmark
from src.baselines.errors import solver_error_status
method, expected = sys.argv[1:]
if method == 'sat':
    benchmark.load_sat_encoder('pairwise')
    import_module('pysat.solvers')
    assert 'src.sat_encodings.binary' not in sys.modules
else:
    benchmark.load_baseline_solver(method)
assert solver_error_status(RuntimeError('License expired')) == 'LICENSE_LIMIT'
loaded = {name for name in ('pysat', 'ortools', 'gurobipy', 'cplex', 'docplex', 'openpyxl')
          if name in sys.modules}
# DOcplex CP itself imports cplex to locate the IBM runtime. This is a
# dependency of the selected library, not an import of our MIP adapter.
allowed = {expected, 'cplex'} if method == 'cplex-cp' else {expected}
assert expected in loaded and loaded <= allowed, loaded
if method == 'cplex-cp':
    assert 'src.baselines.cplex_mip' not in sys.modules
""", method, expected], check=True)
        for n, expected in ((4, "SAT"), (2, "UNSAT")):
            with self.subTest(n=n):
                self.assertEqual(benchmark.run_with_timeout(n, "sat", "pairwise", 10)["status"], expected)
        result = benchmark.run_with_timeout(4, "sat", "invalid-encoding", 10)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("KeyError", result["error"])
        # A spawned worker must use the parent's selected backend, not reload
        # the module default and silently run a different solver.
        with patch.object(sat_solver, "SAT_SOLVER", "invalid-solver"):
            result = benchmark.run_with_timeout(4, "sat", "pairwise", 10)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("NoSuchSolverError", result["error"])

    def test_all_sat_encodings_produce_correct_benchmark_results(self):
        for encoding in benchmark.SAT_ENCODERS:
            for n in (1, 2, 3, 4, 8):
                with self.subTest(encoding=encoding, n=n):
                    clauses, _ = benchmark.build_sat_nqueens(n, benchmark.load_sat_encoder(encoding))
                    with patch("pysat.solvers.Solver", wraps=import_module("pysat.solvers").Solver) as create_solver:
                        model = sat_solver.solve_cnf(clauses)
                        row = benchmark.benchmark_sat_once(n, encoding)
                    self.assertEqual(create_solver.call_count, 2)
                    for call in create_solver.call_args_list:
                        self.assertEqual(call.kwargs["name"], sat_solver.SAT_SOLVER)
                    self.assertEqual(row["status"], "SAT" if model is not None else "UNSAT")
                    self.assertEqual(row["status"], "UNSAT" if n in (2, 3) else "SAT")
                    self.assertGreaterEqual(row["total_variables"], n * n)

    def test_variable_counting_uses_pool_without_rescanning_literals(self):
        for encoding in benchmark.SAT_ENCODERS:
            encoder_type = benchmark.load_sat_encoder(encoding)
            for n in (1, 4, 8):
                with self.subTest(encoding=encoding, n=n):
                    clauses, total_variables = benchmark.build_sat_nqueens(n, encoder_type)
                    # Scan only these tiny test fixtures to independently check counts.
                    expected = max(abs(literal) for clause in clauses for literal in clause)
                    self.assertEqual(total_variables, expected)
                    with patch.object(benchmark, "abs", side_effect=AssertionError("CNF rescanned"), create=True):
                        row = benchmark.benchmark_sat_once(n, encoding)
                    self.assertEqual(row["total_variables"], expected)
                    self.assertEqual(row["auxiliary_variables"], expected - n * n)

    def test_each_execution_keeps_its_own_results_and_environment(self):
        from main import benchmark as run_benchmark

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(benchmark, "RESULTS_DIR", root), \
                 patch.object(benchmark, "N_VALUES", [4]), \
                 patch.object(benchmark, "REPETITIONS", 1), \
                 patch.object(benchmark, "benchmark_configurations", return_value=[("sat", "pairwise", sat_solver.SAT_SOLVER)]), \
                 patch.object(benchmark, "run_with_timeout", return_value={**benchmark.benchmark_sat_once(4, "pairwise"), "error": None, "peak_memory_mb": 42.0}), \
                 contextlib.redirect_stdout(io.StringIO()):
                run_benchmark()
                first_files = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                run_benchmark()
            self.assertEqual(len(list(root.glob("*/raw_results.csv"))), 2)
            self.assertEqual(len(list(root.glob("*/raw_results.xlsx"))), 2)
            self.assertEqual(len(list(root.glob("*/environment.txt"))), 2)
            for path in root.glob("*/environment.txt"):
                metadata = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
                self.assertTrue(metadata["cpu_model"].strip())
                self.assertEqual(int(metadata["physical_ram_bytes"]), benchmark.psutil.virtual_memory().total)
                self.assertAlmostEqual(float(metadata["physical_ram_gib"]),
                                       int(metadata["physical_ram_bytes"]) / 1024**3, places=3)
            for path, contents in first_files.items():
                self.assertEqual(path.read_bytes(), contents)


if __name__ == "__main__":
    unittest.main()

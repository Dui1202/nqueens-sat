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

import cplex
from gurobipy import GRB
from ortools.sat.python import cp_model

from experiments import benchmark as benchmark


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
                     patch.dict(benchmark.BASELINE_SOLVERS, {"external": ("fake", spawn_external_solver)}):
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

    def test_cp_adapters_apply_native_time_limits(self):
        solver = MagicMock()
        solver.solve.return_value = cp_model.UNKNOWN
        with patch("src.baselines.cp_sat.cp_model.CpSolver", return_value=solver):
            benchmark.benchmark_baseline_once(4, "cp-sat", 2)
        self.assertEqual(solver.parameters.max_time_in_seconds, 2)
        result = MagicMock()
        result.get_solve_status.return_value = "Unknown"
        with patch("src.baselines.cplex_cp.CpoModel.solve", return_value=result) as solve:
            benchmark.benchmark_baseline_once(4, "cplex-cp", 2)
        self.assertEqual(solve.call_args.kwargs["TimeLimit"], 2)

    def test_runner_returns_sat_unsat_and_errors(self):
        for n, expected in ((4, "SAT"), (2, "UNSAT")):
            with self.subTest(n=n):
                self.assertEqual(benchmark.run_with_timeout(n, "sat", "pairwise", 10)["status"], expected)
        result = benchmark.run_with_timeout(4, "sat", "invalid-encoding", 10)
        self.assertEqual(result["status"], "ERROR")
        self.assertIn("KeyError", result["error"])

    def test_all_sat_encodings_produce_correct_benchmark_results(self):
        for encoding in benchmark.SAT_ENCODERS:
            for n in (1, 2, 3, 4, 8):
                with self.subTest(encoding=encoding, n=n):
                    row = benchmark.benchmark_sat_once(n, encoding)
                    self.assertEqual(row["status"], "UNSAT" if n in (2, 3) else "SAT")
                    self.assertGreaterEqual(row["total_variables"], n * n)

    def test_variable_counting_is_outside_reported_time(self):
        clock = [0.0]

        def slow_abs(value):
            clock[0] += 1.0
            return abs(value)

        with patch.object(benchmark.time, "perf_counter", side_effect=lambda: clock[0]), \
             patch.object(benchmark, "abs", side_effect=slow_abs, create=True):
            row = benchmark.benchmark_sat_once(4, "pairwise")
        self.assertGreater(clock[0], 0)
        self.assertEqual(row["total_time"], 0)

    def test_each_execution_keeps_its_own_results_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(benchmark, "RESULTS_DIR", root), \
                 patch.object(benchmark, "N_VALUES", [4]), \
                 patch.object(benchmark, "REPETITIONS", 1), \
                 patch.object(benchmark, "benchmark_configurations", return_value=[("sat", "pairwise", benchmark.SAT_SOLVER)]), \
                 patch.object(benchmark, "run_with_timeout", return_value={**benchmark.benchmark_sat_once(4, "pairwise"), "error": None, "peak_memory_mb": 42.0}), \
                 contextlib.redirect_stdout(io.StringIO()):
                benchmark.main()
                first_files = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                benchmark.main()
            self.assertEqual(len(list(root.glob("*/raw_results.csv"))), 2)
            self.assertEqual(len(list(root.glob("*/raw_results.xlsx"))), 2)
            self.assertEqual(len(list(root.glob("*/environment.txt"))), 2)
            for path, contents in first_files.items():
                self.assertEqual(path.read_bytes(), contents)


if __name__ == "__main__":
    unittest.main()

"""Isolated worker execution, timeouts, process cleanup, and RSS sampling."""
from __future__ import annotations

import multiprocessing as mp
import os
import signal
import subprocess
import time
from multiprocessing.connection import Connection

import psutil

from experiments.cases import benchmark_sat_once, benchmark_baseline_once
from src import solver as sat_solver
from src.baselines.errors import solver_error_status


def worker(
    connection: Connection,
    n: int,
    method: str,
    encoding: str | None,
    timeout: float,
    solver_name: str,
) -> None:
    try:
        # Descendants (including CP Optimizer) inherit this private group.
        if os.name == "posix":
            os.setsid()
        connection.send({"ready": True})
        if method == "sat":
            assert encoding is not None
            result = benchmark_sat_once(n, encoding, solver_name)
        else:
            result = benchmark_baseline_once(n, method, timeout)
        if result["status"] == "ERROR" and result.get("error"):
            result["status"] = solver_error_status(result["error"])
        connection.send({"ok": True, "result": result})
    except Exception as exception:
        connection.send(
            {
                "ok": False,
                "status": solver_error_status(exception),
                "error": f"{type(exception).__name__}: {exception}",
            }
        )
    finally:
        connection.close()


def stop_worker(process: mp.Process, group_ready: bool) -> None:
    """Bound cleanup and stop external solvers as well as the Python worker."""
    if os.name == "posix" and group_ready:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.join(0.2)
        # Always escalate for descendants, even if their parent already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif os.name == "nt" and process.is_alive():
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        if result.returncode and process.is_alive():
            raise RuntimeError(f"Could not stop worker tree: {result.stderr!r}")
    elif process.is_alive():
        process.terminate()
    process.join(0.5)
    if process.is_alive():
        process.kill()
        process.join(1)
    if process.is_alive():
        raise RuntimeError("Could not stop benchmark worker")


def failed_result(status: str, *, total_time=None, error=None, peak_memory_mb=None) -> dict:
    return {
        "status": status,
        "original_variables": None,
        "auxiliary_variables": None,
        "total_variables": None,
        "clauses": None,
        "build_time": None,
        "solve_time": None,
        "total_time": total_time,
        "peak_memory_mb": peak_memory_mb,
        "error": error,
    }


def process_tree_rss(process: psutil.Process) -> int | None:
    """Sample resident bytes for the worker and currently observable descendants."""
    try:
        rss = process.memory_info().rss
        children = process.children(recursive=True)
        for child in children:
            try:
                rss += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                # External solvers can finish between enumeration and sampling.
                continue
        return rss
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return None


def run_with_timeout(
    n: int,
    method: str,
    encoding: str | None,
    timeout: float,
    *,
    memory_sample_interval: float = 0.01,
) -> dict:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    # Fresh interpreters avoid inheriting native solver state and make startup
    # consistent across Python versions and platforms.
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=worker,
        args=(sender, n, method, encoding, timeout, sat_solver.SAT_SOLVER),
    )
    group_ready = False
    started = False
    peak_memory_mb = None
    deadline = time.monotonic() + timeout
    try:
        process.start()
        started = True
        sender.close()
        try:
            monitored_process = psutil.Process(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            monitored_process = None
        # Read while the worker is alive so a large error cannot block its exit.
        while True:
            rss = process_tree_rss(monitored_process) if monitored_process is not None else None
            if rss is not None:
                peak_memory_mb = max(peak_memory_mb or 0, rss / 1_000_000)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not receiver.poll(min(memory_sample_interval, remaining)):
                continue
            try:
                response = receiver.recv()
            except EOFError:
                process.join(0.2)
                return failed_result(
                    "ERROR", error=f"Worker exited with code {process.exitcode}",
                    peak_memory_mb=peak_memory_mb,
                )
            if response.get("ready"):
                group_ready = True
                continue
            if not response["ok"]:
                return failed_result(
                    response["status"], error=response["error"], peak_memory_mb=peak_memory_mb,
                )
            return {"error": None, **response["result"], "peak_memory_mb": peak_memory_mb}
        return failed_result("TIMEOUT", total_time=timeout, peak_memory_mb=peak_memory_mb)
    finally:
        try:
            if started:
                process.join(0.1)
                stop_worker(process, group_ready)
        finally:
            receiver.close()
            sender.close()
            if not process.is_alive():
                process.close()

"""Isolated worker execution, timeouts, process cleanup, and RSS sampling."""
from __future__ import annotations

import multiprocessing as mp
import math
import os
import signal
import subprocess
import tempfile
import time
from multiprocessing.connection import Connection

import psutil

from experiments.cases import benchmark_sat_once, benchmark_baseline_once
from experiments.memory import apply_memory_limit, resolve_memory_limit_mb
from src import solver as sat_solver
from src.baselines.errors import solver_error_status


def worker(
    connection: Connection,
    n: int,
    method: str,
    encoding: str | None,
    timeout: float,
    solver_name: str,
    memory_limit_mb: float,
    stderr_path: str,
) -> None:
    # Reserve enough space to report an allocation failure after releasing it.
    emergency_reserve = bytearray(64 * 1024)
    oom_response = {"ok": False, "status": "OOM", "error": "Worker exhausted its memory budget"}
    try:
        # Descendants (including CP Optimizer) inherit this private group.
        if os.name == "posix":
            os.setsid()
        # Native allocators can abort without raising a Python exception.
        # Keep their diagnostics on disk, not in the controller's memory.
        with open(stderr_path, "w") as stream:
            os.dup2(stream.fileno(), 2)
        # Native dependencies otherwise create large thread pools on import,
        # spending the memory budget before a single-threaded solver starts.
        for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
                         "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ[variable] = "1"
        apply_memory_limit(memory_limit_mb)
        if method == "sat":
            assert encoding is not None
            result = benchmark_sat_once(n, encoding, solver_name)
        else:
            result = benchmark_baseline_once(n, method, timeout)
        if result["status"] == "ERROR" and result.get("error"):
            result["status"] = solver_error_status(result["error"])
        connection.send({"ok": True, "result": result})
    except MemoryError:
        del emergency_reserve
        try:
            connection.send(oom_response)
        except (MemoryError, OSError):
            os._exit(71)  # Reserved worker exit code for a confirmed OOM.
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


def stop_worker(process: mp.Process) -> None:
    """Bound cleanup and stop external solvers as well as the Python worker."""
    if os.name == "posix":
        # Target the worker's group even if it died before sending any message.
        # If setsid() has not run yet, this group does not exist.
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
    if os.name == "posix":
        # A slow-starting worker can call setsid() after the first group signals.
        # Once it is reaped it cannot create a new group or more descendants.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


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


def worker_exit_result(process: mp.Process, peak_memory_mb: float | None, stderr_path: str) -> dict:
    process.join(0.2)
    code = process.exitcode
    if code == 71:
        status, error = "OOM", "Worker could not report an out-of-memory failure"
    elif os.name == "posix" and code == -signal.SIGKILL:
        status = "WORKER_KILLED"
        error = "Worker received SIGKILL (possible OS OOM kill or external kill)"
    else:
        status, error = "ERROR", f"Worker exited with code {code} without a result"
    try:
        with open(stderr_path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 8192))
            diagnostic = stream.read(8192).decode("utf-8", errors="replace").strip()
        if diagnostic:
            if solver_error_status(diagnostic) == "OOM":
                status = "OOM"
            error += f"; stderr: {diagnostic}"
    except OSError:
        pass
    return failed_result(status, error=error, peak_memory_mb=peak_memory_mb)


def run_with_timeout(
    n: int,
    method: str,
    encoding: str | None,
    timeout: float,
    *,
    memory_sample_interval: float = 0.01,
    memory_limit_mb: float | None = None,
) -> dict:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if not math.isfinite(memory_sample_interval) or memory_sample_interval <= 0:
        raise ValueError("memory_sample_interval must be finite and positive")
    memory_limit_mb = resolve_memory_limit_mb(memory_limit_mb)
    # Fresh interpreters avoid inheriting native solver state and make startup
    # consistent across Python versions and platforms.
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    diagnostic_directory = tempfile.TemporaryDirectory(prefix="nqueens-worker-")
    stderr_path = os.path.join(diagnostic_directory.name, "stderr.log")
    process = context.Process(
        target=worker,
        args=(sender, n, method, encoding, timeout, sat_solver.SAT_SOLVER, memory_limit_mb, stderr_path),
    )
    started = False
    peak_memory_mb = None
    deadline = time.monotonic() + timeout
    result = None
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
                if rss >= memory_limit_mb * 1_000_000:
                    result = failed_result(
                        "OOM", error=f"Worker tree exceeded {memory_limit_mb:.3f} MB memory limit",
                        peak_memory_mb=peak_memory_mb,
                    )
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not receiver.poll(min(memory_sample_interval, remaining)):
                # A solver descendant may still hold the pipe open after a
                # native crash, so EOF alone is not a reliable death signal.
                if not process.is_alive():
                    result = worker_exit_result(process, peak_memory_mb, stderr_path)
                    break
                continue
            try:
                response = receiver.recv()
            except (EOFError, OSError):
                result = worker_exit_result(process, peak_memory_mb, stderr_path)
                break
            if not response["ok"]:
                result = failed_result(
                    response["status"], error=response["error"], peak_memory_mb=peak_memory_mb,
                )
            else:
                result = {"error": None, **response["result"], "peak_memory_mb": peak_memory_mb}
            break
        if result is None:
            result = failed_result("TIMEOUT", total_time=timeout, peak_memory_mb=peak_memory_mb)
    except (OSError, MemoryError) as exception:
        result = failed_result(
            solver_error_status(exception), error=f"{type(exception).__name__}: {exception}",
            peak_memory_mb=peak_memory_mb,
        )
    finally:
        try:
            if started:
                process.join(0.1)
                stop_worker(process)
        finally:
            receiver.close()
            sender.close()
            if not process.is_alive():
                process.close()
            diagnostic_directory.cleanup()
    return {**result, "memory_limit_mb": memory_limit_mb}

"""Worker memory budgets; no solver libraries are loaded in the controller."""

import math
from pathlib import Path
import sys

import psutil


def available_memory_bytes() -> int:
    """Account for host availability and visible Linux cgroup v2 ancestors."""
    available = psutil.virtual_memory().available
    if sys.platform != "linux":
        return available
    root = Path("/sys/fs/cgroup")
    try:
        membership = Path("/proc/self/cgroup").read_text()
        relative = next(line[3:] for line in membership.splitlines() if line.startswith("0::"))
        group = root / relative.lstrip("/")
        # A cgroup namespace may expose its own root instead of the host path.
        if not group.is_dir() or ".." in group.parts:
            group = root
        for directory in (group, *group.parents):
            if directory != root and root not in directory.parents:
                break
            try:
                limit = (directory / "memory.max").read_text().strip()
                if limit != "max":
                    used = int((directory / "memory.current").read_text())
                    available = min(available, max(0, int(limit) - used))
            except (OSError, ValueError):
                continue
    except (OSError, StopIteration):
        pass
    return available


def resolve_memory_limit_mb(requested: float | None = None) -> float:
    """Honor explicit budgets; reserve half available memory only in auto mode."""
    if requested is not None and (not math.isfinite(requested) or requested <= 0):
        raise ValueError("memory_limit_mb must be finite and positive")
    if requested is None:
        budget = min(4096.0, available_memory_bytes() / 2 / 1_000_000)
    else:
        budget = requested
    if sys.platform == "linux":
        import resource

        for inherited in resource.getrlimit(resource.RLIMIT_AS):
            if inherited != resource.RLIM_INFINITY:
                budget = min(budget, inherited / 1_000_000)
    return budget


def apply_memory_limit(memory_limit_mb: float) -> None:
    """Linux allocation ceiling, inherited by external solver processes.

    RLIMIT_AS limits address space, not RSS. Other platforms use the parent's
    sampled RSS watchdog only. Never change limits in the controller.
    """
    if sys.platform == "linux":
        import resource

        limit = int(memory_limit_mb * 1_000_000)
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        for inherited in (soft, hard):
            if inherited != resource.RLIM_INFINITY:
                limit = min(limit, inherited)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

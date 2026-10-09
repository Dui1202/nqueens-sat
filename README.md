# N-Queens SAT benchmark

Run from the repository root with the project's Python environment:

```sh
.venv/bin/python main.py
.venv/bin/python -m unittest discover -s tests -v
```

`main.benchmark()` runs the full benchmark. The existing
`.venv/bin/python -m experiments.benchmark` entry point is also supported.

All solvers use one search worker/thread: CP-SAT sets `num_search_workers=1`,
Gurobi sets `Threads=1`, CPLEX MIP sets `threads=1`, and CP Optimizer sets
`Workers=1`. The PySAT CaDiCaL backend is sequential. This policy applies to
both warm-up and measured runs and is recorded as `solver_threads=1` in
`environment.txt`.

The benchmark uses `python-sat`, `ortools`, `gurobipy`, `cplex`, and
`docplex`, plus `openpyxl` for Excel export and `psutil` for memory sampling
(`.venv/bin/python -m pip install -r requirements.txt`). Commercial solvers also need their runtime and license; CP Optimizer
needs its executable available to DOcplex. Configure board sizes, repetitions,
and the timeout at the top of `experiments/benchmark.py`.
The shared SAT backend is configured by `SAT_SOLVER` in `src/solver.py`
(currently `cadical195`). Both direct solves and benchmarks call the same
`solve_cnf()` implementation, including solver initialization and model extraction.
Benchmark workers receive the selected backend explicitly, and CSV/environment
metadata records that same selection.

Each execution creates `results/<UTC-timestamp>-<unique-id>/`, containing
`raw_results.csv`, `raw_results.xlsx`, and `environment.txt`. The metadata records the board sizes,
timeout, repetitions, platform, CPU model (`cpu_model`), physical RAM reported
by the operating system (`physical_ram_bytes` and `physical_ram_gib`), Python
version, and solver package versions. CPU model detection reads the platform's
hardware information, with `platform.processor()` as a fallback; if neither is
available, it records `unknown`.
Previous executions are preserved in their own directories.

For each board size and solver/encoding configuration, one warm-up run precedes
the five measured runs. Warm-ups use the same timeout and validation but are
printed only; CSV and Excel contain measured runs numbered 1 through 5.
`WARMUP_RUNS` and `REPETITIONS` control these counts. Each invocation, including
the warm-up, uses a fresh worker and solver instance, so the warm-up can prime
system caches but does not reuse solver state. A failed warm-up is reported and
does not suppress the measured runs.

After the benchmark finishes, it reads `raw_results.csv` to create
`raw_results.xlsx` in the same directory. The workbook contains a filterable
**Raw results** sheet with every CSV row, numeric counts and timings (seconds),
blank cells for missing values, and frozen headers. Excel export runs outside
all benchmark timing measurements. The workbook is a snapshot of the CSV.

To convert an existing CSV, or refresh its workbook after editing the CSV:

```sh
.venv/bin/python -m experiments.export_xlsx results/<experiment-id>/raw_results.csv
```

This recreates the adjacent `.xlsx` file and leaves the CSV unchanged.

## Results and timing

- `SAT`: a placement was found and independently verified.
- `UNSAT`: the solver proved infeasibility.
- `UNKNOWN`: the solver stopped without a placement or proof of infeasibility.
- `TIMEOUT`: the worker exceeded its wall-clock budget, or a baseline explicitly
  reported a time limit without a placement.
- `LICENSE_LIMIT`: a missing, expired, or restricted solver license prevented
  the run, including Community Edition problem-size limits. The original
  diagnostic remains in the `error` column.
- `ERROR`: an invalid model, solver failure, exception, or invalid placement.

Baseline adapters return `SolveResult(status, solution, error)` and accept an
optional timeout in seconds. A feasible incumbent is sufficient for `SAT` in
this feasibility benchmark. Inconclusive results are never treated as `UNSAT`.
Status mappings follow the solver APIs, including [OR-Tools statuses](https://or-tools.github.io/docs/pdoc/ortools/sat/python/cp_model),
[Gurobi statuses](https://docs.gurobi.com/projects/optimizer/en/current/reference/numericcodes/statuscodes.html),
and [DOcplex solve statuses](https://ibmdecisionoptimization.github.io/docplex-doc/2.20.204/cp/_modules/docplex/cp/solution.html).

`total_time` measures model construction, solving, and placement extraction.
Verification and diagnostic counting are excluded. For SAT, `build_time` covers
CNF construction; `solve_time` includes solver initialization, loading clauses,
search, model extraction, solver cleanup, and conversion to queen columns.
Baselines expose only `total_time` because they build and solve internally.

`build_sat_nqueens()` returns `(clauses, total_variables)`. The variable count
comes directly from `IDPool.top` in O(1); the benchmark does not scan the CNF
again to find its largest variable ID. The pool reserves the first `n²` IDs for
board variables, so auxiliary variables equal `total_variables - n²`.

`peak_memory_mb` is the largest sampled sum of resident memory (RSS) of the
worker and its external solver descendants, in decimal MB (1 MB = 1,000,000
bytes). The controller samples approximately every 10 ms throughout the worker
lifetime, including imports, construction, solving, and verification. It excludes
the controller and any separate warm-up run. Shared pages may be counted in more
than one process, and short-lived peaks between samples may be missed. This is
a sampled process-memory peak, not a Python-only allocation measurement or a
baseline-subtracted solver allocation. Error and timeout rows retain the peak
observed before stopping; an unavailable measurement stays blank. The sampling
interval and metric definition are saved in `environment.txt`.

The hard timeout covers the entire worker, including interpreter startup,
verification, and diagnostics; it is broader than `total_time`. For a hard
timeout, `total_time` records the configured budget, not a completed solve time.
Keep timeout rows separate when calculating successful-run timing statistics.

Workers use fresh Python interpreters. Baselines receive native solver time
limits as well. On POSIX, workers and external solver descendants share a private
process group that is terminated and then killed during cleanup. Windows uses
`taskkill /T /F` to stop a live worker and its descendants. Cleanup has bounded
waits and may take a short additional interval beyond the benchmark budget.

Solver imports are lazy: each worker loads only its selected solver library
and that library's dependencies. SAT workers load only their selected encoder.
The dispatcher, shared SAT configuration, and license-error classifier do not
import solver libraries. Excel export stays in the controller. Solver imports
occur before the build/solve timers, but remain inside the hard timeout and
memory-sampling window. Thus memory includes the Python worker and its selected
backend rather than all installed solver libraries. Results from before this
change include a different import overhead and should be compared accordingly.
DOcplex CP may itself import `cplex` to locate the IBM runtime; that transitive
dependency remains part of the CP Optimizer worker's memory footprint.

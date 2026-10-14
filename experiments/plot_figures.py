"""Generate four report figures from one benchmark CSV (no solver runs)."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import ScalarFormatter
from matplotlib.transforms import offset_copy
import pandas as pd


ENCODINGS = {
    "pairwise": "Pairwise",
    "binary": "Binary",
    "sequential": "Sequential counter",
    "commander": "Commander",
    "product": "Product",
}
COLORS = dict(zip(ENCODINGS, ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00"]))
MARKERS = dict(zip(ENCODINGS, ["o", "s", "D", "P", "v"]))
BASELINES = {"cp-sat": "CP-SAT", "cplex-mip": "CPLEX MIP", "cplex-cp": "CPLEX CP"}


def read_results(path: Path, repetitions: int) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"n", "method", "encoding", "solver", "run", "status", "clauses",
                "total_time", "peak_memory_mb", "memory_limit_mb"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    df["encoding"] = df["encoding"].fillna("")
    for column in ("n", "run", "clauses", "total_time", "peak_memory_mb", "memory_limit_mb"):
        df[column] = pd.to_numeric(df[column], errors="raise")
    if df.empty or df["n"].isna().any() or not (df["n"] > 0).all():
        raise ValueError("Input must contain positive board sizes.")
    if not df["run"].isin(range(1, repetitions + 1)).all():
        raise ValueError(f"Expected measured repetitions numbered 1 through {repetitions}.")
    if df.duplicated(["n", "method", "encoding", "run"]).any():
        raise ValueError("Duplicate runs: use one experiment and one solver per method.")
    if (df.groupby("method")["solver"].nunique() > 1).any():
        raise ValueError("Multiple solvers for one method would mix incompatible timings.")
    unknown = set(df.loc[df["method"].eq("sat"), "encoding"]) - ENCODINGS.keys()
    if unknown:
        raise ValueError(f"Unknown SAT encodings: {sorted(unknown)}")
    success = df.loc[df["status"].eq("SAT"), "total_time"]
    if success.isna().any() or not success.between(0, float("inf"), inclusive="neither").all():
        raise ValueError("Successful runs require finite, positive total_time.")
    return df


def new_figure(n_values, title, ylabel):
    fig, ax = plt.subplots(figsize=(9.4, 6.2))
    fig.subplots_adjust(left=0.12, right=0.97, bottom=0.27, top=0.89)
    ax.set(title=title, xlabel="Board size, N", ylabel=ylabel)
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks(n_values)
    # Keep the entire tested range visible, including sizes with only failures.
    ax.set_xlim(n_values[0] / 1.25, n_values[-1] * 1.3)
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.minorticks_off()
    ax.grid(which="major", alpha=0.22)
    ax.spines[["top", "right"]].set_visible(False)
    return fig, ax


def save_figure(fig, ax, output, name, note, *, ncol=3):
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=ncol,
              frameon=False, fontsize=9)
    fig.text(0.12, 0.025, note, fontsize=8.5, color="#444444", va="bottom")
    for extension in ("png", "pdf"):
        path = output / f"{name}.{extension}"
        fig.savefig(path, dpi=300, facecolor="white")
        print(path)
    plt.close(fig)


def plot_encodings(ax, data, n_values, value):
    # Reindex onto every tested N so missing measurements break the line.
    for encoding, label in ENCODINGS.items():
        series = data.loc[data["encoding"].eq(encoding)].set_index("n")[value]
        ax.plot(n_values, series.reindex(n_values), label=label,
                color=COLORS[encoding], marker=MARKERS[encoding], markersize=5)


def mark_partial_means(ax, data, value, repetitions):
    for row in data.loc[data["sat_runs"] < repetitions].to_dict("records"):
        ax.annotate(f'{row["sat_runs"]}/{repetitions}', (row["n"], row[value]),
                    xytext=(5, 7), textcoords="offset points", fontsize=8)


def generate(csv_path: Path, output: Path, repetitions: int = 5) -> None:
    df = read_results(csv_path, repetitions)
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 11, "axes.titlesize": 14, "lines.linewidth": 1.8,
                         "pdf.fonttype": 42})
    n_values = sorted(df["n"].unique())
    sat = df.loc[df["method"].eq("sat")]
    groups = ["method", "encoding", "n"]
    success = df.loc[df["status"].eq("SAT")]
    runtime = success.groupby(groups, as_index=False).agg(
        mean_total_time_s=("total_time", "mean"), sat_runs=("run", "nunique"))
    runtime.to_csv(output / "successful_runtime_summary.csv", index=False)
    df.groupby(groups + ["status"]).size().rename("runs").to_csv(output / "status_counts.csv")
    sat_runtime = runtime.loc[runtime["method"].eq("sat")]
    partial = runtime.loc[runtime["sat_runs"] < repetitions]
    if not partial.empty:
        print("Partial successful groups (labels show successful/expected repetitions):")
        print(partial.to_string(index=False))

    # CNF size is deterministic. Keep measured counts only; never fill failed runs.
    clause_groups = sat.groupby(["encoding", "n"])["clauses"]
    if (clause_groups.nunique() > 1).any():
        raise ValueError("Clause counts differ across repetitions for the same encoding/N.")
    clauses = clause_groups.first().rename("clauses").reset_index()
    clauses.to_csv(output / "clause_summary.csv", index=False)
    fig, ax = new_figure(n_values, "CNF clause growth", "CNF clauses (log scale)")
    plot_encodings(ax, clauses, n_values, "clauses")
    save_figure(fig, ax, output, "01_cnf_clause_growth",
                "Measured CNF sizes; identical counts across repetitions are shown once.\n"
                "Missing counts after OOM/timeout remain missing; no extrapolation. X-axis: log2.")

    fig, ax = new_figure(n_values, "SAT runtime by encoding",
                         "Mean total runtime (s, log scale)")
    plot_encodings(ax, sat_runtime, n_values, "mean_total_time_s")
    mark_partial_means(ax, sat_runtime, "mean_total_time_s", repetitions)
    save_figure(fig, ax, output, "02_sat_runtime",
                f"Arithmetic mean of status == SAT runs ({repetitions} repetitions scheduled per group).\n"
                "total_time includes build + solve + extraction; OOM/timeout excluded.\n"
                f"Partial means are labelled k/{repetitions}; absent successful groups leave gaps. X-axis: log2.")

    # Minimize encoding-level means, not individual repetitions; ties use CSV order.
    envelope = sat_runtime.loc[
        sat_runtime.groupby("n")["mean_total_time_s"].idxmin()].copy()
    available = sat_runtime.groupby("n")["encoding"].nunique()
    envelope["available_encodings"] = envelope["n"].map(available)
    envelope.rename(columns={"encoding": "best_encoding"}).to_csv(
        output / "sat_envelope.csv", index=False)
    fig, ax = new_figure(n_values, "Cross-paradigm runtime",
                         "Mean total runtime (s, log scale)")
    ax.plot(n_values, envelope.set_index("n")["mean_total_time_s"].reindex(n_values),
            color="#222222", marker="o", linewidth=2.4, label="SAT: best-observed envelope")
    mark_partial_means(ax, envelope, "mean_total_time_s", repetitions)
    for method, color, marker in zip(BASELINES, ["#0072B2", "#D55E00", "#009E73"], ["s", "D", "^"]):
        points = runtime.loc[runtime["method"].eq(method)]
        ax.plot(n_values, points.set_index("n")["mean_total_time_s"].reindex(n_values),
                color=color, marker=marker, label=BASELINES[method])
        mark_partial_means(ax, points, "mean_total_time_s", repetitions)
    censored = df.loc[df["method"].eq("gurobi") & df["status"].eq("LICENSE_LIMIT"), "n"]
    license_note = "Gurobi omitted"
    if not censored.empty:
        license_note += "; license-censored at N = " + ", ".join(map(str, sorted(censored.unique())))
    coverage = ", ".join(f"{n}: {int(available.get(n, 0))}/5" for n in n_values)
    save_figure(fig, ax, output, "03_cross_paradigm_runtime",
                "Envelope = min of encoding-level SAT-only means; selected post-hoc at each N.\n"
                f"Available SAT encodings (N: count): {coverage}.\n"
                f"{license_note}. X-axis: log2.", ncol=2)

    # Failed-run RSS is an observed peak before termination, not memory to completion.
    memory = sat.groupby(["encoding", "n", "status"], as_index=False).agg(
        mean_peak_memory_mb=("peak_memory_mb", "mean"), measured_runs=("peak_memory_mb", "count"))
    memory.to_csv(output / "sat_memory_summary.csv", index=False)
    fig, ax = new_figure(n_values, "SAT peak memory and failure boundaries",
                         "Mean sampled peak RSS (MB, log scale)")
    plot_encodings(ax, memory.loc[memory["status"].eq("SAT")], n_values, "mean_peak_memory_mb")
    for status, marker in (("OOM", "X"), ("TIMEOUT", "^")):
        failed = memory.loc[memory["status"].eq(status)]
        for offset, encoding in zip((-16, -8, 0, 8, 16), ENCODINGS):
            points = failed.loc[failed["encoding"].eq(encoding)]
            ax.scatter(points["n"], points["mean_peak_memory_mb"], color=COLORS[encoding],
                       marker=marker, s=85, edgecolors="white", linewidths=0.6, zorder=5,
                       transform=offset_copy(ax.transData, fig=fig, x=offset, units="points"))
    limits = sat["memory_limit_mb"].dropna().unique()
    if len(limits) == 1 and limits[0] > 0:
        ax.axhline(limits[0], color="#666666", linestyle="--", linewidth=1)
        ax.annotate(f"Configured limit: {limits[0]:g} MB", (n_values[0], limits[0]),
                    xytext=(0, 5), textcoords="offset points", fontsize=9)
        ax.set_ylim(top=max(limits[0], memory["mean_peak_memory_mb"].max()) * 1.6)
    status_legend = ax.legend(handles=[
        Line2D([], [], color="#555555", marker="o", label="SAT (line)"),
        Line2D([], [], color="#555555", marker="X", linestyle="none", label="OOM"),
        Line2D([], [], color="#555555", marker="^", linestyle="none", label="Timeout"),
    ], loc="upper left", bbox_to_anchor=(0.01, 0.86), fontsize=8, frameon=True)
    ax.add_artist(status_legend)
    save_figure(fig, ax, output, "04_sat_peak_memory",
                "Mean peak RSS by encoding, N and status; worker + descendants (decimal MB). X-axis: log2.\n"
                "Failure peaks measured before termination; markers offset horizontally for visibility.\n"
                "OOM may occur below RSS limit (RLIMIT_AS); failed peaks do not estimate memory to completion.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="One experiment's raw_results.csv")
    parser.add_argument("--output-dir", type=Path, help="Default: <CSV directory>/figures")
    parser.add_argument("--repetitions", type=int, default=5, help="Expected measured runs per group")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    generate(args.csv, args.output_dir or args.csv.parent / "figures", args.repetitions)


if __name__ == "__main__":
    main()

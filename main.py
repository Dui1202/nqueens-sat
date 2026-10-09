"""Run the N-Queens benchmark from the project root."""

import multiprocessing as mp


def benchmark() -> None:
    from experiments.benchmark import main as run_benchmark

    run_benchmark()


if __name__ == "__main__":
    mp.freeze_support()
    benchmark()

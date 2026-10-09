from itertools import combinations
from math import ceil, sqrt

from pysat.formula import IDPool


class ProductEncoder:
    def __init__(self, vpool: IDPool):
        self._vpool = vpool

    def at_most_one(
        self,
        literals: list[int],
    ) -> list[list[int]]:
        n = len(literals)

        if n <= 1:
            return []

        p = ceil(sqrt(n))
        q = ceil(n / p)

        rows = [self._vpool.id() for _ in range(p)]

        cols = [self._vpool.id() for _ in range(q)]

        clauses: list[list[int]] = []

        clauses.extend(self._pairwise_at_most_one(rows))

        clauses.extend(self._pairwise_at_most_one(cols))

        for i, x in enumerate(literals):
            row = i // q
            col = i % q

            clauses.append([-x, rows[row]])
            clauses.append([-x, cols[col]])

        return clauses

    def at_least_one(self, literals: list[int]) -> list[list[int]]:
        return [literals]

    def exactly_one(self, literals: list[int]) -> list[list[int]]:
        return self.at_least_one(literals) + self.at_most_one(literals)

    @staticmethod
    def _pairwise_at_most_one(
        literals: list[int],
    ) -> list[list[int]]:
        return [[-x, -y] for x, y in combinations(literals, 2)]

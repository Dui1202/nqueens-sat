from pysat.formula import IDPool


class SequentialEncoder:
    def __init__(self, vpool: IDPool):
        self.vpool = vpool

    def at_most_one(self, literals: list[int]) -> list[list[int]]:
        n = len(literals)

        if n <= 1:
            return []

        s = [self.vpool.id() for _ in range(n - 1)]

        clauses: list[list[int]] = []

        clauses.append([-literals[0], s[0]])

        for i in range(1, n - 1):
            clauses.append([-literals[i], s[i]])

            clauses.append([-s[i - 1], s[i]])

            clauses.append([-s[i - 1], -literals[i]])

        clauses.append([-s[n - 2], -literals[n - 1]])

        return clauses

    def at_least_one(self, literals: list[int]) -> list[list[int]]:
        return [list(literals)]

    def exactly_one(self, literals: list[int]) -> list[list[int]]:
        return self.at_least_one(literals) + self.at_most_one(literals)

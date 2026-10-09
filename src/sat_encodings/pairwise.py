from pysat.formula import IDPool


class PairWiseEncoder:
    def __init__(self, vpool: IDPool):
        self.vpool = vpool

    def at_least_one(self, variables: list[int]) -> list[list[int]]:
        return [variables]

    def at_most_one(self, variables: list[int]) -> list[list[int]]:
        clauses: list[list[int]] = []
        for i in range(len(variables)):
            for j in range(i + 1, len(variables)):
                clauses.append([-variables[i], -variables[j]])
        return clauses

    def exactly_one(self, variables: list[int]) -> list[list[int]]:
        return self.at_least_one(variables) + self.at_most_one(variables)

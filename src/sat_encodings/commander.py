from pysat.formula import IDPool


class CommanderEncoder:
    def __init__(self, vpool: IDPool, group_size: int = 3):
        if group_size < 2:
            raise ValueError("group_size must be at least 2")

        self.vpool = vpool
        self.group_size = group_size

    def at_least_one(self, literals: list[int]) -> list[list[int]]:
        return [literals]

    def at_most_one(self, literals: list[int]) -> list[list[int]]:
        if len(literals) <= 1:
            return []

        if len(literals) <= self.group_size:
            return self._pairwise_at_most_one(literals)

        clauses: list[list[int]] = []

        groups = [
            literals[i : i + self.group_size]
            for i in range(0, len(literals), self.group_size)
        ]

        commanders = [self.vpool.id() for _ in groups]

        for group, commander in zip(groups, commanders):
            clauses.extend(self._pairwise_at_most_one(group))

            for literal in group:
                clauses.append([-literal, commander])

            clauses.append([-commander, *group])

        clauses.extend(self._pairwise_at_most_one(commanders))

        return clauses

    def exactly_one(self, literals: list[int]) -> list[list[int]]:
        return self.at_least_one(literals) + self.at_most_one(literals)

    @staticmethod
    def _pairwise_at_most_one(
        literals: list[int],
    ) -> list[list[int]]:
        clauses: list[list[int]] = []

        for i in range(len(literals)):
            for j in range(i + 1, len(literals)):
                clauses.append(
                    [
                        -literals[i],
                        -literals[j],
                    ]
                )

        return clauses

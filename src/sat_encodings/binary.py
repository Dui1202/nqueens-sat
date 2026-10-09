from pysat.formula import IDPool


class BinaryEncoder:
    def __init__(self, vpool: IDPool):
        self._vpool = vpool
        self._constraint_id = 0

    def at_most_one(self, xs: list[int]) -> list[list[int]]:
        n = len(xs)

        if n <= 1:
            return []

        num_bits = (n - 1).bit_length()

        auxiliary_bits = []

        for bit in range(num_bits):
            new_variable = self._vpool.id()
            auxiliary_bits.append(new_variable)

        clauses = []

        for code, x in enumerate(xs):
            for bit_index in range(num_bits):
                y = auxiliary_bits[bit_index]

                bit_value = (code >> bit_index) & 1

                if bit_value == 1:
                    clauses.append([-x, y])
                else:
                    clauses.append([-x, -y])

        return clauses

    def at_least_one(self, literals: list[int]) -> list[list[int]]:
        return [list(literals)]

    def exactly_one(self, literals: list[int]) -> list[list[int]]:
        xs = list(literals)

        return self.at_least_one(xs) + self.at_most_one(xs)

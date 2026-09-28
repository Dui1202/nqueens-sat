def at_least_one(variables: list[int]) -> list[list[int]]:
    return [variables]


def at_most_one(variables: list[int]) -> list[list[int]]:
    clauses: list[list[int]] = []
    for i in range(len(variables)):
        for j in range(i + 1, len(variables)):
            clauses.append([-variables[i], -variables[j]])
    return clauses


def exactly_one(variables: list[int]) -> list[list[int]]:
    return at_least_one(variables) + at_most_one(variables)

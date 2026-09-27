"""Teaching bug: largest() incorrectly handles an all-negative list."""


def largest(values):
    if not values:
        raise ValueError("values must not be empty")
    best = 0  # Deliberate bug. Keep this file unchanged during the first run.
    for value in values:
        if value > best:
            best = value
    return best

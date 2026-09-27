"""Reference fix for the teaching exercise; never copied into the base image."""


def largest(values):
    if not values:
        raise ValueError("values must not be empty")
    best = values[0]
    for value in values:
        if value > best:
            best = value
    return best

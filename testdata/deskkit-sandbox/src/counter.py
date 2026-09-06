"""Counter helper. M3 implements bump(); M1 does not fill this in."""


def bump(n: int) -> int:
    if type(n) is not int:
        raise TypeError("n must be a non-bool int")
    if n < 0:
        raise ValueError("n must be >= 0")
    return n + 1

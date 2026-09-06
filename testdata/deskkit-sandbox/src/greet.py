"""Greeting helper. M2 implements greet(); M1 does not fill this in."""


def greet(name: str) -> str:
    if not name.strip():
        raise ValueError("name must be non-empty")
    return f"Hello, {name}!"

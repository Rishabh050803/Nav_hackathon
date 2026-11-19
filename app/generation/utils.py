"""Shared normalization and supported generation difficulties."""
Difficulties = ("easy", "medium", "hard")


def normalize(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


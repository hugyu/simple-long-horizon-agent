"""Completion verdict shared by durable runs and in-process workflows."""

from collections.abc import Callable
from dataclasses import dataclass

from .state import State


@dataclass(frozen=True)
class CompletionResult:
    """A caller-owned verifier's verdict, independent of model final text."""

    done: bool
    blocked: bool = False
    reason: str = ""


CompletionCheck = Callable[[State], CompletionResult]

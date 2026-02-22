"""
Math Operations
Core mathematical operations for the math agent
"""

import re
import operator
from typing import Any


OPERATIONS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "%": operator.mod,
    "**": operator.pow,
}


def evaluate_expression(expression: str) -> float:
    """
    Safely evaluate a simple math expression.
    Supports: +, -, *, /, //, %, **
    Handles parentheses and order of operations.
    
    Args:
        expression: Mathematical expression as string
        
    Returns:
        Result of the evaluation
        
    Raises:
        ValueError: If expression contains invalid characters or cannot be evaluated
    """
    # Sanitize: only allow digits, operators, parentheses, whitespace, and decimal points
    sanitized = expression.strip()
    if not re.match(r'^[\d\s\+\-\*/%\.\(\)]+$', sanitized):
        raise ValueError(f"Invalid characters in expression: {sanitized}")

    # Use Python's eval with restricted builtins for safety
    allowed_names: dict[str, Any] = {"__builtins__": {}}
    try:
        result = eval(sanitized, allowed_names)  # noqa: S307
    except (SyntaxError, TypeError, NameError) as e:
        raise ValueError(f"Could not evaluate expression '{sanitized}': {e}") from e

    return float(result)


def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


def subtract(a: float, b: float) -> float:
    """Subtract b from a."""
    return a - b


def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b


def divide(a: float, b: float) -> float:
    """Divide a by b. Raises ValueError if b is zero."""
    if b == 0:
        raise ValueError("Cannot divide by zero.")
    return a / b


def power(a: float, b: float) -> float:
    """Raise a to the power of b."""
    return a ** b


def modulo(a: float, b: float) -> float:
    """Return the remainder of a divided by b."""
    if b == 0:
        raise ValueError("Cannot perform modulo by zero.")
    return a % b

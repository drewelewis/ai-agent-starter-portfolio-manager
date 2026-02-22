"""
Math Tools for Microsoft Agent Framework
Provides typed tool functions for mathematical operations
"""

from typing import Annotated
from operations.math_operations import (
    add as math_add,
    subtract as math_subtract,
    multiply as math_multiply,
    divide as math_divide,
    power as math_power,
    modulo as math_modulo,
    evaluate_expression as math_evaluate
)


def add(
    a: Annotated[float, "The first number"],
    b: Annotated[float, "The second number"]
) -> str:
    """
    Add two numbers together.
    
    Args:
        a: The first number
        b: The second number
        
    Returns:
        The sum formatted as a string
    """
    try:
        result = math_add(a, b)
        return f"{a} + {b} = {result}"
    except Exception as e:
        return f"Error adding {a} and {b}: {str(e)}"


def subtract(
    a: Annotated[float, "The number to subtract from"],
    b: Annotated[float, "The number to subtract"]
) -> str:
    """
    Subtract b from a.
    
    Args:
        a: The number to subtract from
        b: The number to subtract
        
    Returns:
        The difference formatted as a string
    """
    try:
        result = math_subtract(a, b)
        return f"{a} - {b} = {result}"
    except Exception as e:
        return f"Error subtracting {b} from {a}: {str(e)}"


def multiply(
    a: Annotated[float, "The first number"],
    b: Annotated[float, "The second number"]
) -> str:
    """
    Multiply two numbers together.
    
    Args:
        a: The first number
        b: The second number
        
    Returns:
        The product formatted as a string
    """
    try:
        result = math_multiply(a, b)
        return f"{a} × {b} = {result}"
    except Exception as e:
        return f"Error multiplying {a} and {b}: {str(e)}"


def divide(
    a: Annotated[float, "The dividend (number to be divided)"],
    b: Annotated[float, "The divisor (number to divide by)"]
) -> str:
    """
    Divide a by b.
    
    Args:
        a: The dividend (number to be divided)
        b: The divisor (number to divide by)
        
    Returns:
        The quotient formatted as a string
    """
    try:
        result = math_divide(a, b)
        return f"{a} ÷ {b} = {result}"
    except Exception as e:
        return f"Error dividing {a} by {b}: {str(e)}"


def power(
    a: Annotated[float, "The base number"],
    b: Annotated[float, "The exponent"]
) -> str:
    """
    Raise a to the power of b.
    
    Args:
        a: The base number
        b: The exponent
        
    Returns:
        The result formatted as a string
    """
    try:
        result = math_power(a, b)
        return f"{a}^{b} = {result}"
    except Exception as e:
        return f"Error calculating {a} to the power of {b}: {str(e)}"


def modulo(
    a: Annotated[float, "The dividend"],
    b: Annotated[float, "The divisor"]
) -> str:
    """
    Calculate the remainder of a divided by b.
    
    Args:
        a: The dividend
        b: The divisor
        
    Returns:
        The remainder formatted as a string
    """
    try:
        result = math_modulo(a, b)
        return f"{a} mod {b} = {result}"
    except Exception as e:
        return f"Error calculating {a} modulo {b}: {str(e)}"


def evaluate_expression(
    expression: Annotated[str, "A mathematical expression like '2 + 3 * 4' or '(10 - 5) / 2'"]
) -> str:
    """
    Evaluate a complex mathematical expression.
    Supports: +, -, *, /, parentheses, and order of operations.
    
    Args:
        expression: A mathematical expression as a string
        
    Returns:
        The result of evaluating the expression
        
    Examples:
        - "2 + 3 * 4" returns 14
        - "(10 - 5) * 2" returns 10
        - "100 / 4 + 3" returns 28
    """
    try:
        result = math_evaluate(expression)
        return f"{expression} = {result}"
    except Exception as e:
        return f"Error evaluating expression '{expression}': {str(e)}"

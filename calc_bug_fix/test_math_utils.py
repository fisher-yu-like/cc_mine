"""pytest tests for math_utils."""
import pytest
import math_utils


def test_normal_division():
    assert math_utils.divide_elements(10, 2) == 5.0


def test_zero_denominator_raises_valueerror():
    with pytest.raises(ValueError, match="zero"):
        math_utils.divide_elements(10, 0)


def test_negative_numbers():
    assert math_utils.divide_elements(-10, 2) == -5.0

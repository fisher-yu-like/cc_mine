def divide_elements(numerator: float, denominator: float) -> float:
    """将两个数相除，需要防御分母为零的情况。"""
    if denominator == 0:
        raise ValueError("Denominator must not be zero.")
    return numerator / denominator
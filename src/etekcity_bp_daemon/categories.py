"""American Heart Association blood-pressure category classification."""

from __future__ import annotations

CRISIS = "Hypertensive Crisis"
STAGE_2 = "Stage 2"
STAGE_1 = "Stage 1"
ELEVATED = "Elevated"
NORMAL = "Normal"


def classify(systolic_mmhg: int | None, diastolic_mmhg: int | None) -> str | None:
    """Classify a reading per the AHA's blood pressure categories.

    See heart.org's "Understanding Blood Pressure Readings" for the
    reference thresholds this follows.

    Args:
        systolic_mmhg: Systolic pressure in mmHg.
        diastolic_mmhg: Diastolic pressure in mmHg.

    Returns:
        One of NORMAL, ELEVATED, STAGE_1, STAGE_2, CRISIS, or None if either
        value is missing.
    """
    if systolic_mmhg is None or diastolic_mmhg is None:
        return None
    if systolic_mmhg > 180 or diastolic_mmhg > 120:
        return CRISIS
    if systolic_mmhg >= 140 or diastolic_mmhg >= 90:
        return STAGE_2
    if systolic_mmhg >= 130 or diastolic_mmhg >= 80:
        return STAGE_1
    if systolic_mmhg >= 120:
        return ELEVATED
    return NORMAL

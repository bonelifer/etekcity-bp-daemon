from etekcity_bp_daemon.categories import CRISIS, ELEVATED, NORMAL, STAGE_1, STAGE_2, classify


def test_normal():
    assert classify(115, 75) == NORMAL


def test_elevated():
    assert classify(125, 70) == ELEVATED


def test_stage_1_by_systolic():
    assert classify(135, 75) == STAGE_1


def test_stage_1_by_diastolic():
    assert classify(125, 85) == STAGE_1


def test_stage_2_by_systolic():
    assert classify(145, 75) == STAGE_2


def test_stage_2_by_diastolic():
    assert classify(120, 95) == STAGE_2


def test_crisis_by_systolic():
    assert classify(185, 75) == CRISIS


def test_crisis_by_diastolic():
    assert classify(120, 125) == CRISIS


def test_missing_values_return_none():
    assert classify(None, 75) is None
    assert classify(120, None) is None

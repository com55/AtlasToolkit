from atlas_toolkit.domain.region_validation import validate_region_name


def test_trims_before_validating_and_returns_trimmed_value():
    r = validate_region_name("  arm  ", {"leg"})
    assert r.ok is True
    assert r.value == "arm"


def test_rejects_blank_including_all_whitespace():
    assert validate_region_name("", set()).ok is False
    assert validate_region_name("   ", set()).ok is False


def test_rejects_embedded_newline_or_carriage_return():
    assert validate_region_name("arm\nleg", set()).ok is False
    assert validate_region_name("arm\rleg", set()).ok is False


def test_rejects_a_colon():
    assert validate_region_name("arm:leg", set()).ok is False


def test_rejects_trailing_png_trimmed_first():
    assert validate_region_name("foo.png", set()).ok is False
    assert validate_region_name("foo.png ", set()).ok is False


def test_rejects_a_literal_hash():
    assert validate_region_name("arm#2", set()).ok is False


def test_rejects_collision_with_another_effective_display_name_compared_trimmed():
    r = validate_region_name(" arm ", {"arm", "leg"})
    assert r.ok is False
    assert "already in use" in r.reason.lower()


def test_does_not_reject_the_empty_collision_case():
    assert validate_region_name("arm", set()).ok is True


def test_check_order_blank_wins_over_collision():
    r = validate_region_name("   ", {""})
    assert r.ok is False
    assert "blank" in r.reason.lower()


def test_check_order_newline_wins_over_colon():
    r = validate_region_name("arm\nleg:foot", set())
    assert r.ok is False
    assert "line break" in r.reason.lower()


def test_check_order_colon_wins_over_trailing_png():
    r = validate_region_name("arm:leg.png", set())
    assert r.ok is False
    assert ":" in r.reason


def test_check_order_trailing_png_wins_over_hash():
    r = validate_region_name("arm#2.png", set())
    assert r.ok is False
    assert ".png" in r.reason.lower()


def test_check_order_hash_wins_over_collision():
    r = validate_region_name("arm#2", {"arm#2"})
    assert r.ok is False
    assert "#" in r.reason

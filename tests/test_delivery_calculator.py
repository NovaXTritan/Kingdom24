from services import delivery_calculator as dc


def test_noida_free_above_2000():
    cost = dc.calculate("Noida", weight_kg=30, is_frozen=True, order_value=5000)
    assert cost.shipping == 0
    assert cost.packaging == 800


def test_noida_below_threshold():
    cost = dc.calculate("Noida", weight_kg=10, is_frozen=False, order_value=1500)
    assert cost.shipping == 150
    assert cost.packaging == 0


def test_metro_minimum_30kg_billable():
    # 12kg actual, but billed at 30kg minimum
    cost = dc.calculate("Mumbai", weight_kg=12, is_frozen=True, order_value=2500)
    assert cost.shipping == 30 * 35
    assert cost.packaging == 800


def test_metro_batched_60kg():
    # 45kg → bills 2 × 30kg packs = 60kg
    cost = dc.calculate("Bengaluru", weight_kg=45, is_frozen=True, order_value=8000)
    assert cost.shipping == 60 * 35


def test_ncr_first30_then_rest():
    cost = dc.calculate("Delhi", weight_kg=50, is_frozen=False, order_value=4000)
    assert cost.shipping == round(30 * 15 + 20 * 12)


def test_other_city_quoted_on_confirmation():
    cost = dc.calculate("Imphal", weight_kg=100, is_frozen=False)
    assert cost.shipping == 0
    assert "quoted" in cost.notes.lower()

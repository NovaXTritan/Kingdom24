from services import product_catalog


def test_catalog_loads():
    products = product_catalog.all_products()
    assert len(products) >= 30
    # Every product must have a price (per kg or per piece)
    for p in products:
        assert p.price_per_kg or p.price_per_piece


def test_search_dal_makhani():
    res = product_catalog.search("dal makhani")
    assert res, "expected at least one match for 'dal makhani'"
    assert res[0].id == "gravy-dal-makhani"


def test_search_hindi_query():
    res = product_catalog.search("दाल मखनी")
    assert res, "expected Hindi name to match"
    assert any(p.id == "gravy-dal-makhani" for p in res)


def test_search_hinglish_query():
    res = product_catalog.search("paneer momos chahiye")
    assert res
    assert res[0].category == "frozen-momos"


def test_search_filter_storage():
    res = product_catalog.search("", storage="ambient", limit=10)
    assert res
    assert all(p.storage == "ambient" for p in res)


def test_categories_list():
    cats = product_catalog.categories()
    assert {c["category"] for c in cats} >= {
        "gravies-curries", "frozen-momos", "breads-bakery",
        "frozen-snacks", "iqf-vegetables", "ready-pastes", "ambient-range",
    }

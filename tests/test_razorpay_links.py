from urllib.parse import parse_qs, urlparse

from services import razorpay_links


def test_link_includes_required_params():
    url = razorpay_links.page_link(amount=4950, name="Sharma Kitchen", phone="9876543210")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs["amount"] == ["4950"]
    assert qs["name"] == ["Sharma Kitchen"]
    assert qs["contact"] == ["9876543210"]
    assert "/pl_SMfNZnTLY1GTcY/view" in parsed.path


def test_link_strips_country_code_from_phone():
    url = razorpay_links.page_link(amount=1000, name="X", phone="+919876543210")
    qs = parse_qs(urlparse(url).query)
    assert qs["contact"] == ["9876543210"]


def test_link_omits_optional_fields_when_missing():
    url = razorpay_links.page_link(amount=1000, name="X")
    qs = parse_qs(urlparse(url).query)
    assert "contact" not in qs
    assert "email" not in qs


def test_card_payload_shape():
    card = razorpay_links.to_card(
        amount=11800, description="Trial order", name="Sharma Kitchen", phone="9876543210"
    )
    assert card["amount"] == 11800
    assert card["currency"] == "INR"
    assert "razorpay" in card["url"]
    assert card["expires_in_minutes"] == 30

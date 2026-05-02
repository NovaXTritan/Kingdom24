"""Deterministic response templates, language-aware.

Used when:
  1. Intent is fast-shortcuttable (CONTACT, NON_B2B, OFF_TOPIC) — skip the LLM
  2. All LLM providers fail — graceful fallback
  3. Pricing-gate post-processor strips premature ₹ quotes

Each template has 3 language variants. Resolve via `get_template(name, lang)`.
"""

from __future__ import annotations


TEMPLATES: dict[str, dict[str, str]] = {
    "GREETING": {
        "ENGLISH": (
            "Welcome to Kingdom Foods! 👋\n\n"
            "We're an FSSAI & ISO certified frozen food manufacturer in Noida, "
            "supplying restaurants, hotels, cloud kitchens, and caterers across India.\n\n"
            "What type of food business do you run? I'll recommend the right products for your kitchen."
        ),
        "HINDI": (
            "Kingdom Foods mein aapka swagat hai! 👋\n\n"
            "Hum FSSAI aur ISO certified frozen food manufacturer hain Noida mein — "
            "restaurants, hotels, cloud kitchens, aur caterers ko poore India mein supply karte hain.\n\n"
            "Aapka kaisa business hai? Main aapki kitchen ke liye sahi products suggest karunga."
        ),
        "HINGLISH": (
            "Welcome to Kingdom Foods! 👋\n\n"
            "Hum FSSAI & ISO certified frozen food manufacturer hain — "
            "restaurants, hotels, cloud kitchens, aur caterers ko supply karte hain.\n\n"
            "Aapka business kaisa hai? Best products suggest karta hoon."
        ),
    },
    "NON_B2B": {
        "ENGLISH": (
            "Thank you for your interest! Kingdom Foods is a B2B manufacturer — "
            "we supply restaurants, hotels, cloud kitchens, and caterers.\n\n"
            "For personal orders, please visit our website: https://kingdom24.in\n\n"
            "If you know someone in the food business, they can reach us at 8800804580."
        ),
        "HINDI": (
            "Dhanyavaad! Kingdom Foods ek B2B manufacturer hai — hum sirf restaurants, "
            "hotels, cloud kitchens, aur caterers ko supply karte hain.\n\n"
            "Personal orders ke liye humari website dekhein: https://kingdom24.in\n\n"
            "Agar aap kisi food business wale ko jaante hain, woh hume call kar sakte hain: 8800804580."
        ),
        "HINGLISH": (
            "Thank you! Kingdom Foods B2B manufacturer hai — hum restaurants, "
            "hotels, cloud kitchens, aur caterers ko supply karte hain.\n\n"
            "Personal orders ke liye: https://kingdom24.in\n\n"
            "Food business wale ko jaante ho toh share karo: 8800804580."
        ),
    },
    "OFF_TOPIC": {
        "ENGLISH": (
            "I'm the Kingdom Foods sales assistant — I help restaurants, hotels, and "
            "cloud kitchens find the right frozen food products.\n\n"
            "Are you looking for frozen foods or HoReCa supplies for your business?"
        ),
        "HINDI": (
            "Main Kingdom Foods ka sales assistant hoon — restaurants, hotels, aur "
            "cloud kitchens ko sahi frozen food products dhundhne mein madad karta hoon.\n\n"
            "Kya aap apne business ke liye frozen food ya HoReCa supplies dhundh rahe hain?"
        ),
        "HINGLISH": (
            "I'm Kingdom Foods ka sales assistant — restaurants, hotels, aur "
            "cloud kitchens ko frozen food products find karne mein help karta hoon.\n\n"
            "Aap apne business ke liye frozen food ya supplies dhundh rahe hain?"
        ),
    },
    "CONTACT": {
        "ENGLISH": (
            "You can reach our sales team directly:\n\n"
            "📞 Phone/WhatsApp: 8800804580\n"
            "📧 Email: contact@just2eat.com\n"
            "📍 D 106, Sector 63, Noida, UP 201301\n"
            "🌐 Website: https://kingdom24.in\n\n"
            "We're available Monday to Saturday, 9 AM to 7 PM."
        ),
        "HINDI": (
            "Humari sales team se seedha baat karein:\n\n"
            "📞 Phone/WhatsApp: 8800804580\n"
            "📧 Email: contact@just2eat.com\n"
            "📍 D 106, Sector 63, Noida, UP 201301\n"
            "🌐 Website: https://kingdom24.in\n\n"
            "Monday se Saturday, subah 9 se sham 7 baje tak available hain."
        ),
        "HINGLISH": (
            "Sales team se seedha baat karo:\n\n"
            "📞 Phone/WhatsApp: 8800804580\n"
            "📧 Email: contact@just2eat.com\n"
            "📍 D 106, Sector 63, Noida, UP 201301\n"
            "🌐 https://kingdom24.in\n\n"
            "Mon-Sat, 9 AM to 7 PM available hain."
        ),
    },
    "FALLBACK": {
        "ENGLISH": (
            "I'm having trouble processing that right now. "
            "Please call our sales team directly at 8800804580 — they'll help you right away."
        ),
        "HINDI": (
            "Abhi thodi problem aa rahi hai. "
            "Kripya humari sales team ko call karein: 8800804580 — woh turant help karenge."
        ),
        "HINGLISH": (
            "Abhi thodi issue aa rahi hai. "
            "Sales team ko call karo: 8800804580 — woh turant help karenge."
        ),
    },
    "PRICING_GATE": {
        "ENGLISH": (
            "I'd love to get you the best pricing! To give you an accurate quote, "
            "could you tell me:\n\n"
            "1. What type of business do you run? (restaurant, hotel, cloud kitchen, caterer)\n"
            "2. Approximately how much do you need per month?\n\n"
            "This helps me offer you the right bulk tier."
        ),
        "HINDI": (
            "Aapko best pricing dena chahta hoon! Accurate quote ke liye batayein:\n\n"
            "1. Aapka business kya hai? (restaurant, hotel, cloud kitchen, caterer)\n"
            "2. Monthly approximately kitna chahiye?\n\n"
            "Isse main sahi bulk tier offer kar sakta hoon."
        ),
        "HINGLISH": (
            "Best pricing ke liye thoda batayein:\n\n"
            "1. Aapka business kya hai? (restaurant, hotel, cloud kitchen, caterer)\n"
            "2. Monthly kitna chahiye approximately?\n\n"
            "Right bulk tier offer karunga!"
        ),
    },
    "UNKNOWN_PRODUCT": {
        "ENGLISH": (
            "I'm not finding an exact match in this category. Could you share a "
            "bit more — product name or category? Or here's our full catalog: "
            "https://kingdom24.in"
        ),
        "HINDI": (
            "Is category mein exact match nahi mil raha. Kya aap thoda detail dein — "
            "product name ya category? Ya humara full catalog: https://kingdom24.in"
        ),
        "HINGLISH": (
            "Is category mein exact match nahi mil raha. Thoda detail do — "
            "product name ya category? Ya full catalog: https://kingdom24.in"
        ),
    },
}


def get_template(template_name: str, language: str = "ENGLISH") -> str:
    """Return a template in the requested language. Falls back to ENGLISH."""
    bucket = TEMPLATES.get(template_name, {})
    return bucket.get(language) or bucket.get("ENGLISH", "")


# ─── Backwards-compat constants ─────────────────────────────────
# Existing modules (channels/website.py, channels/fallback_responder.py) import
# these names directly. Keep them as aliases for the ENGLISH variant so older
# call-sites still work; new code should prefer get_template().
GREETING_REPLY = TEMPLATES["GREETING"]["ENGLISH"]
NON_B2B_REPLY = TEMPLATES["NON_B2B"]["ENGLISH"]
CONTACT_REPLY = TEMPLATES["CONTACT"]["ENGLISH"]
NO_PROVIDER_FALLBACK = TEMPLATES["FALLBACK"]["ENGLISH"]
UNKNOWN_PRODUCT = TEMPLATES["UNKNOWN_PRODUCT"]["ENGLISH"]

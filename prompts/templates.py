"""Deterministic response templates.

Used when:
  1. Intent is fast-shortcuttable (CONTACT, NON_B2B) — skip the LLM
  2. All LLM providers fail — graceful fallback
"""

from __future__ import annotations

NON_B2B_REPLY = (
    "Hum sirf restaurants, hotels, cloud kitchens, caterers aur QSRs ko supply karte hain — "
    "B2B bulk orders only.\n\n"
    "For personal / retail orders please visit our website: https://kingdom24.in\n\n"
    "Agar aap koi food business chalate hain (chhota dhaba bhi count karta hai 😊) "
    "to thoda batao — main aapko relevant SKUs aur tier pricing share karta hoon."
)

CONTACT_REPLY = (
    "Kingdom Foods (Kingdom 24 Pvt Ltd) — yahan se reach kar sakte ho:\n\n"
    "WhatsApp / Phone (India): +91 8800804580 (Mon–Sat, 9 AM – 7 PM IST)\n"
    "WhatsApp (International): +1 (555) 749-5990\n"
    "Email: contact@just2eat.com\n"
    "Website: https://kingdom24.in\n\n"
    "Registered Office: D-106, Sector 63, Noida, UP 201301\n"
    "Manufacturing: A-4, Sector 68, Noida 201301\n"
    "Google Maps: https://maps.app.goo.gl/8rAc8i9AdYYmVhsB7\n\n"
    "Aap chahein to abhi yahin baat shuru kar sakte ho — bataaiye kya chahiye?"
)

GREETING_REPLY = (
    "Welcome to Kingdom Foods 👋\n\n"
    "We're a B2B frozen and ambient ready-to-eat / ready-to-cook supplier for "
    "hotels, restaurants, cloud kitchens, and caterers — FSSAI & ISO 22000 "
    "certified plant in Noida.\n\n"
    "Tell me what kind of kitchen you run and what you're sourcing — I'll share "
    "the right SKUs and bulk pricing. Hindi, English ya Hinglish — jo aapko "
    "comfortable ho."
)

NO_PROVIDER_FALLBACK = (
    "Abhi humara AI assistant thoda busy hai. Aap seedha sales team se "
    "WhatsApp / call par baat karein: +91 8800804580 (India, Mon–Sat 9 AM – 7 PM IST) "
    "ya +1 (555) 749-5990 (International). Ya email karein: contact@just2eat.com"
)

UNKNOWN_PRODUCT = (
    "Is category mein abhi exact match nahi mil raha. Kya aap thoda detail dein — "
    "product name ya category? Ya main aapko humara full catalog ka link bhej doon: "
    "https://kingdom24.in"
)

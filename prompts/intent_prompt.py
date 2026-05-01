"""Prompt template fed to the fast classification model (Groq 8B)."""

INTENT_PROMPT = """Classify this customer message into exactly ONE category.

Categories:
- ORDER: wants to place / confirm an order or buy products
- INQUIRY: asking about products, prices, categories, catalog, menu
- SAMPLE: requesting samples or trial packs
- COMPLAINT: unhappy about quality, delivery, or service
- PAYMENT: asking about payment methods, payment link, invoice
- CONTACT: wants phone number, address, WhatsApp, location
- LEAD: new business introducing themselves, asking about partnership
- NON_B2B: individual / home consumer wanting personal purchase
- GREETING: hello, hi, namaste, good morning type messages
- GENERAL: anything else

Message: "{message}"

Reply with ONLY the category name. Nothing else."""

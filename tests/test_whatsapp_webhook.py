"""
tests/test_whatsapp_webhook.py — Simulate an incoming WhatsApp message

Sends a fake Meta webhook payload to the local server and prints the
Malayalam response without needing a real WhatsApp token.

Usage:
    python tests/test_whatsapp_webhook.py
    python tests/test_whatsapp_webhook.py "ചോറ് കഴിക്കാൻ പറ്റുമോ?"
"""

import sys
import json
import httpx

SERVER = "http://localhost:8000"
TEST_PHONE = "919876543210"   # fake phone number for testing

# Default test message if none provided
message = sys.argv[1] if len(sys.argv) > 1 else "എന്റെ പഞ്ചസാര കൂടുതലാണ്. എന്ത് കഴിക്കണം?"

# Meta webhook payload format
payload = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "from": TEST_PHONE,
                    "type": "text",
                    "text": {"body": message}
                }]
            }
        }]
    }]
}

print(f"\nSending: {message}")
print("-" * 60)

resp = httpx.post(f"{SERVER}/whatsapp/webhook", json=payload, timeout=120)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.json()}")
print("\n(Check server logs for the Malayalam response that would be sent to WhatsApp)")

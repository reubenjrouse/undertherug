# Example target for trying /rug:lift.
# The fallbacks below are deliberate: one hides a bug, one is at an edge.
import json
import urllib.request
from urllib.error import URLError

prices = {"apple": 30, "bread": 45}


def get_price(product_id):
    try:
        return prices[product_id]
    except Exception:
        return 0


def total(cart):
    return sum(get_price(item) for item in cart)


def fetch_exchange_rate(currency):
    try:
        with urllib.request.urlopen(f"https://example.com/rates/{currency}", timeout=5) as resp:
            return json.load(resp)["rate"]
    except URLError:
        return None

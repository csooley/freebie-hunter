"""Configuration constants for Freebie Hunter."""

import os
from pathlib import Path

# Base paths
BASE_DIR = Path(os.environ.get("FREEBIE_HUNTER_HOME", Path.home() / ".hermes" / "tools" / "freebie-hunter"))
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "freebies.db"

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# User-Agent for all HTTP requests
USER_AGENT = "FreebieHunter/1.0 (Personal use; contact via GitHub)"

# Request delay range (seconds) to be polite to sources
REQUEST_DELAY_MIN = 5
REQUEST_DELAY_MAX = 10

# Reddit API endpoints (no auth needed for .json)
REDDIT_FREEBIES_CANADA = "https://www.reddit.com/r/freebiesCanada/new/.json"
REDDIT_FREEBIES = "https://www.reddit.com/r/freebies/new/.json"

# Canadian freebie aggregation sites
CANADIAN_FREE_STUFF = "https://canadianfreestuff.com/"
FREEBIES_CANADA = "https://freebiescanada.com/"
SLICKDEALS_FREEBIES = "https://slickdeals.net/deals/freebies/"

# Guerrilla Mail API
GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"

# Category keywords for inference
CATEGORY_KEYWORDS = {
    "beauty": ["beauty", "makeup", "skincare", "cosmetic", "lipstick", "mascara", "lotion", "cream", "perfume", "fragrance", "shampoo", "conditioner", "nail", "face wash", "moisturizer"],
    "food": ["food", "snack", "drink", "candy", "chocolate", "coffee", "tea", "protein", "bar", "snack", "beverage", "soda", "juice", "cookie", "chip", "cereal", "granola", "sample pack"],
    "household": ["cleaner", "detergent", "soap", "tissue", "paper towel", "garbage bag", "air freshener", "dish", "laundry", "bleach", "wipe", "sponge"],
    "pet": ["pet", "dog", "cat", "treat", "pet food", "kibble", "litter", "chew", "toy"],
    "baby": ["baby", "diaper", "formula", "wipes", "pacifier", "onesie", "stroller"],
    "health": ["vitamin", "supplement", "pill", "medicine", "ointment", "bandage", "first aid", "probiotic", "protein powder", "electrolyte"],
    "other": [],
}

# Profile for signups — populated from environment variables or ~/.freebie-hunter-profile.json
# If neither exists, signup features will be disabled.
PROFILE_PATH = Path(os.environ.get("FREEBIE_HUNTER_PROFILE", Path.home() / ".freebie-hunter-profile.json"))
PROFILE = None  # Lazy-loaded via get_profile()

def get_profile() -> dict | None:
    """Load profile from FREEBIE_HUNTER_PROFILE or ~/.freebie-hunter-profile.json."""
    global PROFILE
    if PROFILE is not None:
        return PROFILE if PROFILE != {} else None
    
    import json
    if PROFILE_PATH.exists():
        try:
            PROFILE = json.loads(PROFILE_PATH.read_text())
            return PROFILE
        except Exception:
            pass
    
    # Check environment variable overrides
    env_name = os.environ.get("FREEBIE_HUNTER_NAME")
    if env_name:
        PROFILE = {
            "name": env_name,
            "address": os.environ.get("FREEBIE_HUNTER_ADDRESS", ""),
            "city": os.environ.get("FREEBIE_HUNTER_CITY", ""),
            "province": os.environ.get("FREEBIE_HUNTER_PROVINCE", ""),
            "postal_code": os.environ.get("FREEBIE_HUNTER_POSTAL", ""),
            "country": os.environ.get("FREEBIE_HUNTER_COUNTRY", "Canada"),
            "phone": os.environ.get("FREEBIE_HUNTER_PHONE", ""),
        }
        return PROFILE
    
    PROFILE = {}  # Sentinel: checked, not found
    return None

# Region detection keywords
CANADA_KEYWORDS = [
    "canada", "canadian", "toronto", "vancouver", "montreal", "calgary",
    "ottawa", "edmonton", "quebec", "winnipeg", "hamilton", "nova scotia",
    "new brunswick", "manitoba", "saskatchewan", "alberta", "british columbia",
    "ontario", "pei", "yukon", "nunavut", "nwt", "canad", "ship to canada",
    "available in canada", "canada only", "canada-wide",
]

US_KEYWORDS = [
    "us only", "usa only", "united states", "continental us", "lower 48",
    "america only", "us residents", "usa residents",
]

WORLDWIDE_KEYWORDS = [
    "worldwide", "international", "global", "anywhere", "all countries",
    "open to all", "no restrictions",
]

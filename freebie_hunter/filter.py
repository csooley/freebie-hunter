"""Filtering, deduplication, and scoring for freebie offers."""

import logging
from typing import Optional

from freebie_hunter.config import CANADA_KEYWORDS, WORLDWIDE_KEYWORDS
from freebie_hunter.database import offer_exists_by_url, offer_exists_by_title

logger = logging.getLogger(__name__)


def is_canada_available(offer: dict) -> bool:
    """Check if an offer is available in Canada based on text analysis.

    Returns True if the offer mentions Canada, Canadian shipping, or is worldwide.
    """
    title = (offer.get("title") or "").lower()
    desc = (offer.get("description") or "").lower()
    region = (offer.get("region") or "").lower()
    text = f"{title} {desc}"

    # Direct region match
    if region in ("canada", "worldwide"):
        return True

    # US-only check: if it explicitly says US only, it's not available in Canada
    us_exclusive = [
        "us only", "usa only", "united states only", "continental us",
        "lower 48 only", "us residents only", "usa residents only",
    ]
    for phrase in us_exclusive:
        if phrase in text:
            # But check if Canada is also mentioned (e.g., "US only? No, Canada too!")
            if not any(kw in text for kw in CANADA_KEYWORDS):
                return False
            return True

    # Check Canada keywords
    if any(kw in text for kw in CANADA_KEYWORDS):
        return True

    # Check worldwide keywords
    if any(kw in text for kw in WORLDWIDE_KEYWORDS):
        return True

    return False


def score_offer(offer: dict) -> int:
    """Score an offer for relevance. Higher = better.

    Scoring criteria:
    - Canada-exclusive: +100
    - Worldwide: +60
    - US with Canada mention: +40
    - Has estimated value: +20
    - High-value (>$10): +30
    - Has description: +10
    - Category bonus for beauty/health: +10
    - Exploit potential: +25
    """
    score = 0
    region = (offer.get("region") or "").lower()
    text = f"{(offer.get('title') or '').lower()} {(offer.get('description') or '').lower()}"

    # Region scoring
    if region == "canada":
        score += 100
    elif region == "worldwide":
        score += 60
    elif is_canada_available(offer):
        score += 40

    # Value scoring
    value = offer.get("value_estimate", "")
    if value:
        score += 20
        try:
            amount = float(value.replace("$", "").replace(",", ""))
            if amount >= 10:
                score += 30
            elif amount >= 5:
                score += 15
        except ValueError:
            pass

    # Description bonus
    if offer.get("description"):
        score += 10

    # Category bonuses
    high_value_cats = {"beauty", "health", "food"}
    if offer.get("category", "") in high_value_cats:
        score += 10

    # Exploit detection
    if detect_exploit(offer):
        score += 25

    return score


def detect_exploit(offer: dict) -> bool:
    """Detect if an offer might be exploitable (no limit, per household loophole, etc.)."""
    text = f"{(offer.get('title') or '').lower()} {(offer.get('description') or '').lower()}"

    exploit_patterns = [
        r"no limit",
        r"unlimited",
        r"per household",
        r"per address",
        r"per email",
        r"multiple accounts?",
        r"refer a friend",
        r"referral",
        r"share with friends?",
        r"get additional",
        r"bonus sample",
    ]

    import re
    for pattern in exploit_patterns:
        if re.search(pattern, text):
            return True
    return False


def filter_low_value(offer: dict) -> bool:
    """Check if an offer is too low value to bother with.

    Returns True if the offer should be KEPT (not filtered out).

    We keep:
    - Free samples, free products, free with purchase
    - High-value coupons ($3+ off, BOGO, free items with purchase)
    - Contests/sweepstakes with prizes worth $50+
    - Cash back / rebate offers

    We filter out:
    - Digital downloads, wallpapers, printable coloring pages, ebooks, PDF guides
    - Navigation junk from Slickdeals (Deal Alerts, Popular Deals, etc.)
    - Spam and truly useless content
    """
    import re

    title = (offer.get("title") or "").lower()
    desc = (offer.get("description") or "").lower()
    text = f"{title} {desc}"

    # --- Explicit KEEP signals (positive indicators) ---
    keep_patterns = [
        r"\bfree sample\b", r"\bfree product\b", r"\bfree with purchase\b",
        r"\bfree gift\b", r"\bfreebie\b", r"\btry free\b", r"\bfree trial\b",
        r"\bbogo\b", r"\bbuy one get one\b", r"\bbuy 1 get 1\b",
        r"\bcash back\b", r"\bcashback\b", r"\brebate\b", r"\brefund\b",
        r"\bmail-in rebate\b", r"\bmoney back\b",
        r"\$\d+ off\b", r"\$\d+ cash\b", r"\$\d+ gift card\b",
    ]
    for pattern in keep_patterns:
        if re.search(pattern, text):
            return True  # Definitely keep

    # --- Contest/sweepstakes: keep if prize seems substantial ---
    contest_indicators = [r"\bcontest\b", r"\bsweepstakes\b", r"\bgiveaway\b", r"\benter to win\b", r"\bwin\b"]
    is_contest = any(re.search(p, text) for p in contest_indicators)
    if is_contest:
        # Look for prize value indicators
        value = (offer.get("value_estimate") or "").replace("$", "").replace(",", "")
        try:
            if value and float(value) >= 50:
                return True
        except ValueError:
            pass
        # Check for prize keywords: cash, gift card, product prize, trip, etc.
        prize_keywords = [
            r"\bcash prize\b", r"\bgift card\b", r"\bgiftcard\b", r"\bprize pack\b",
            r"\bwin a\b", r"\btrip to\b", r"\bvacation\b", r"\bconcert tickets\b",
            r"\bproduct giveaway\b", r"\bprize worth\b", r"\bvalued at\s*\$\d+\b",
        ]
        for pk in prize_keywords:
            if re.search(pk, text):
                return True
        # Contest with no clear prize info — skip
        return False

    # --- Coupon: keep if high-value ---
    coupon_indicators = [r"\bcoupon\b", r"\bprintable coupon\b", r"\bsave\s*\$\d+", r"\boff coupon\b"]
    is_coupon = any(re.search(p, text) for p in coupon_indicators)
    if is_coupon:
        # Check for free item / BOGO / high-value discount
        high_value_coupon = [
            r"\bfree\b.{0,30}\b(coupon|item|product|sample)\b",
            r"\bbogo\b", r"\bbuy one get one\b",
            r"\$\d+\.?\d*\s*off\b",
        ]
        for hvc in high_value_coupon:
            if re.search(hvc, text):
                # For $X off, check the dollar amount
                dollar_match = re.search(r"\$(\d+\.?\d*)\s*off\b", text)
                if dollar_match:
                    try:
                        if float(dollar_match.group(1)) >= 3:
                            return True
                    except ValueError:
                        pass
                else:
                    return True  # "free" or "BOGO" without dollar amount
        return False

    # --- Explicit FILTER OUT signals (navigation junk, truly useless) ---
    filter_patterns = [
        r"\bdigital download\b", r"\bwallpaper\b", r"\bprintable coloring page\b",
        r"\bprintable\b", r"\bebook\b", r"\bpdf guide\b", r"\bpdf\b",
        r"\bspam\b", r"\bsurvey\b",  # surveys rarely worth it
        # Slickdeals navigation junk
        r"\bdeal alerts\b", r"\bpopular deals\b", r"\bhot deals\b",
        r"\bcredit cards\b", r"\bpriceline\b",
    ]
    for pattern in filter_patterns:
        if re.search(pattern, text):
            return False

    # Default: keep (it passed all negative checks)
    return True


def filter_and_score(offers: list[dict], min_score: int = 30) -> list[dict]:
    """Filter and score a list of offers.

    Steps:
    1. Filter for Canada availability
    2. Filter out low-value
    3. Deduplicate against database
    4. Score each offer
    5. Filter by minimum score
    6. Sort by score descending

    Returns filtered, scored, sorted list.
    """
    filtered = []

    for offer in offers:
        # Canada filter
        if not is_canada_available(offer):
            logger.debug(f"Filtered out (not Canada-available): {offer.get('title', 'N/A')[:80]}")
            continue

        # Low-value filter
        if not filter_low_value(offer):
            logger.debug(f"Filtered out (low value): {offer.get('title', 'N/A')[:80]}")
            continue

        # Database deduplication
        url = offer.get("url", "")
        title = offer.get("title", "")

        if url and offer_exists_by_url(url):
            logger.debug(f"Filtered out (duplicate URL): {title[:80]}")
            continue

        if title and offer_exists_by_title(title):
            logger.debug(f"Filtered out (duplicate title): {title[:80]}")
            continue

        # Score
        offer["score"] = score_offer(offer)
        offer["is_exploit"] = detect_exploit(offer)

        if offer["score"] >= min_score:
            filtered.append(offer)

    # Sort by score descending
    filtered.sort(key=lambda o: o.get("score", 0), reverse=True)

    logger.info(f"Filtered from {len(offers)} to {len(filtered)} offers (min_score={min_score})")
    return filtered

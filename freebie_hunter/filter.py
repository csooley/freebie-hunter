"""Filtering, deduplication, and scoring for freebie offers."""

import logging
import re
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

    Contest-specific:
    - Cash prizes: base score from dollar amount
    - Daily entry: +15 bonus
    - Weekly entry: +10 bonus

    Direct-search results get stricter scoring — must have brand/URL/snippet
    signals to pass, otherwise scored -50 to fall below min_score threshold.
    """
    offer_type = offer.get("offer_type", "freebie")
    source = offer.get("source", "")
    score = 0
    region = (offer.get("region") or "").lower()
    text = f"{(offer.get('title') or '').lower()} {(offer.get('description') or '').lower()}"

    # ------------------------------------------------------------------
    # Fix 3: Stricter scoring for DDG search results (source="direct_search")
    # ------------------------------------------------------------------
    if source == "direct_search":
        url_lower = (offer.get("url") or "").lower()

        # Known brand/retailer URLs that should NOT be penalized
        known_brand_urls = [
            "pggoodeveryday.ca", "laroche-posay.ca", "vichy.ca",
            "nestlebaby.ca", "enfamil.ca", "similac.ca",
            "pampers.ca", "huggies.ca", "samplesource.com",
            "pinchme.com", "topboxcircle.com", "chickadvisor.com",
            "hometesterclub.com", "bzzagent.com", "influenster.com",
            "sephora.ca", "shoppersdrugmart.ca", "pharmaprix.ca",
            "well.ca", "londondrugs.com", "rexall.ca",
        ]
        is_known_url = any(brand_url in url_lower for brand_url in known_brand_urls)

        # Aggregator URLs get penalized same as direct_search (they're not
        # the main concern — they have already-good filtering)
        is_aggregator = source in ("aggregator", "canadianfreestuff", "freebiescanada")

        if not is_known_url and not is_aggregator:
            # Must pass at least ONE of these checks
            passes_strict = False

            # Check 1: Known brand name in title/description
            known_brands = [
                "p&g", "nestlé", "nestle", "enfamil", "similac",
                "pampers", "huggies", "topbox", "top box",
                "chickadvisor", "chick advisor", "bzzagent",
                "hometester", "home tester", "pinchme", "pinch me",
                "samplesource", "sample source", "vichy",
                "la roche-posay", "la roche posay", "dove", "olay",
                "garnier", "l'oréal", "loreal", "maybelline",
                "covergirl", "cover girl", "neutrogena", "aveeno",
                "cerave", "cetaphil", "clinique", "shiseido",
                "johnson", "nivea", "vaseline",
            ]
            for brand in known_brands:
                if brand in text:
                    passes_strict = True
                    break

            # Check 2: URL path contains sample/signup indicators
            if not passes_strict:
                url_path_indicators = [
                    "/sample", "/free-sample", "/echantillon",
                    "/try", "/signup", "/sign-up", "/register",
                    "/freebie", "/rewards", "/sampling",
                ]
                for ind in url_path_indicators:
                    # Match path segments: "/sample" or "/sample/" but not "/sample-info"
                    if ind in url_lower and (
                        url_lower.endswith(ind)
                        or (ind + "/") in url_lower
                        or (ind + "?") in url_lower
                        or (ind + "#") in url_lower
                        or (ind + ".") in url_lower
                    ):
                        passes_strict = True
                        break

            # Check 3: Description explicitly mentions "free" + product type
            if not passes_strict:
                free_product_patterns = [
                    r"\bfree\b.{0,20}\b(sample|product|item|kit|box|trial|coupon)\b",
                    r"\b(free|gratuit)\b.{0,20}\b(beauty|skincare|makeup|cosmetic|perfume|parfum)\b",
                    r"\b(free|gratuit)\b.{0,20}\b(baby|diaper|couche|formula|formule)\b",
                    r"\b(free|gratuit)\b.{0,20}\b(food|coffee|tea|snack|drink)\b",
                    r"\béchantillon gratuit\b",
                ]
                import re as re2
                for pat in free_product_patterns:
                    if re2.search(pat, text):
                        passes_strict = True
                        break

            if not passes_strict:
                score = -50
                return score

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
            if offer_type == "contest" and amount >= 100:
                score += 50  # Big contest prize
            elif amount >= 10:
                score += 30
            elif amount >= 5:
                score += 15
        except ValueError:
            pass

    # Description bonus
    if offer.get("description"):
        score += 10

    # Category bonuses
    if offer_type == "contest":
        # Contest categories that are particularly exciting
        high_value_contest_cats = {"cash", "travel", "car", "electronics"}
        if offer.get("category", "") in high_value_contest_cats:
            score += 15
    else:
        high_value_cats = {"beauty", "health", "food"}
        if offer.get("category", "") in high_value_cats:
            score += 10

    # Exploit detection
    if detect_exploit(offer):
        score += 25

    # Contest entry frequency bonus
    if offer_type == "contest":
        if re.search(r"daily|every\s*day|once\s*(?:a|per)\s*day", text):
            score += 15
        elif re.search(r"weekly|once\s*(?:a|per)\s*week", text):
            score += 10

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
    source = (offer.get("source") or "").lower()
    url = (offer.get("url") or "").lower()
    offer_type = offer.get("offer_type", "freebie")
    text = f"{title} {desc}"

    # --- Fix 3/4: Stronger filtering for search results ---
    if source == "direct_search":
        # Reject blog/magazine/how-to content
        blog_noise = [
            "how to", "comment ", "voici les", "voici ", "top 10",
            "magazine", "blog", "article",
        ]
        for noise in blog_noise:
            if noise in text:
                logger.debug(f"Filtered direct_search (blog/magazine noise): {title[:80]}")
                return False

        # Reject if URL domain looks like a news/magazine/blog site
        news_domains = [
            "cnn.com", "cbc.ca", "globalnews.ca", "ctvnews.ca",
            "thestar.com", "torontosun.com", "nationalpost.com",
            "globeandmail.com", "huffpost.com", "buzzfeed.com",
            "medium.com", "forbes.com", "businessinsider.com",
            "narcity.com", "mtlblog.com", "curiocity.com",
            "dailyhive.com", "blogto.com", "todocanada.ca",
        ]
        for nd in news_domains:
            if nd in url:
                # Only keep if it contains actual signup/sample keywords
                signup_keywords = [
                    "sign up", "register", "join", "get your free",
                    "order sample", "request sample", "claim",
                ]
                has_signup = any(sk in text for sk in signup_keywords)
                if not has_signup:
                    logger.debug(f"Filtered direct_search (news domain no signup): {title[:80]}")
                    return False

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
    is_contest = offer.get("offer_type") == "contest" or any(re.search(p, text) for p in contest_indicators)
    if is_contest:
        # Contests are always valid — just check it's not complete junk
        # If it has a title and isn't pure navigation, keep it
        if title and len(title) > 3:
            return True
        return False

    # --- Coupon: keep if high-value ---
    # Fix 4: For freebie-type offers, reject low-value coupons that aren't free samples
    coupon_indicators = [r"\bcoupon\b", r"\bprintable coupon\b", r"\bsave\s*\$\d+", r"\boff coupon\b"]
    is_coupon = any(re.search(p, text) for p in coupon_indicators)
    if is_coupon:
        # Fix 4: Distinguish coupons from free samples for freebie scan
        if offer_type == "freebie":
            # Check if it has "coupon" in title AND a small dollar amount
            has_coupon_word = "coupon" in title
            dollar_match = re.search(r"\$(\d+\.?\d*)\s*off\b", text)
            has_free_product = re.search(
                r"\bfree\b.{0,30}\b(product|sample|item|perfume|cosmetic|beauty)\b", text
            ) or re.search(r"\bbogo\b|\bbuy one get one\b|\bfree item\b", text)

            if has_coupon_word and dollar_match and not has_free_product:
                try:
                    amount = float(dollar_match.group(1))
                    if amount < 5:
                        logger.debug(f"Filtered coupon (<$5 off, no free product): {title[:80]}")
                        return False
                except ValueError:
                    pass
            # Keep BOGO and free-item-with-purchase — those ARE free products

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

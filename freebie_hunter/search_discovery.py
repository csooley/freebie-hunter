"""Search-based free sample discovery engine.

Finds free samples directly from brand websites and DuckDuckGo search queries,
bypassing aggregator blogs entirely.
"""

import logging
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from freebie_hunter.scraper import (
    _fetch,
    _clean_text,
    _infer_category,
    _infer_region,
    _extract_value,
    HEADERS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known brand portal URLs — checked for active sample offers
# ---------------------------------------------------------------------------

BRAND_PORTALS = {
    "pggoodeveryday": {
        "url": "https://www.pggoodeveryday.ca/samples/",
        "brands": "P&G (Tide, Dawn, Olay, Crest, Gillette, Head & Shoulders, Pantene, etc.)",
        "category": "household/beauty",
    },
    "loreal_paris": {
        "url": "https://www.laroche-posay.ca/en_CA/free-sample.html",
        "brands": "La Roche-Posay",
        "category": "beauty",
    },
    "vichy": {
        "url": "https://www.vichy.ca/en_CA/free-sample.html",
        "brands": "Vichy",
        "category": "beauty",
    },
    "nestle_baby": {
        "url": "https://www.nestlebaby.ca/en",
        "brands": "Nestlé Baby (formula)",
        "category": "baby",
    },
    "enfamil": {
        "url": "https://www.enfamil.ca/en",
        "brands": "Enfamil (formula)",
        "category": "baby",
    },
    "similac": {
        "url": "https://similac.ca/en",
        "brands": "Similac (formula)",
        "category": "baby",
    },
    "pampers": {
        "url": "https://www.pampers.ca/en-ca",
        "brands": "Pampers (diapers)",
        "category": "baby",
    },
    "huggies": {
        "url": "https://www.huggies.ca/en-ca/rewards",
        "brands": "Huggies (diapers)",
        "category": "baby",
    },
    "samplesource": {
        "url": "https://www.samplesource.com/",
        "brands": "Multiple (Canadian sampling platform)",
        "category": "multi",
    },
    "pinchme": {
        "url": "https://www.pinchme.com/",
        "brands": "Multiple (monthly sample boxes)",
        "category": "multi",
    },
    "topbox_circle": {
        "url": "https://www.topboxcircle.com/",
        "brands": "Multiple (beauty samples + reviews)",
        "category": "beauty",
    },
    "chickadvisor": {
        "url": "https://www.chickadvisor.com/",
        "brands": "Multiple (Product Review Club)",
        "category": "multi",
    },
    "hometester": {
        "url": "https://www.hometesterclub.com/ca/en/",
        "brands": "Multiple (full-size product testing)",
        "category": "multi",
    },
    "bzzagent": {
        "url": "https://www.bzzagent.com/",
        "brands": "Multiple (product testing campaigns)",
        "category": "multi",
    },
    "influenster": {
        "url": "https://www.influenster.com/",
        "brands": "Multiple (VoxBox campaigns)",
        "category": "multi",
    },
}

# ---------------------------------------------------------------------------
# Keywords that indicate an active sample/signup opportunity on a page
# ---------------------------------------------------------------------------

ACTIVE_SAMPLE_KEYWORDS = [
    "sign up", "register", "get started", "join", "try free",
    "claim sample", "request sample", "free sample",
    "échantillon gratuit", "inscrivez-vous", "s'inscrire",
    "get your free", "order sample", "receive a sample",
    "try it free", "request yours", "get yours",
]

# ---------------------------------------------------------------------------
# Content relevance filter — rejects noise like exams, insurance, etc.
# ---------------------------------------------------------------------------

# Keywords that indicate a result is NOT a real free product sample
_NOISE_FILTER_KEYWORDS = [
    "exam", "bar exam", "test prep", "practice test", "sample question",
    "sample letter", "certification", "regulation", "compliance",
    "standards", "inspection", "tournament", "golf", "track day",
    "racing", "event registration", "food bank", "charity", "donation",
    "receive aid", "assistance", "magazine", "blog", "how to get",
    "comment obtenir", "voici les", "top 10", "software", "download",
    "télécharger", "app", "tool", "login", "portal",
    "chamber of commerce", "crop science", "farming", "agriculture",
    "insurance", "financial", "mortgage", "banking", "credit card",
    "government", "handbook", "regulation", "policy", "statement",
    "bus", "transit", "travel advisor", "travel agent", "news", "article",
    "ontario bar", "lsat", "ielts", "writing sample",
]

# Keywords/patterns that CONFIRM this is a real free product sample
_POSITIVE_SAMPLE_KEYWORDS = [
    "free sample", "échantillon gratuit", "product testing",
    "try me free", "sample box", "sample program", "free trial size",
    "free trial set", "free sample box", "free sample kit",
    "free beauty sample", "free skincare sample", "free perfume sample",
    "free cosmetic sample", "free baby sample", "free diaper sample",
    "free formula sample", "free food sample", "free coffee sample",
]

# Known brand names that are strong positive signals
_KNOWN_BRANDS = [
    "p&g", "procter", "gamble", "nestlé", "nestle", "enfamil",
    "similac", "pampers", "huggies", "topbox", "top box",
    "chickadvisor", "chick advisor", "bzzagent", "hometester",
    "home tester", "pinchme", "pinch me", "samplesource", "sample source",
    "vichy", "la roche-posay", "la roche posay", "dove", "olay",
    "garnier", "l'oréal", "loreal", "maybelline", "covergirl",
    "cover girl", "neutrogena", "aveeno", "cerave", "cetaphil",
    "clinique", "estée lauder", "estee lauder", "lancôme", "lancome",
    "biotherm", "shiseido", "kiehl", "origins", "clarins",
    "johnson", "johnson's", "nivea", "vaseline", "ponds", "simple",
    "burt's bees", "burts bees", "the body shop", "body shop",
    "lush", "sephora", "shoppers drug mart", "pharmaprix",
    "rexall", "london drugs", "well.ca",
]

# URL path endings that strongly indicate a sample offer page
_POSITIVE_URL_PATTERNS = [
    "/samples", "/free-sample", "/freebie", "/try", "/sample",
    "/echantillon", "/freebies", "/free-samples", "/try-me",
    "/signup", "/sign-up", "/register", "/rewards",
    "/product-testing", "/tester-club", "/sampling",
]


def _is_actual_free_sample(text: str, url: str = "") -> bool:
    """Check whether a search result looks like a REAL free product sample.

    Returns True if the text/URL indicates a genuine free sample offer,
    False if it's noise (exam prep, insurance, government, etc.).
    """
    text_lower = text.lower()
    url_lower = url.lower() if url else ""

    # --- IMMEDIATE REJECT: noise keywords ---
    for noise_kw in _NOISE_FILTER_KEYWORDS:
        if noise_kw in text_lower:
            return False

    # --- POSITIVE SIGNALS ---

    # 1. Known brand names
    for brand in _KNOWN_BRANDS:
        if brand in text_lower:
            return True

    # 2. Positive URL path endings (boundary-aware: match /sample or /sample/ but not /sample-info)
    for url_pat in _POSITIVE_URL_PATTERNS:
        if url_pat in url_lower and (
            url_lower.endswith(url_pat)
            or (url_pat + "/") in url_lower
            or (url_pat + "?") in url_lower
            or (url_pat + "#") in url_lower
            or (url_pat + ".") in url_lower
        ):
            return True

    # 3. Positive keyword combinations
    for pos_kw in _POSITIVE_SAMPLE_KEYWORDS:
        if pos_kw in text_lower:
            return True

    # If we got here: no noise keywords, but also no strong positive signals.
    # For brand portal checks we're lenient; for search results we're strict.
    return False

# ---------------------------------------------------------------------------
# DDG search query templates — bypass aggregator blogs
# ---------------------------------------------------------------------------

SEARCH_QUERIES = [
    'site:.ca "free sample" beauty OR skincare OR perfume OR cosmetic sign up -blog -"how to"',
    'site:.ca "free sample" baby OR diaper OR formula sign up -blog',
    'site:.ca "échantillon gratuit" parfum OR beauté OR cosmétique OR soin -blog',
    'site:.ca "product testing" Canada "sign up" panel OR club OR program -blog',
    'site:.ca "try me free" Canada beauty OR food OR health -blog',
    'site:.ca "sample box" OR "sample program" Canada sign up',
    'site:.ca "free trial" product Canada beauty OR skincare OR health -blog',
]

DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"

# Domains to skip in search results (aggregators, already-scraped)
SKIP_DOMAINS = {
    "canadianfreestuff.com",
    "freebiescanada.com",
    "slickdeals.net",
    "contestcanada.net",
    "sweepstakes.ca",
    "sweepstakesadvantage.com",
    "contestgirl.com",
    "reddit.com",
    "redd.it",
    "freebiemom.com",
    "hunt4freebies.com",
    "thefrugalfreegal.com",
    "freebies2deals.com",
    "freebies4mom.com",
    "hip2save.com",
    "moneysavingmom.com",
    "samplesize.com",
    "freebiesinyourmail.com",
    "couponaholic.net",
    "smartcanucks.ca",
    "redflagdeals.com",
    "savvynewcanadians.com",
    "canadiansavers.ca",
    "getmefreesamples.com",
    "free-samples.ca",
    "savealoonie.com",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "pinterest.com",
    "tiktok.com",
}

# Minimum delay between DDG queries (seconds)
DDG_DELAY = 6

# Maximum number of DDG queries per run (rate-limit ceiling)
MAX_QUERIES_PER_RUN = 7


def _extract_domain(url: str) -> str:
    """Extract domain from URL, stripping www."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _should_skip_domain(url: str) -> bool:
    """Check if a domain should be skipped (aggregator or already scraped)."""
    domain = _extract_domain(url)
    for skip_domain in SKIP_DOMAINS:
        if domain == skip_domain or domain.endswith("." + skip_domain):
            return True
    return False


def _page_has_active_sample(soup: BeautifulSoup) -> bool:
    """Check if a page contains keywords indicating an active sample offer."""
    page_text = soup.get_text().lower()
    for keyword in ACTIVE_SAMPLE_KEYWORDS:
        if keyword in page_text:
            return True
    return False


def _extract_page_description(soup: BeautifulSoup, keywords: list[str]) -> str:
    """Extract the most relevant text chunk near sample keywords from a page."""
    if not keywords:
        return ""

    page_text = soup.get_text()
    text_lower = page_text.lower()

    # Find the first occurrence of any keyword and grab surrounding text
    for kw in keywords:
        idx = text_lower.find(kw.lower())
        if idx >= 0:
            start = max(0, idx - 50)
            end = min(len(page_text), idx + 200)
            snippet = page_text[start:end].strip()
            return _clean_text(snippet)[:300]

    # Fallback: first 200 chars of body text
    body = soup.find("body")
    if body:
        return _clean_text(body.get_text())[:300]
    return ""


# ---------------------------------------------------------------------------
# Brand portal checker
# ---------------------------------------------------------------------------

def check_brand_portals() -> list[dict]:
    """Check each known brand portal URL for active sample offers.

    Returns list of offer dicts for portals that appear to have live sample offers.
    """
    offers = []
    logger.info(f"Checking {len(BRAND_PORTALS)} brand portals for active sample offers...")

    for portal_key, portal in BRAND_PORTALS.items():
        url = portal["url"]
        logger.debug(f"Checking brand portal: {portal_key} -> {url}")

        try:
            resp = _fetch(url, timeout=20)
            if not resp:
                logger.debug(f"Brand portal {portal_key} returned no response")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Check if the page has active sample indicators
            if not _page_has_active_sample(soup):
                logger.debug(f"Brand portal {portal_key}: no active sample keywords found")
                continue

            page_text = soup.get_text()
            page_text_lower = page_text.lower()

            # Content relevance check: page must actually be about free samples
            if not _is_actual_free_sample(page_text_lower, url):
                logger.debug(f"Brand portal {portal_key}: relevance check failed — not a free sample page")
                continue

            # Extract title
            title_tag = soup.find("title")
            title = _clean_text(title_tag.get_text()) if title_tag else portal["brands"]

            # Extract description
            description = _extract_page_description(soup, ACTIVE_SAMPLE_KEYWORDS)
            if not description:
                description = f"Sample offers from {portal['brands']}"

            # Build offer dict
            offer = {
                "source": "brand_portal",
                "url": url,
                "title": title[:200],
                "description": description[:500],
                "category": portal["category"],
                "region": "Canada",
                "value_estimate": "",
                "offer_type": "freebie",
            }

            offers.append(offer)
            logger.info(f"Brand portal ACTIVE: {portal_key} — {title[:80]}")

        except Exception as e:
            logger.warning(f"Brand portal {portal_key} error: {e}")
            continue

    logger.info(f"Brand portals active: {len(offers)}/{len(BRAND_PORTALS)}")
    return offers


# ---------------------------------------------------------------------------
# DuckDuckGo search query engine
# ---------------------------------------------------------------------------

def _run_ddg_query(query: str, max_results: int = 10) -> list[dict]:
    """Run a single DuckDuckGo text search and return structured results.

    Uses the ddgs library for bot-evasion and structured output.
    Returns list of {'url': str, 'title': str, 'snippet': str} dicts.
    """
    results = []
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results))
            for r in raw_results:
                url = r.get("href") or ""
                title = r.get("title", "")
                snippet = r.get("body", "")

                if not url or not title:
                    continue

                results.append({
                    "url": url,
                    "title": _clean_text(title)[:200],
                    "snippet": _clean_text(snippet)[:500],
                })
    except ImportError:
        logger.error("ddgs library not installed. Install with: pip install ddgs")
    except Exception as e:
        logger.warning(f"DDG search error for query '{query[:60]}...': {e}")

    return results


def run_search_queries() -> list[dict]:
    """Run DuckDuckGo search queries and extract offer candidates.

    Returns list of offer dicts from search results, filtering out
    aggregator domains, blogs, and already-scraped sources.
    """
    all_offers = []
    queries_to_run = SEARCH_QUERIES[:MAX_QUERIES_PER_RUN]

    logger.info(f"Running {len(queries_to_run)} DuckDuckGo search queries...")

    for i, query in enumerate(queries_to_run):
        logger.info(f"DDG query {i+1}/{len(queries_to_run)}: {query[:80]}...")

        try:
            results = _run_ddg_query(query, max_results=10)
            logger.debug(f"DDG query {i+1} raw results: {len(results)}")

            for result in results:
                url = result["url"]
                title = result["title"]
                snippet = result["snippet"]

                # Skip aggregator domains
                if _should_skip_domain(url):
                    logger.debug(f"Skipping aggregator/already-scraped domain: {url}")
                    continue

                # Skip if no useful content
                if not title:
                    continue

                # Content relevance filter: reject noise (exams, insurance, etc.)
                full_text = f"{title} {snippet}"
                if not _is_actual_free_sample(full_text, url):
                    logger.debug(f"Relevance filter rejected: {title[:80]}")
                    continue

                # Infer metadata from search result
                full_text = f"{title} {snippet}"
                category = _infer_category(full_text)
                region = _infer_region(full_text)
                value = _extract_value(full_text)

                # If region detection failed but we searched .ca, assume Canada
                if region == "unknown":
                    region = "Canada"

                offer = {
                    "source": "direct_search",
                    "url": url,
                    "title": title[:200],
                    "description": snippet[:500],
                    "category": category,
                    "region": region,
                    "value_estimate": value,
                    "offer_type": "freebie",
                }

                all_offers.append(offer)

        except Exception as e:
            logger.warning(f"DDG query {i+1} error: {e}")
            continue

        # Be VERY polite — delay between queries
        if i < len(queries_to_run) - 1:
            logger.debug(f"Sleeping {DDG_DELAY}s before next DDG query...")
            time.sleep(DDG_DELAY)

    # Deduplicate by URL
    seen = set()
    unique = []
    for offer in all_offers:
        url = offer["url"]
        if url not in seen:
            seen.add(url)
            unique.append(offer)

    logger.info(f"Search queries found {len(unique)} unique offers (from {len(all_offers)} raw)")
    return unique


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def search_discover(use_portals: bool = True, use_queries: bool = True) -> list[dict]:
    """Run search-based free sample discovery.

    Args:
        use_portals: Check known brand portal URLs for active sample offers.
        use_queries: Run DuckDuckGo search queries for new offer discovery.

    Returns:
        Combined and deduplicated list of offer dicts.
    """
    all_offers = []

    if use_portals:
        logger.info("--- Brand Portal Discovery ---")
        try:
            portal_offers = check_brand_portals()
            all_offers.extend(portal_offers)
        except Exception as e:
            logger.error(f"Brand portal check failed: {e}")

    if use_queries:
        logger.info("--- Search Query Discovery ---")
        try:
            query_offers = run_search_queries()
            all_offers.extend(query_offers)
        except Exception as e:
            logger.error(f"Search query discovery failed: {e}")

    # Deduplicate by URL
    seen = set()
    unique = []
    for offer in all_offers:
        url = offer["url"]
        if url not in seen:
            seen.add(url)
            offer["discovery_method"] = "search"
            unique.append(offer)

    logger.info(f"Search discovery complete: {len(unique)} total unique offers "
                f"(portals + queries)")
    return unique


# ---------------------------------------------------------------------------
# Scraper integration wrapper
# ---------------------------------------------------------------------------

def scrape_direct_search() -> list[dict]:
    """Wrapper for integration with scraper.py's ALL_SCRAPERS.

    Runs brand portal checks AND search queries, returning offers tagged
    with source='brand_portal' or source='direct_search'.
    """
    logger.info("Starting direct search discovery (brand portals + DDG queries)...")
    return search_discover(use_portals=True, use_queries=True)

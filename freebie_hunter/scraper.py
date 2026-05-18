"""Web scraping module for freebie discovery."""

import json
import logging
import random
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress insecure SSL warnings for sites with expired certs (e.g., freebiescanada.com)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from freebie_hunter.config import (
    USER_AGENT,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    REDDIT_FREEBIES_CANADA,
    REDDIT_FREEBIES,
    CANADIAN_FREE_STUFF,
    FREEBIES_CANADA,
    SLICKDEALS_FREEBIES,
    CATEGORY_KEYWORDS,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "FreebieHunter/1.0 (by /u/freebiebot; personal use project)",
    "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Connection": "keep-alive",
}


def _polite_delay() -> None:
    """Sleep for a random duration between REQUEST_DELAY_MIN and REQUEST_DELAY_MAX seconds."""
    delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
    logger.debug(f"Sleeping {delay:.1f}s to be polite...")
    time.sleep(delay)


def _fetch(url: str, timeout: int = 30) -> Optional[requests.Response]:
    """Fetch a URL with error handling. Falls back to verify=False for SSL errors."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        logger.debug(f"SSL error for {url}, retrying with verify=False...")
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            logger.warning(f"Failed to fetch {url} (even with verify=False): {e}")
            return None
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None


def _infer_category(text: str) -> str:
    """Infer category from text using keyword matching."""
    if not text:
        return "other"
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category == "other":
            continue
        for kw in keywords:
            if kw in text_lower:
                return category
    return "other"


def _infer_region(text: str) -> str:
    """Infer region from text context."""
    if not text:
        return "unknown"
    text_lower = text.lower()

    # Check for Canada keywords
    from freebie_hunter.config import CANADA_KEYWORDS, US_KEYWORDS, WORLDWIDE_KEYWORDS

    canada_hits = sum(1 for kw in CANADA_KEYWORDS if kw in text_lower)
    us_hits = sum(1 for kw in US_KEYWORDS if kw in text_lower)
    worldwide_hits = sum(1 for kw in WORLDWIDE_KEYWORDS if kw in text_lower)

    if canada_hits > 0:
        return "Canada"
    elif worldwide_hits > 0:
        return "Worldwide"
    elif us_hits > 0 and canada_hits == 0:
        return "US"
    else:
        return "unknown"


def _extract_value(text: str) -> str:
    """Try to extract estimated value from text."""
    if not text:
        return ""
    # Look for dollar amounts
    matches = re.findall(r'\$(\d+(?:\.\d{2})?)', text)
    if matches:
        return f"${max(float(m) for m in matches)}"
    # Look for "worth $X"
    worth_matches = re.findall(r'worth\s*\$?(\d+(?:\.\d{2})?)', text, re.IGNORECASE)
    if worth_matches:
        return f"${max(float(m) for m in worth_matches)}"
    return ""


def _clean_text(text: str) -> str:
    """Clean up extracted text."""
    if not text:
        return ""
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Decode HTML entities (BeautifulSoup usually handles this, but belt-and-suspenders)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text


# --- Source-specific scrapers ---

def scrape_canadian_free_stuff() -> list[dict]:
    """Scrape https://canadianfreestuff.com/"""
    offers = []
    logger.info("Scraping Canadian Free Stuff...")

    resp = _fetch(CANADIAN_FREE_STUFF)
    if not resp:
        return offers

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try common WordPress/feed structures
    articles = soup.find_all("article") or soup.find_all("div", class_=re.compile(r"post|entry|card|item"))
    if not articles:
        # Try finding any linked posts
        articles = soup.find_all("a", href=re.compile(r"/20\d{2}/"))

    seen_urls = set()
    for article in articles[:30]:  # Limit per source
        try:
            # Get title and URL
            title_elem = (
                article.find(["h1", "h2", "h3", "h4"]) or
                article.find("a", class_=re.compile(r"title|heading", re.I))
            )
            if not title_elem:
                title_elem = article.find("a")

            title = _clean_text(title_elem.get_text() if title_elem else "")
            link = None
            if title_elem and title_elem.get("href"):
                link = title_elem["href"]
            elif title_elem:
                link = title_elem.find("a")
                link = link["href"] if link and link.get("href") else None

            if not link:
                continue
            # Make absolute
            link = urljoin(CANADIAN_FREE_STUFF, link)
            if link in seen_urls:
                continue
            seen_urls.add(link)

            # Get description
            desc_elem = article.find("div", class_=re.compile(r"excerpt|content|summary|desc", re.I))
            description = _clean_text(desc_elem.get_text() if desc_elem else "")

            # Get full text for category/value/region inference
            full_text = f"{title} {description}"
            category = _infer_category(full_text)
            region = _infer_region(full_text)
            value = _extract_value(full_text)

            if not title:
                continue

            offers.append({
                "source": "canadianfreestuff.com",
                "url": link,
                "title": title[:200],
                "description": description[:500],
                "category": category,
                "region": region,
                "value_estimate": value,
            })

        except Exception as e:
            logger.debug(f"Error parsing article from canadianfreestuff.com: {e}")
            continue

    logger.info(f"Found {len(offers)} offers from canadianfreestuff.com")
    return offers


def scrape_freebies_canada() -> list[dict]:
    """Scrape https://freebiescanada.com/"""
    offers = []
    logger.info("Scraping Freebies Canada...")

    resp = _fetch(FREEBIES_CANADA)
    if not resp:
        return offers

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = soup.find_all("article") or soup.find_all("div", class_=re.compile(r"post|entry|card|item"))

    seen_urls = set()
    for article in articles[:30]:
        try:
            title_elem = article.find(["h1", "h2", "h3", "h4"]) or article.find("a")
            title = _clean_text(title_elem.get_text() if title_elem else "")

            link = None
            if title_elem and title_elem.get("href"):
                link = title_elem["href"]
            elif title_elem:
                a_tag = title_elem.find("a")
                link = a_tag["href"] if a_tag and a_tag.get("href") else None

            if not link:
                continue
            link = urljoin(FREEBIES_CANADA, link)
            if link in seen_urls:
                continue
            seen_urls.add(link)

            desc_elem = article.find("div", class_=re.compile(r"excerpt|content|summary|desc", re.I))
            description = _clean_text(desc_elem.get_text() if desc_elem else "")

            full_text = f"{title} {description}"
            category = _infer_category(full_text)
            region = _infer_region(full_text)
            value = _extract_value(full_text)

            if not title:
                continue

            offers.append({
                "source": "freebiescanada.com",
                "url": link,
                "title": title[:200],
                "description": description[:500],
                "category": category,
                "region": region,
                "value_estimate": value,
            })

        except Exception as e:
            logger.debug(f"Error parsing article from freebiescanada.com: {e}")
            continue

    logger.info(f"Found {len(offers)} offers from freebiescanada.com")
    return offers


def _scrape_reddit(url: str, source_name: str) -> list[dict]:
    """Generic Reddit .json scraper."""
    offers = []
    resp = _fetch(url)
    if not resp:
        return offers

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse Reddit JSON from {url}: {e}")
        return offers

    posts = data.get("data", {}).get("children", [])
    for post_data in posts[:30]:
        try:
            post = post_data.get("data", {})
            title = _clean_text(post.get("title", ""))
            selftext = _clean_text(post.get("selftext", ""))
            permalink = post.get("permalink", "")
            post_url = post.get("url", "")
            score = post.get("score", 0)

            # Skip stickied posts, low score
            if post.get("stickied") or score < 1:
                continue

            full_text = f"{title} {selftext}"
            category = _infer_category(full_text)
            region = _infer_region(full_text)
            value = _extract_value(full_text)

            # Use permalink as canonical URL
            canonical = f"https://www.reddit.com{permalink}" if permalink else post_url

            offers.append({
                "source": source_name,
                "url": canonical,
                "title": title[:200],
                "description": (selftext or post_url)[:500],
                "category": category,
                "region": region,
                "value_estimate": value,
            })

        except Exception as e:
            logger.debug(f"Error parsing Reddit post: {e}")
            continue

    logger.info(f"Found {len(offers)} offers from {source_name}")
    return offers


def scrape_reddit_freebies_canada() -> list[dict]:
    """Scrape r/freebiesCanada."""
    return _scrape_reddit(REDDIT_FREEBIES_CANADA, "reddit.com/r/freebiesCanada")


def scrape_reddit_freebies() -> list[dict]:
    """Scrape r/freebies and filter for Canada mentions."""
    raw = _scrape_reddit(REDDIT_FREEBIES, "reddit.com/r/freebies")

    # Filter: only keep posts with Canada mentions or worldwide
    from freebie_hunter.config import CANADA_KEYWORDS, WORLDWIDE_KEYWORDS
    canada_filtered = []
    for offer in raw:
        text = f"{offer['title']} {offer['description']}".lower()
        if any(kw in text for kw in CANADA_KEYWORDS) or \
           any(kw in text for kw in WORLDWIDE_KEYWORDS) or \
           offer["region"] in ("Canada", "Worldwide"):
            canada_filtered.append(offer)

    logger.info(f"Filtered r/freebies from {len(raw)} to {len(canada_filtered)} Canada-relevant offers")
    return canada_filtered


def scrape_slickdeals() -> list[dict]:
    """Scrape Slickdeals freebies section."""
    offers = []
    logger.info("Scraping Slickdeals Freebies...")

    resp = _fetch(SLICKDEALS_FREEBIES)
    if not resp:
        return offers

    soup = BeautifulSoup(resp.text, "html.parser")

    # Slickdeals uses various class names; try common ones
    deal_items = (
        soup.find_all("div", class_=re.compile(r"dealCard|dealTile|fpGridBox|deal", re.I)) or
        soup.find_all("li", class_=re.compile(r"deal|fp", re.I))
    )

    # Navigation junk titles to filter out
    nav_junk = {
        "deal alerts", "popular deals", "hot deals",
        "credit cards", "priceline",
    }

    seen_urls = set()
    for item in deal_items[:30]:
        try:
            title_elem = item.find(["a", "span"], class_=re.compile(r"title|dealTitle|itemTitle", re.I))
            if not title_elem:
                title_elem = item.find("a", href=True)

            title = _clean_text(title_elem.get_text() if title_elem else "")
            link = title_elem.get("href") if title_elem else None

            if not link:
                continue
            link = urljoin(SLICKDEALS_FREEBIES, link)
            if link in seen_urls:
                continue
            seen_urls.add(link)

            # Skip navigation junk by title
            title_lower = title.lower().strip()
            if title_lower in nav_junk or any(junk in title_lower for junk in nav_junk):
                logger.debug(f"Filtering Slickdeals nav junk: {title[:80]}")
                continue

            # Get description/price info
            desc_elem = item.find(["div", "span"], class_=re.compile(r"desc|info|price", re.I))
            description = _clean_text(desc_elem.get_text() if desc_elem else "")

            # Get score/rating if available (score 0 means no real content)
            score_elem = item.find(["span", "div"], class_=re.compile(r"score|rating|votes", re.I))
            if score_elem:
                score_text = _clean_text(score_elem.get_text())
                try:
                    item_score = int(re.sub(r"[^\d]", "", score_text) or "0")
                    if item_score == 0 and not description.strip():
                        logger.debug(f"Filtering Slickdeals item with no content: {title[:80]}")
                        continue
                except ValueError:
                    pass

            full_text = f"{title} {description}"
            category = _infer_category(full_text)
            region = _infer_region(full_text)
            value = _extract_value(full_text)

            if not title:
                continue

            offers.append({
                "source": "slickdeals.net",
                "url": link,
                "title": title[:200],
                "description": description[:500],
                "category": category,
                "region": region,
                "value_estimate": value,
            })

        except Exception as e:
            logger.debug(f"Error parsing Slickdeals item: {e}")
            continue

    logger.info(f"Found {len(offers)} offers from slickdeals.net")
    return offers


# --- Main scraper orchestrator ---

ALL_SCRAPERS = [
    ("canadian_free_stuff", scrape_canadian_free_stuff),
    ("freebies_canada", scrape_freebies_canada),
    ("reddit_freebies_canada", scrape_reddit_freebies_canada),
    ("reddit_freebies", scrape_reddit_freebies),
    ("slickdeals", scrape_slickdeals),
]


def scrape_all(sources: list[str] = None) -> list[dict]:
    """Run all configured scrapers and return combined results.

    Args:
        sources: Optional list of source keys to scrape. If None, scrape all.
                 Valid keys: canadian_free_stuff, freebies_canada,
                 reddit_freebies_canada, reddit_freebies, slickdeals
    """
    all_offers = []

    for source_key, scraper_fn in ALL_SCRAPERS:
        if sources and source_key not in sources:
            continue
        try:
            offers = scraper_fn()
            all_offers.extend(offers)
        except Exception as e:
            logger.error(f"Scraper {source_key} failed: {e}")
            continue

        # Be polite between sources
        if len(all_offers) > 0:
            _polite_delay()

    # Deduplicate by URL within this batch
    seen = set()
    unique = []
    for offer in all_offers:
        if offer["url"] not in seen:
            seen.add(offer["url"])
            unique.append(offer)

    logger.info(f"Total unique offers across all sources: {len(unique)}")
    return unique

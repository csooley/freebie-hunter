"""Formatter for terminal and Discord digest output."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from freebie_hunter.config import DATA_DIR

logger = logging.getLogger(__name__)

# Status colors/styles for terminal
STATUS_STYLES = {
    "new": "cyan",
    "claimed": "yellow",
    "shipped": "green",
    "expired": "red",
    "rejected": "grey",
    "captcha_blocked": "magenta",
}

# Category emojis
CATEGORY_EMOJIS = {
    "beauty": "💄",
    "food": "🍕",
    "household": "🧹",
    "pet": "🐾",
    "baby": "👶",
    "health": "💊",
    "other": "📦",
}


def _category_emoji(category: str) -> str:
    return CATEGORY_EMOJIS.get(category, "📦")


def _status_emoji(status: str) -> str:
    emoji_map = {
        "new": "🆕",
        "claimed": "✅",
        "shipped": "🚚",
        "expired": "⏰",
        "rejected": "❌",
        "captcha_blocked": "🔒",
    }
    return emoji_map.get(status, "❓")


def format_terminal(offers: list[dict], title: str = "Freebie Hunter Results") -> str:
    """Format offers as a Rich-powered terminal table.

    Returns a string (the Rich markup would need to be printed via a Console).
    The caller should use rich.console.Console().print() with this.
    """
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich.panel import Panel

    console = Console()

    # Create table
    table = Table(title=title, show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Score", width=6)
    table.add_column("Title", max_width=50)
    table.add_column("Cat", width=5)
    table.add_column("Region", width=8)
    table.add_column("Value", width=7)
    table.add_column("Status", width=8)

    for i, offer in enumerate(offers, 1):
        status = offer.get("status", "new")
        style = STATUS_STYLES.get(status, "")
        exploit = "💥" if offer.get("is_exploit") else ""

        table.add_row(
            str(i),
            str(offer.get("score", "-")),
            f"{exploit} {offer.get('title', 'N/A')[:80]}",
            _category_emoji(offer.get("category", "other")),
            offer.get("region", "?"),
            offer.get("value_estimate", "-"),
            f"[{style}]{status}[/{style}]",
        )

    # Capture as string
    with console.capture() as capture:
        console.print(table)
    return capture.get()


def format_discord(offers: list[dict], title: str = "🛍️ Freebie Hunter Scan") -> str:
    """Format offers for Discord markdown (embed-friendly).

    Discord embeds have field limits (1024 chars). This produces
    a markdown summary suitable for Discord messages.
    """
    lines = [
        f"## {title}",
        f"*{len(offers)} offers found*",
        "",
    ]

    for i, offer in enumerate(offers[:15], 1):  # Discord has limits
        cat_emoji = _category_emoji(offer.get("category", "other"))
        status = _status_emoji(offer.get("status", "new"))
        score = offer.get("score", "?")
        value = offer.get("value_estimate", "")

        line = f"**{i}. [{score}pts]** {cat_emoji} {status} "
        line += f"[{offer.get('title', 'N/A')[:100]}]({offer.get('url', '')})"
        if value:
            line += f" — {value}"
        if offer.get("is_exploit"):
            line += " 💥"
        lines.append(line)

    if len(offers) > 15:
        lines.append(f"\n*...and {len(offers) - 15} more offers*")

    lines.append(f"\n*Scanned at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)


def format_json(offers: list[dict]) -> str:
    """Format offers as JSON string."""
    # Convert datetime objects to strings
    clean = []
    for o in offers:
        cleaned = dict(o)
        for key in list(cleaned.keys()):
            if isinstance(cleaned[key], datetime):
                cleaned[key] = cleaned[key].isoformat()
        clean.append(cleaned)
    return json.dumps(clean, indent=2, default=str)


def save_daily_summary(offers: list[dict], stats: dict) -> Path:
    """Save a daily summary file to the data directory.

    Returns the path to the saved file.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    filename = DATA_DIR / f"summary_{today}.md"

    lines = [
        f"# Freebie Hunter Daily Summary - {today}",
        "",
        f"**Total offers in database:** {stats.get('total', 0)}",
        "",
        "## Status Breakdown",
        "",
    ]

    for status, count in stats.get("by_status", {}).items():
        lines.append(f"- {_status_emoji(status)} **{status}**: {count}")

    lines.extend(["", "## Today's New Offers", ""])

    for i, offer in enumerate(offers[:50], 1):
        cat = _category_emoji(offer.get("category", "other"))
        lines.append(
            f"{i}. {cat} [{offer.get('title', 'N/A')[:100]}]({offer.get('url', '')}) "
            f"— {offer.get('region', '?')} | Score: {offer.get('score', '?')}"
        )
        if offer.get("value_estimate"):
            lines.append(f"   Value: {offer['value_estimate']}")

    content = "\n".join(lines)
    filename.write_text(content)
    logger.info(f"Saved daily summary to {filename}")
    return filename


def format_stats_terminal(stats: dict) -> str:
    """Format database stats for terminal display."""
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel

    console = Console()

    # Status table
    status_table = Table(title="Offers by Status")
    status_table.add_column("Status", style="bold")
    status_table.add_column("Count")

    for status, count in sorted(stats.get("by_status", {}).items()):
        status_table.add_row(f"{_status_emoji(status)} {status}", str(count))

    # Source table
    source_table = Table(title="Offers by Source")
    source_table.add_column("Source", style="bold")
    source_table.add_column("Count")

    for source, count in sorted(stats.get("by_source", {}).items(), key=lambda x: -x[1]):
        source_table.add_row(source, str(count))

    # Category table
    cat_table = Table(title="Offers by Category")
    cat_table.add_column("Category", style="bold")
    cat_table.add_column("Count")

    for cat, count in sorted(stats.get("by_category", {}).items(), key=lambda x: -x[1]):
        cat_table.add_row(f"{_category_emoji(cat)} {cat}", str(count))

    with console.capture() as capture:
        console.print(Panel(f"[bold]Total Offers: {stats.get('total', 0)}[/bold]"))
        console.print(status_table)
        console.print(source_table)
        console.print(cat_table)
    return capture.get()


def print_offer_detail(offer: dict) -> str:
    """Format a single offer in detail for terminal."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    with console.capture() as capture:
        console.print(Panel(
            f"[bold]{offer.get('title', 'N/A')}[/bold]\n\n"
            f"URL: {offer.get('url', 'N/A')}\n"
            f"Source: {offer.get('source', 'N/A')}\n"
            f"Category: {_category_emoji(offer.get('category', 'other'))} {offer.get('category', 'other')}\n"
            f"Region: {offer.get('region', 'N/A')}\n"
            f"Value: {offer.get('value_estimate', 'N/A')}\n"
            f"Status: {_status_emoji(offer.get('status', 'new'))} {offer.get('status', 'new')}\n"
            f"Email: {offer.get('email_used', 'N/A')}\n"
            f"Description: {offer.get('description', 'N/A')[:300]}\n"
            f"Exploit: {'💥 Yes' if offer.get('is_exploit') else 'No'}\n"
            f"Notes: {offer.get('notes', '')}"
        ))
    return capture.get()

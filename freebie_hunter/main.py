#!/usr/bin/env python3
"""Freebie Hunter CLI - Autonomous free sample discovery and tracking."""

import argparse
import logging
import sys
import time
from datetime import datetime

from freebie_hunter.config import DATA_DIR, DB_PATH
from freebie_hunter.database import (
    init_db,
    insert_offer,
    get_offers,
    get_offer_by_id,
    get_stats,
    get_new_offers_count,
    update_offer_status,
    log_run,
)
from freebie_hunter.scraper import scrape_all
from freebie_hunter.filter import filter_and_score
from freebie_hunter.digest import (
    format_terminal,
    format_discord,
    format_json,
    format_stats_terminal,
    print_offer_detail,
    save_daily_summary,
)
from freebie_hunter.email_gen import test_guerrilla_mail
from freebie_hunter.signup import signup_offer


# Setup logging
def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy libs
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def cmd_scan(args) -> int:
    """Scan for new freebies and contests and show results."""
    from rich.console import Console

    console = Console()
    logger = logging.getLogger(__name__)

    # Choose heading based on type
    type_label = args.type if hasattr(args, 'type') and args.type != 'all' else 'all'
    emoji = "🎯" if type_label == "contest" else "🔍"
    heading = f"{emoji} Freebie Hunter - Scanning for {type_label} offers..."
    console.print(f"[bold blue]{heading}[/bold blue]\n")

    # Scrape
    start = time.time()
    offer_type = args.type if hasattr(args, 'type') else 'all'
    raw_offers = scrape_all(sources=args.sources.split(",") if args.sources else None, offer_type=offer_type)
    logger.info(f"Raw offers found: {len(raw_offers)}")

    if not raw_offers:
        console.print("[yellow]No offers found from any source. Check your internet connection.[/yellow]")
        return 0

    # Filter and score
    filtered = filter_and_score(raw_offers, min_score=args.min_score if hasattr(args, 'min_score') else 30)
    logger.info(f"After filtering: {len(filtered)}")

    # Save to database
    new_count = 0
    for offer in filtered:
        offer_id = insert_offer(offer)
        if offer_id:
            offer["id"] = offer_id
            new_count += 1

    elapsed = time.time() - start

    # Log the run
    log_run(
        offers_found=len(filtered),
        offers_claimed=0,
        duration_seconds=elapsed,
    )

    # Output
    if args.json:
        print(format_json(filtered))
    else:
        if filtered:
            # Show terminal table of new offers
            for offer in filtered:
                if "status" not in offer:
                    offer["status"] = "new"
            output = format_terminal(filtered, title=f"Freebie Hunter — {len(filtered)} offers ({elapsed:.1f}s)")
            console.print(output)
        else:
            console.print("[yellow]No new Canada-available offers found after filtering.[/yellow]")

    # Show summary stats
    stats = get_stats()
    console.print(
        f"\n[dim]💾 Database: {stats['total']} total | {new_count} new this scan | "
        f"at {DB_PATH}[/dim]"
    )

    return 0


def cmd_claim(args) -> int:
    """Claim a specific offer by ID."""
    from rich.console import Console

    console = Console()
    logger = logging.getLogger(__name__)

    offer = get_offer_by_id(args.id)
    if not offer:
        console.print(f"[red]Offer ID {args.id} not found.[/red]")
        return 1

    console.print(f"[bold]Attempting to claim:[/bold] {offer['title']}")
    console.print(f"[dim]URL: {offer['url']}[/dim]")

    if args.dry_run:
        console.print("[yellow]DRY RUN MODE - will not actually submit[/yellow]")

    # Determine email type based on offer type
    email_type = "persistent" if offer.get("offer_type") == "contest" else "disposable"

    result = signup_offer(
        offer_url=offer["url"],
        email_address=args.email if hasattr(args, 'email') and args.email else None,
        dry_run=args.dry_run,
        email_type=email_type,
    )

    if result["success"]:
        status = "claimed"
        console.print(f"[green]✅ Signup successful! Email: {result.get('email_used', 'N/A')}[/green]")
    elif result.get("captcha_detected"):
        status = "captcha_blocked"
        console.print("[magenta]🔒 CAPTCHA blocked — requires manual claiming[/magenta]")
    else:
        status = "rejected"
        console.print(f"[red]❌ Claim failed: {result.get('error', 'Unknown error')}[/red]")

    update_offer_status(
        offer_id=offer["id"],
        status=status,
        email_used=result.get("email_used"),
        notes=result.get("error", ""),
    )

    if result.get("confirmation_text"):
        console.print(f"[dim]Confirmation: {result['confirmation_text'][:200]}...[/dim]")

    # CAPTCHA is not an error — return 0 for both success and captcha_blocked
    return 0 if (result["success"] or result.get("captcha_detected")) else 1


def cmd_full(args) -> int:
    """Full pipeline: scan + auto-claim eligible offers."""
    from rich.console import Console

    console = Console()
    logger = logging.getLogger(__name__)

    console.print("[bold blue]🚀 Freebie Hunter - Full Pipeline[/bold blue]\n")

    # Step 1: Scan
    console.print("[bold]Step 1/3: Scanning for offers...[/bold]")
    offer_type = args.type if hasattr(args, 'type') and args.type else 'all'
    raw_offers = scrape_all(sources=args.sources.split(",") if args.sources else None, offer_type=offer_type)
    filtered = filter_and_score(raw_offers)

    new_count = 0
    new_offers = []
    for offer in filtered:
        offer_id = insert_offer(offer)
        if offer_id:
            offer["id"] = offer_id
            offer["status"] = "new"
            new_offers.append(offer)
            new_count += 1

    console.print(f"  Found {len(filtered)} offers, {new_count} new")

    # Step 2: Select top offers for auto-claim
    console.print(f"\n[bold]Step 2/3: Auto-claiming top offers...[/bold]")
    limit = args.limit if hasattr(args, 'limit') and args.limit else 5
    top_offers = new_offers[:limit]

    claimed_count = 0
    captcha_count = 0
    failed_count = 0
    for i, offer in enumerate(top_offers, 1):
        console.print(f"\n  Claiming [{i}/{len(top_offers)}]: {offer['title'][:80]}...")
        # Determine email type based on offer type
        email_type = "persistent" if offer.get("offer_type") == "contest" else "disposable"
        result = signup_offer(
            offer_url=offer["url"],
            dry_run=args.dry_run,
            email_type=email_type,
        )

        if result["success"]:
            status = "claimed"
            claimed_count += 1
            console.print(f"    [green]✅ Claimed! {result.get('email_used', '')}[/green]")
        elif result.get("captcha_detected"):
            status = "captcha_blocked"
            captcha_count += 1
            console.print(f"    [magenta]🔒 CAPTCHA blocked — requires manual claiming[/magenta]")
        else:
            status = "rejected"
            failed_count += 1
            console.print(f"    [red]❌ Failed: {result.get('error', 'Unknown')}[/red]")

        update_offer_status(
            offer_id=offer["id"],
            status=status,
            email_used=result.get("email_used"),
            notes=result.get("error", ""),
        )

    # Step 3: Summary
    console.print(f"\n[bold]Step 3/3: Summary[/bold]")
    console.print(f"  📊 Scanned: {len(filtered)} | New: {new_count}")
    console.print(f"  ✅ Auto-claimed: {claimed_count} | 🔒 Manual needed: {captcha_count} | ❌ Failed: {failed_count}")
    stats = get_stats()
    console.print(f"  💾 Total in database: {stats['total']}")

    return 0


def cmd_claim_pending(args) -> int:
    """Claim all pending (status='new') offers, skipping CAPTCHA ones."""
    from rich.console import Console

    console = Console()
    logger = logging.getLogger(__name__)

    pending = get_offers(status="new", limit=args.limit if hasattr(args, 'limit') and args.limit else 50)
    if not pending:
        console.print("[yellow]No pending offers to claim.[/yellow]")
        return 0

    console.print(f"[bold]🔍 Found {len(pending)} pending offers[/bold]\n")

    claimed_count = 0
    captcha_count = 0
    failed_count = 0

    for i, offer in enumerate(pending, 1):
        console.print(f"\n  [{i}/{len(pending)}] {offer['title'][:80]}...")
        console.print(f"  [dim]URL: {offer['url']}[/dim]")

        email_type = "persistent" if offer.get("offer_type") == "contest" else "disposable"
        result = signup_offer(
            offer_url=offer["url"],
            dry_run=False,
            email_type=email_type,
        )

        if result["success"]:
            status = "claimed"
            claimed_count += 1
            console.print(f"    [green]✅ Claimed! {result.get('email_used', '')}[/green]")
        elif result.get("captcha_detected"):
            status = "captcha_blocked"
            captcha_count += 1
            console.print(f"    [magenta]🔒 CAPTCHA blocked — requires manual claiming[/magenta]")
        else:
            status = "rejected"
            failed_count += 1
            console.print(f"    [red]❌ Failed: {result.get('error', 'Unknown')}[/red]")

        update_offer_status(
            offer_id=offer["id"],
            status=status,
            email_used=result.get("email_used"),
            notes=result.get("error", ""),
        )

    console.print(f"\n[bold]📊 Claim-Pending Summary[/bold]")
    console.print(f"  ✅ Auto-claimed: {claimed_count} | 🔒 Manual needed: {captcha_count} | ❌ Failed: {failed_count}")
    return 0


def cmd_stats(args) -> int:
    """Show database statistics."""
    from rich.console import Console

    console = Console()
    stats = get_stats()
    output = format_stats_terminal(stats)
    console.print(output)
    return 0


def cmd_test_email(args) -> int:
    """Test Guerrilla Mail integration."""
    from rich.console import Console

    console = Console()
    console.print("[bold]📧 Testing Guerrilla Mail Integration[/bold]\n")

    result = test_guerrilla_mail()

    if result["success"]:
        console.print(f"[green]✅ Guerrilla Mail is working![/green]")
        console.print(f"  Email: {result['email']}")
        console.print(f"  Session ID: {result['session_id'][:30]}...")
        console.print(f"  Messages found: {result.get('messages_found', 0)}")
    else:
        console.print(f"[red]❌ Guerrilla Mail test failed[/red]")
        for error in result.get("errors", []):
            console.print(f"  Error: {error}")

    return 0 if result["success"] else 1


def cmd_list(args) -> int:
    """List offers from the database."""
    from rich.console import Console

    console = Console()
    status = args.status if hasattr(args, 'status') and args.status else None
    offers = get_offers(status=status, limit=args.limit if hasattr(args, 'limit') else 50)

    if not offers:
        console.print("[yellow]No offers found. Run 'scan' first.[/yellow]")
        return 0

    # Add status if not present
    for offer in offers:
        if "status" not in offer:
            offer["status"] = "new"

    if args.json:
        print(format_json(offers))
    else:
        output = format_terminal(offers, title=f"Database Offers ({len(offers)})")
        console.print(output)

    return 0


def cmd_show(args) -> int:
    """Show details for a specific offer."""
    from rich.console import Console

    console = Console()
    offer = get_offer_by_id(args.id)

    if not offer:
        console.print(f"[red]Offer ID {args.id} not found.[/red]")
        return 1

    output = print_offer_detail(offer)
    console.print(output)
    return 0


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="freebie-hunter",
        description="🔍 Freebie Hunter - Autonomous free sample discovery and tracking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  freebie-hunter scan               Find new freebies
  freebie-hunter scan --json        JSON output
  freebie-hunter scan --sources reddit_freebies_canada
  freebie-hunter claim 5            Claim offer ID 5
  freebie-hunter claim 5 --dry-run  Test claim without submitting
  freebie-hunter full               Full scan + auto-claim pipeline
  freebie-hunter claim-pending      Claim all new offers (skips CAPTCHA)
  freebie-hunter stats              Show database stats
  freebie-hunter list               List all offers
  freebie-hunter list --status new  List new offers only
  freebie-hunter show 3             Show offer ID 3 details
  freebie-hunter test-email         Test Guerrilla Mail
        """,
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--data-dir", type=str, default=str(DATA_DIR), help="Data directory")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # scan
    scan_parser = subparsers.add_parser("scan", help="Find new freebies and/or contests")
    scan_parser.add_argument("--json", action="store_true", help="Output as JSON")
    scan_parser.add_argument("--min-score", type=int, default=30, help="Minimum score to include (default: 30)")
    scan_parser.add_argument("--sources", type=str, help="Comma-separated source keys to scrape")
    scan_parser.add_argument("--type", type=str, default="all", choices=["freebie", "contest", "all"],
                            help="Offer type to scan (default: all)")

    # claim
    claim_parser = subparsers.add_parser("claim", help="Claim a specific offer")
    claim_parser.add_argument("id", type=int, help="Offer ID to claim")
    claim_parser.add_argument("--dry-run", action="store_true", help="Don't actually submit forms")
    claim_parser.add_argument("--email", type=str, help="Email to use (generates one if not provided)")
    claim_parser.add_argument("--type", type=str, default="all", choices=["freebie", "contest", "all"],
                            help="Offer type context (default: all)")

    # full
    full_parser = subparsers.add_parser("full", help="Full scan + auto-claim pipeline")
    full_parser.add_argument("--dry-run", action="store_true", help="Don't actually submit forms")
    full_parser.add_argument("--limit", type=int, default=5, help="Max offers to auto-claim (default: 5)")
    full_parser.add_argument("--sources", type=str, help="Comma-separated source keys")
    full_parser.add_argument("--type", type=str, default="all", choices=["freebie", "contest", "all"],
                            help="Offer type (default: all)")

    # stats
    subparsers.add_parser("stats", help="Show database statistics")

    # list
    list_parser = subparsers.add_parser("list", help="List offers from database")
    list_parser.add_argument("--status", type=str, help="Filter by status (new, claimed, shipped, expired, rejected, captcha_blocked)")
    list_parser.add_argument("--limit", type=int, default=50, help="Max offers to show (default: 50)")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")
    list_parser.add_argument("--type", type=str, default="all", choices=["freebie", "contest", "all"],
                            help="Filter by offer type (default: all)")

    # show
    show_parser = subparsers.add_parser("show", help="Show offer details")
    show_parser.add_argument("id", type=int, help="Offer ID")

    # test-email
    subparsers.add_parser("test-email", help="Test Guerrilla Mail integration")

    # claim-pending
    claim_pending_parser = subparsers.add_parser("claim-pending", help="Claim all pending (new) offers, skipping CAPTCHA")
    claim_pending_parser.add_argument("--limit", type=int, default=50, help="Max offers to claim (default: 50)")

    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Initialize database
    try:
        init_db()
    except Exception as e:
        print(f"Failed to initialize database: {e}", file=sys.stderr)
        return 1

    if not args.command:
        parser.print_help()
        return 0

    # Route to command
    commands = {
        "scan": cmd_scan,
        "claim": cmd_claim,
        "full": cmd_full,
        "claim-pending": cmd_claim_pending,
        "stats": cmd_stats,
        "list": cmd_list,
        "show": cmd_show,
        "test-email": cmd_test_email,
    }

    handler = commands.get(args.command)
    if handler:
        try:
            return handler(args)
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return 130
        except Exception as e:
            logging.getLogger(__name__).exception(f"Command '{args.command}' failed")
            from rich.console import Console
            Console(stderr=True).print(f"[red]Error: {e}[/red]")
            return 1
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

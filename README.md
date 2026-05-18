# 🔍 Freebie Hunter

Autonomous free sample and freebie discovery system. Scrapes Canadian-focused freebie sites, filters for relevance, generates disposable emails, and can auto-fill signup forms via browser automation.

**Currently finding ~10 new Canada-available offers per scan from freebie aggregators.**

## Features

- **Multi-source scraping** — Canadian Free Stuff, Freebies Canada, Reddit r/freebiesCanada, r/freebies, Slickdeals Freebies
- **Canada-focused filtering** — Automatically detects Canada availability and cross-border offers
- **Smart scoring** — Ranks offers by relevance, estimated value, and exploit potential (multi-account, per-household loopholes)
- **Disposable email** — Guerrilla Mail API integration for signups (works from Canadian IPs)
- **Deduplication** — SQLite-backed storage prevents duplicate offer listing across runs
- **Auto-signup** — Playwright-based browser automation for form filling (CAPTCHA-aware, skips when CAPTCHA detected)
- **Exploit detection** — Flags offers with "no limit", "per household", referral multipliers, or other multi-sample opportunities

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/freebie-hunter.git
cd freebie-hunter
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For auto-signup, install Playwright browsers:
```bash
playwright install chromium
```

## Usage

```bash
freebie-hunter scan         # Find new offers, show digest
freebie-hunter scan --json  # Machine-readable output
freebie-hunter claim ID     # Auto-fill signup form for offer #ID
freebie-hunter full         # Scan + claim all eligible offers
freebie-hunter stats        # Database statistics
freebie-hunter list         # View all saved offers
freebie-hunter show ID      # Details for a specific offer
freebie-hunter test-email   # Verify Guerrilla Mail integration
```

## Profile Setup

For auto-signup, create a profile file with your mailing info:

```bash
cat > ~/.freebie-hunter-profile.json << 'EOF'
{
  "name": "Your Name",
  "address": "123 Main St",
  "city": "Your City",
  "province": "ON",
  "postal_code": "A1A 1A1",
  "country": "Canada",
  "phone": ""
}
EOF
```

Or use environment variables: `FREEBIE_HUNTER_NAME`, `FREEBIE_HUNTER_ADDRESS`, etc.

Profile data is **never** committed to the repository.

## Architecture

```
freebie_hunter/
├── scraper.py      # Source-specific HTML/JSON parsers (5 sources)
├── filter.py       # Canada detection, value scoring, dedup, exploit detection
├── email_gen.py    # Guerrilla Mail REST API wrapper
├── signup.py       # Playwright browser automation for form filling
├── database.py     # SQLite (WAL mode) — offers, emails, run log
├── digest.py       # Rich terminal + Discord + JSON output
├── config.py       # Sources, keywords, API endpoints
└── main.py         # CLI entry point with 7 subcommands
```

## Why Guerrilla Mail?

Popular disposable email CLI tools (`tmpmail`, `tempmail-python`) depend on 1secmail.com — which is **403-blocked from Canadian IPs**. Guerrilla Mail's REST API works from Canada, requires no API key, supports HTML email, and is scriptable in ~20 lines.

## Canadian Availability

| Source | Region | Status |
|--------|--------|--------|
| Canadian Free Stuff | Canada only | ✅ Active May 2026 |
| Freebies Canada | Canada only | ⚠️ Low volume, expired SSL |
| r/freebiesCanada | Canada only | ⚠️ Reddit API intermittent |
| r/freebies | Mixed (filtered) | ⚠️ Reddit API intermittent |
| Slickdeals Freebies | US mostly | ⚠️ Rarely has Canada offers |

## Limitations

- **Reddit API is flaky** — r/freebiesCanada and r/freebies both occasionally return 500 errors from Reddit's infrastructure. The scraper handles these gracefully.
- **Auto-signup needs Playwright** — `playwright install chromium` required (one-time download of ~150MB)
- **No CAPTCHA solving** — Forms with CAPTCHAs are detected and skipped. Manual intervention required.
- **Canadian freebie ecosystem is small** — Most freebie aggregators are US-focused. The ~3 Canada-specific sources are the core value.

## Roadmap

- [ ] Add more Canadian-specific sources
- [ ] Email verification link auto-clicking
- [ ] Delivery tracking (shipping notifications)
- [ ] Per-offer notes and tagging
- [ ] Discord bot integration for on-demand scanning
- [ ] Multi-account rotation for exploit offers

## License

MIT — see [LICENSE](LICENSE) for details.

## Contributing

This is a personal project that filled a gap in the open-source ecosystem. The freebie automation space is dominated by game-claimers (Epic Games, Prime Gaming) — physical sample hunting is untouched. PRs welcome!

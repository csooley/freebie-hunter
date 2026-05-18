"""Browser-based form filling for freebie signups using Playwright."""

import logging
import re
import time
from typing import Optional

from freebie_hunter.config import get_profile, CONTEST_EMAIL, STQ_PATTERNS
from freebie_hunter.email_gen import GuerrillaMail

logger = logging.getLogger(__name__)

# Common form field name patterns to look for
FIELD_PATTERNS = {
    "first_name": [r"first.?name", r"fname", r"given.?name", r"forename"],
    "last_name": [r"last.?name", r"lname", r"surname", r"family.?name"],
    "full_name": [r"full.?name", r"name"],
    "email": [r"email", r"e.?mail", r"mail"],
    "confirm_email": [r"confirm.?email", r"verify.?email", r"retype.?email"],
    "address": [r"address", r"street", r"addr"],
    "address2": [r"address.?2", r"apt", r"suite", r"unit", r"apt"],
    "city": [r"city", r"town", r"municipality"],
    "province": [r"province", r"state", r"region"],
    "postal_code": [r"postal.?code", r"zip.?code", r"zip", r"postcode"],
    "country": [r"country", r"nation"],
    "phone": [r"phone", r"telephone", r"mobile", r"cell", r"tel"],
    "birth_date": [r"birth", r"dob", r"date.?of.?birth"],
    "gender": [r"gender", r"sex"],
    "age_verify": [r"age", r"over[\s-]*\d+", r"years[\s-]*old"],
    "skill_test": [r"skill", r"stq", r"math", r"answer"],
    "province_select": [r"province", r"state", r"region"],
}


def _find_field(page, patterns: list[str]):
    """Find a form field by trying various selectors based on patterns."""
    all_inputs = page.locator("input, select, textarea")

    for i in range(all_inputs.count()):
        try:
            element = all_inputs.nth(i)
            # Check name attribute
            name = element.get_attribute("name") or ""
            id_attr = element.get_attribute("id") or ""
            placeholder = element.get_attribute("placeholder") or ""
            label = ""
            aria_label = element.get_attribute("aria-label") or ""

            # Get label text via label element
            label_id = element.get_attribute("id") or ""
            if label_id:
                try:
                    label_el = page.locator(f"label[for='{label_id}']")
                    if label_el.count() > 0:
                        label = label_el.text_content() or ""
                except Exception:
                    pass

            combined = f"{name} {id_attr} {label} {placeholder} {aria_label}".lower()

            for pattern in patterns:
                if re.search(pattern, combined, re.IGNORECASE):
                    return element
        except Exception:
            continue

    return None


def _fill_field(page, patterns: list[str], value: str) -> bool:
    """Find a field matching patterns and fill it with value.

    Returns True if a field was found and filled.
    """
    if not value:
        return False

    field = _find_field(page, patterns)
    if field:
        try:
            # Check if it's a select element
            tag = field.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                # Try to select an option containing the value
                field.select_option(label=re.compile(re.escape(value), re.IGNORECASE))
            else:
                field.click()
                field.fill("")
                field.type(value, delay=50)
            logger.debug(f"Filled field matching {patterns[0]} with '{value}'")
            return True
        except Exception as e:
            logger.debug(f"Failed to fill field: {e}")
    return False


def _detect_captcha(page) -> bool:
    """Detect if a CAPTCHA is present on the page.

    Returns True if CAPTCHA detected.
    """
    captcha_indicators = [
        "captcha",
        "recaptcha",
        "hcaptcha",
        "g-recaptcha",
        "i am not a robot",
        "verify you are human",
        "are you a human",
        "cloudflare",
        "challenge",
        "turnstile",
    ]

    try:
        page_text = page.content().lower()
        for indicator in captcha_indicators:
            if indicator in page_text:
                return True

        # Check for iframes with captcha
        iframes = page.locator("iframe[src*='captcha'], iframe[src*='recaptcha'], iframe[src*='hcaptcha']")
        if iframes.count() > 0:
            return True

    except Exception:
        pass

    return False


def _detect_required_fields(page) -> set[str]:
    """Detect which fields are actually required on the form.

    Returns set of field keys that appear to be required.
    """
    required = set()
    try:
        required_elements = page.locator("[required], [aria-required='true'], input[type='email']")
        for i in range(min(required_elements.count(), 50)):
            element = required_elements.nth(i)
            name = (element.get_attribute("name") or "").lower()
            id_attr = (element.get_attribute("id") or "").lower()

            for field_name, patterns in FIELD_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, name) or re.search(pattern, id_attr):
                        required.add(field_name)
                        break
    except Exception as e:
        logger.debug(f"Error detecting required fields: {e}")

    return required


def _detect_submit_button(page):
    """Find the submit button on a form."""
    selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Submit')",
        "button:has-text('Send')",
        "button:has-text('Sign Up')",
        "button:has-text('Register')",
        "button:has-text('Get Free')",
        "button:has-text('Claim')",
        "button:has-text('Order')",
        "button:has-text('Continue')",
        "button:has-text('Next')",
        "a:has-text('Submit')",
    ]

    for selector in selectors:
        try:
            element = page.locator(selector).first
            if element.count() > 0:
                return element
        except Exception:
            continue

    return None


def solve_stq(page) -> bool:
    """Detect and solve a Canadian skill-testing math question on the page.

    Looks for text like 'What is 15 x 4 + 12?' or 'Skill-testing question: (8 * 3) - 5'
    Parses the arithmetic, computes answer, fills the answer field.
    Returns True if STQ was found and solved.
    """
    try:
        page_text = page.content().lower()
    except Exception:
        return False

    # Check if any STQ pattern matches
    found_pattern = False
    for pattern in STQ_PATTERNS:
        if re.search(pattern, page_text, re.IGNORECASE):
            found_pattern = True
            break
    if not found_pattern:
        return False

    logger.info("Skill-testing question detected, attempting to solve...")

    # Extract math expression: handles "X * Y + Z", "X x Y + Z - W", "X multiplied by Y plus Z"
    # First try standard arithmetic form
    expr_match = re.search(
        r"(\d+)\s*[\*\u00d7x]\s*(\d+)\s*[\+\-]\s*(\d+)(?:\s*[\+\-]\s*(\d+))?",
        page_text, re.IGNORECASE
    )
    if not expr_match:
        # Try word form: "X multiplied by Y plus Z"
        expr_match = re.search(
            r"(\d+)\s*(?:multiplied\s*by|times)\s*(\d+)\s*(?:plus|add)\s*(\d+)(?:\s*(?:minus|subtract)\s*(\d+))?",
            page_text, re.IGNORECASE
        )

    if not expr_match:
        logger.debug("Could not extract math expression from STQ")
        return False

    groups = expr_match.groups()
    a = int(groups[0])
    b = int(groups[1])
    c = int(groups[2])
    d = int(groups[3]) if groups[3] else None

    # Compute with BODMAS: multiplication first, then left-to-right for +/-
    result = a * b

    # Determine the first operator (+ or -) between the multiplication and next num
    full_expr = expr_match.group(0)
    op_match = re.search(r"[*x\u00d7]\s*\d+\s*([+\-])", full_expr, re.IGNORECASE)
    first_op = op_match.group(1) if op_match else "+"

    if first_op == "+":
        result += c
    else:
        result -= c

    if d is not None:
        op_match2 = re.search(rf"{c}\s*([+\-])\s*{d}", full_expr)
        second_op = op_match2.group(1) if op_match2 else "+"
        if second_op == "+":
            result += d
        else:
            result -= d

    logger.info(f"STQ: computed answer = {result}")

    # Find the answer input field and fill it
    answer_str = str(result)
    try:
        # Try finding input near the STQ text by looking at inputs on page
        all_inputs = page.locator("input[type='text'], input:not([type])")
        for i in range(min(all_inputs.count(), 30)):
            try:
                el = all_inputs.nth(i)
                name = (el.get_attribute("name") or "").lower()
                id_attr = (el.get_attribute("id") or "").lower()
                placeholder = (el.get_attribute("placeholder") or "").lower()
                combined = f"{name} {id_attr} {placeholder}"

                # Look for skill/math/answer related field names
                if any(kw in combined for kw in ["answer", "skill", "math", "stq", "result", "solution"]):
                    el.click()
                    el.fill("")
                    el.type(answer_str, delay=50)
                    logger.info(f"STQ: filled answer '{answer_str}' into field '{name or id_attr}'")
                    return True
            except Exception:
                continue

        # Fallback: try to find the input closest to the STQ text
        for i in range(min(all_inputs.count(), 30)):
            try:
                el = all_inputs.nth(i)
                el.click()
                el.fill("")
                el.type(answer_str, delay=50)
                logger.info(f"STQ: filled answer '{answer_str}' into fallback field")
                return True
            except Exception:
                continue

        logger.warning("STQ: could not find answer input field")
    except Exception as e:
        logger.warning(f"STQ: error filling answer: {e}")

    return True  # We found and solved the math, even if filling failed


def signup_offer(
    offer_url: str,
    email_address: str = None,
    profile: dict = None,
    dry_run: bool = False,
    email_type: str = "disposable",
) -> dict:
    """Attempt to sign up for an offer using Playwright.

    Args:
        offer_url: URL of the offer signup page.
        email_address: Email to use. If None, generates one via Guerrilla Mail
                       (when email_type='disposable') or uses CONTEST_EMAIL.
        profile: Dict with profile data. If None, uses config.PROFILE.
        dry_run: If True, don't actually submit.
        email_type: 'disposable' (Guerrilla Mail) or 'persistent' (CONTEST_EMAIL).

    Returns:
        Dict with: success, email_used, captcha_detected, error, confirmation_text
    """
    from playwright.sync_api import sync_playwright

    if profile is None:
        profile = get_profile() or {}

    result = {
        "success": False,
        "email_used": email_address,
        "captcha_detected": False,
        "error": None,
        "confirmation_text": "",
    }

    # Generate email if needed
    gm = None
    if not email_address:
        if email_type == "persistent":
            email_address = CONTEST_EMAIL
            result["email_used"] = email_address
            logger.info(f"Using persistent email: {email_address}")
        else:
            try:
                gm = GuerrillaMail()
                email_address = gm.get_email_address()
                result["email_used"] = email_address
                logger.info(f"Generated email: {email_address}")
            except Exception as e:
                result["error"] = f"Failed to generate email: {e}"
                return result

    logger.info(f"Attempting signup for: {offer_url}")
    logger.info(f"Using email: {email_address}")
    if dry_run:
        logger.info("DRY RUN: Will not submit form")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Navigate to offer
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)  # Let any JS load

            # Check for CAPTCHA
            if _detect_captcha(page):
                logger.warning("CAPTCHA detected, skipping signup")
                result["captcha_detected"] = True
                browser.close()
                return result

            # Detect required fields
            required = _detect_required_fields(page)
            logger.debug(f"Required fields: {required}")

            # Fill form fields
            field_fill_count = 0

            # Email (always try)
            if _fill_field(page, FIELD_PATTERNS["email"], email_address):
                field_fill_count += 1
            if "confirm_email" not in required:
                _fill_field(page, FIELD_PATTERNS["confirm_email"], email_address)

            # Name
            if profile.get("name"):
                # Try full name first
                if _fill_field(page, FIELD_PATTERNS["full_name"], profile["name"]):
                    field_fill_count += 1
                else:
                    # Try first + last separately
                    name_parts = profile["name"].rsplit(" ", 1)
                    if len(name_parts) == 2:
                        if _fill_field(page, FIELD_PATTERNS["first_name"], name_parts[0]):
                            field_fill_count += 1
                        if _fill_field(page, FIELD_PATTERNS["last_name"], name_parts[1]):
                            field_fill_count += 1

            # Address
            if profile.get("address"):
                if _fill_field(page, FIELD_PATTERNS["address"], profile["address"]):
                    field_fill_count += 1
            if profile.get("address2"):
                _fill_field(page, FIELD_PATTERNS["address2"], profile["address2"])

            # City
            if profile.get("city"):
                if _fill_field(page, FIELD_PATTERNS["city"], profile["city"]):
                    field_fill_count += 1

            # Province/State (try both text and select)
            if profile.get("province"):
                _fill_field(page, FIELD_PATTERNS["province"], profile["province"])

            # Postal code
            if profile.get("postal_code"):
                if _fill_field(page, FIELD_PATTERNS["postal_code"], profile["postal_code"]):
                    field_fill_count += 1

            # Country
            if profile.get("country"):
                _fill_field(page, FIELD_PATTERNS["country"], profile["country"])

            # Phone (only if required)
            if "phone" in required and profile.get("phone"):
                _fill_field(page, FIELD_PATTERNS["phone"], profile["phone"])

            logger.info(f"Filled {field_fill_count} fields")

            # Attempt to solve any skill-testing question (Canadian law requirement for contests)
            stq_solved = solve_stq(page)
            if stq_solved:
                logger.info("Skill-testing question handled")

            # Submit the form
            if not dry_run:
                submit_btn = _detect_submit_button(page)
                if submit_btn:
                    # Re-check for CAPTCHA (sometimes loads after filling)
                    if _detect_captcha(page):
                        logger.warning("CAPTCHA appeared after form fill, skipping")
                        result["captcha_detected"] = True
                        browser.close()
                        return result

                    submit_btn.click()
                    time.sleep(3)  # Wait for submission

                    # Check for confirmation
                    page_text = page.content().lower()
                    confirmation_keywords = [
                        "thank you", "thanks", "confirmed", "submitted",
                        "on its way", "shipping", "you will receive",
                        "check your email", "verify your email",
                    ]
                    for keyword in confirmation_keywords:
                        if keyword in page_text:
                            result["success"] = True
                            break

                    result["confirmation_text"] = page.text_content()[:500]
                    logger.info(f"Signup submitted, success={result['success']}")
                else:
                    result["error"] = "Could not find submit button"
                    logger.warning("No submit button found")
            else:
                logger.info("DRY RUN: Skipped form submission")
                result["success"] = True  # Dry run always "succeeds"

            browser.close()

    except ImportError:
        result["error"] = "Playwright not installed. Run: playwright install chromium"
        logger.error(result["error"])
    except Exception as e:
        result["error"] = f"Signup failed: {e}"
        logger.error(f"Signup exception: {e}")

    return result

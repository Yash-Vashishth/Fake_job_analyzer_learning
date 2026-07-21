"""
Job Posting Authenticity Parameters
- Domain legitimacy score
- Posting urgency language index
- Contact info verifiability score
- Cross-platform consistency score
"""
import re
from Levenshtein import distance as lev_distance


# India-specific fraud signal phrases
URGENCY_PHRASES = [
    "registration fee", "processing fee", "security deposit", "refundable deposit",
    "training fee", "joining fee", "courier charges", "document verification fee",
    "pay before joining", "online payment", "google pay", "paytm", "phonepe",
    "immediate joining", "join immediately", "urgent requirement",
    "no interview", "no experience required", "work from home guaranteed",
    "limited seats", "offer expires", "act fast", "don't delay",
    "100% job guarantee", "guaranteed placement", "100% salary",
    "earn lakhs", "earn in thousands daily",
]

PAYMENT_REQUEST_PHRASES = [
    "registration fee", "processing fee", "security deposit",
    "training fee", "joining fee", "document fee", "courier",
    "pay before", "advance payment", "upi", "google pay", "paytm",
]

FREE_EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com",
    "rediffmail.com", "ymail.com", "live.com", "aol.com", "protonmail.com",
]

INDIAN_CORPORATE_INDICATORS = [
    r"\+91[\s\-]?\d{10}",          # Indian mobile with country code
    r"0\d{2,4}[\s\-]\d{6,8}",     # Indian landline
    r"cin\s*[:\-]\s*[lu]\d{5}",   # CIN number
    r"gst\s*[:\-]\s*\d{2}[a-z]{5}\d{4}[a-z]\d[z][a-z\d]",  # GST number
]


def analyze(text: str = "", company_domain: str = "", contact_domain: str = "") -> dict:
    text_lower = text.lower()

    domain_result = _analyze_domain(company_domain, contact_domain, text_lower)
    urgency_result = _analyze_urgency(text_lower)
    contact_result = _analyze_contact(text_lower, contact_domain)
    cross_result = _analyze_cross_platform(text_lower, company_domain)

    return {
        "domain_legitimacy": domain_result,
        "urgency_language": urgency_result,
        "contact_verifiability": contact_result,
        "cross_platform": cross_result,
    }


def _analyze_domain(company_domain: str, contact_domain: str, text: str) -> dict:
    # Extract email domains from text if not provided
    if not contact_domain:
        emails = re.findall(r"[\w.\-]+@([\w.\-]+\.\w+)", text)
        if emails:
            contact_domain = emails[0]

    if not company_domain and not contact_domain:
        return {
            "score": 5,
            "reason": "No domain information provided; cannot perform domain legitimacy check",
            "applicable": False
        }

    if not company_domain:
        is_free = any(contact_domain.lower().endswith(d) for d in FREE_EMAIL_DOMAINS)
        score = 2 if is_free else 5
        reason = (
            f"Contact domain '{contact_domain}' is a free email provider — highly suspicious for official HR communication; legitimate Indian companies use corporate email domains"
            if is_free
            else f"Contact domain '{contact_domain}' found but no company domain provided for comparison"
        )
        return {"score": score, "reason": reason, "applicable": True}

    company_root = _extract_root(company_domain)
    contact_root = _extract_root(contact_domain) if contact_domain else ""

    # Check free email first
    is_free = any(contact_domain.lower().endswith(d) for d in FREE_EMAIL_DOMAINS) if contact_domain else False
    if is_free:
        return {
            "score": 1,
            "reason": f"Contact email uses free provider '{contact_domain}' while official domain is '{company_domain}'; no legitimate company sends offer letters from Gmail/Yahoo",
            "applicable": True
        }

    if not contact_root:
        return {
            "score": 5,
            "reason": f"Company domain '{company_domain}' provided but no contact domain found in offer to compare",
            "applicable": True
        }

    if company_root == contact_root:
        return {
            "score": 10,
            "reason": f"Contact domain '{contact_domain}' matches official company domain '{company_domain}' — exact match, high legitimacy",
            "applicable": True
        }

    # Edit distance check
    edit_dist = lev_distance(company_root, contact_root)
    if edit_dist <= 2:
        score = 1
        reason = f"Typosquat detected: edit distance={edit_dist} between '{company_root}' and '{contact_root}' — classic domain spoofing (e.g. infosys → inf0sys)"
    elif edit_dist <= 5:
        score = 3
        reason = f"Suspicious domain similarity: edit distance={edit_dist} between '{company_root}' and '{contact_root}'; possible brand impersonation"
    else:
        score = 2
        reason = f"Contact domain '{contact_domain}' is unrelated to company domain '{company_domain}' (edit distance={edit_dist}); likely fraudulent"

    return {"score": score, "reason": reason, "applicable": True}


def _extract_root(domain: str) -> str:
    """Extract root domain without TLD"""
    domain = domain.lower().strip()
    domain = re.sub(r"^(https?://)?(www\.)?", "", domain)
    parts = domain.split(".")
    if len(parts) >= 2:
        # Handle .co.in, .net.in, etc.
        if parts[-1] in ("in", "uk", "au") and len(parts) >= 3:
            return parts[-3]
        return parts[-2]
    return domain


def _analyze_urgency(text: str) -> dict:
    found_urgent = [p for p in URGENCY_PHRASES if p in text]
    found_payment = [p for p in PAYMENT_REQUEST_PHRASES if p in text]

    if found_payment:
        score = 0
        reason = f"PAYMENT REQUEST detected — critical India-specific fraud signal. Phrases found: {found_payment[:3]}. No legitimate employer asks for fees before/during onboarding"
    elif len(found_urgent) >= 3:
        score = 1
        reason = f"Multiple urgency/pressure phrases detected ({len(found_urgent)} hits): {found_urgent[:4]}. Consistent with social-engineering tactics in Indian job scams"
    elif len(found_urgent) >= 1:
        score = 4
        reason = f"Urgency language present: {found_urgent[:2]}. Mild pressure tactics detected; requires corroboration with other signals"
    else:
        score = 9
        reason = "No urgency or payment-request language detected; communication tone appears professional and measured"

    return {"score": score, "reason": reason, "applicable": True}


def _analyze_contact(text: str, contact_domain: str) -> dict:
    score_parts = []
    notes = []

    # Check for landline number (strong corporate indicator)
    has_landline = bool(re.search(r"0\d{2,4}[\s\-]\d{6,8}", text))
    has_cin = bool(re.search(r"\bcin\b", text, re.IGNORECASE))
    has_gst = bool(re.search(r"\bgst\b.*\d{2}[A-Z]{5}\d{4}", text, re.IGNORECASE))
    has_mobile_only = bool(re.search(r"\b[6-9]\d{9}\b", text)) and not has_landline

    # Check for structured corporate address
    has_address = bool(re.search(r"(floor|building|tower|plot|sector|phase|industrial area)", text, re.IGNORECASE))

    # HR name format (first + last name before "HR" or "Manager")
    has_hr_name = bool(re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+\s*(HR|Human Resources|Manager|Executive)", text))

    if has_landline:
        score_parts.append(3)
        notes.append("landline number present")
    elif has_mobile_only:
        score_parts.append(-2)
        notes.append("mobile-only contact (no landline) — suspicious")

    if has_cin:
        score_parts.append(2)
        notes.append("CIN reference found")
    if has_gst:
        score_parts.append(2)
        notes.append("GST number found")
    if has_address:
        score_parts.append(1)
        notes.append("physical address present")
    if has_hr_name:
        score_parts.append(1)
        notes.append("named HR contact present")

    # Free email penalty
    if contact_domain and any(contact_domain.endswith(d) for d in FREE_EMAIL_DOMAINS):
        score_parts.append(-3)
        notes.append("free email provider used")

    raw = 5 + sum(score_parts)
    final_score = max(0, min(10, raw))

    if not notes:
        reason = "No structured contact information found in text; missing landline, CIN, and named HR contact are all red flags"
    else:
        reason = f"Contact info signals: {', '.join(notes)}. Score adjusted to {final_score}/10"

    return {"score": final_score, "reason": reason, "applicable": True}


def _analyze_cross_platform(text: str, company_domain: str) -> dict:
    """
    Heuristic check: does the text mention job portals or appear sourced
    from a legitimate channel? Full cross-platform API search is out of scope
    for real-time analysis, but we can check internal signals.
    """
    portal_mentions = re.findall(
        r"\b(naukri|linkedin|indeed|shine|monster|instahyre|hirist|foundit|apna|iimjobs|timesjobs)\b",
        text, re.IGNORECASE
    )
    direct_application = bool(re.search(r"(apply (directly|at|on)|careers page|our website)", text, re.IGNORECASE))
    referral = bool(re.search(r"(referred by|employee referral|referred from)", text, re.IGNORECASE))

    if portal_mentions:
        score = 7
        reason = f"Job posting references known portal(s): {list(set(portal_mentions))}; cross-platform presence is a positive signal"
    elif direct_application or referral:
        score = 6
        reason = "Offer mentions direct application or employee referral channel; acceptable but not independently verifiable"
    elif company_domain:
        score = 5
        reason = f"No portal mentions found; recommend manually checking {company_domain}/careers for role listing"
    else:
        score = 4
        reason = "No cross-platform sourcing signals; cannot verify posting authenticity through external channels"

    return {"score": score, "reason": reason, "applicable": True}

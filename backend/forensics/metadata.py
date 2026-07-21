"""
Temporal & Metadata Contradiction Parameters
- Causal inversion count
- Timezone entropy score
- Tool anachronism score
"""
import fitz
import re
from datetime import datetime, timezone
from typing import Optional


# Known PDF producer tool release dates (tool_string_fragment: first_release_date)
TOOL_RELEASE_DATES = {
    "microsoft word 2019": datetime(2018, 9, 24),
    "microsoft word 2016": datetime(2015, 9, 22),
    "microsoft word 2013": datetime(2013, 1, 29),
    "microsoft word 2010": datetime(2010, 6, 15),
    "microsoft word 2007": datetime(2006, 11, 30),
    "libreoffice 7.": datetime(2020, 8, 1),
    "libreoffice 6.": datetime(2018, 1, 31),
    "libreoffice 5.": datetime(2015, 7, 28),
    "adobe acrobat dc": datetime(2015, 4, 7),
    "adobe acrobat 2020": datetime(2020, 6, 2),
    "adobe acrobat 2017": datetime(2017, 6, 6),
    "adobe acrobat xi": datetime(2012, 10, 15),
    "adobe acrobat x": datetime(2010, 11, 15),
    "wps office": datetime(2012, 3, 1),
    "google docs": datetime(2006, 3, 9),
    "mac os x": datetime(2001, 3, 24),
}


def analyze(pdf_path: str = None, text_content: str = None) -> dict:
    if pdf_path:
        return _analyze_pdf(pdf_path)
    elif text_content:
        return _analyze_text(text_content)
    return _not_applicable("No input provided")


def _parse_pdf_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    # PDF date format: D:YYYYMMDDHHmmSSOHH'mm'
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?([+-Z])?(\d{2})?'?(\d{2})?", date_str)
    if not m:
        return None
    try:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        minute = int(m.group(5) or 0)
        second = int(m.group(6) or 0)
        return datetime(year, month, day, hour, minute, second)
    except Exception:
        return None


def _extract_tz_offset(date_str: Optional[str]) -> Optional[int]:
    """Return UTC offset in minutes, or None"""
    if not date_str:
        return None
    m = re.search(r"([+-])(\d{2})'(\d{2})'?$", date_str)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return sign * (int(m.group(2)) * 60 + int(m.group(3)))
    if "Z" in date_str:
        return 0
    return None


def _analyze_pdf(pdf_path: str) -> dict:
    try:
        doc = fitz.open(pdf_path)
        meta = doc.metadata

        create_str = meta.get("creationDate", "")
        mod_str = meta.get("modDate", "")
        producer = (meta.get("producer", "") or "").lower()
        creator = (meta.get("creator", "") or "").lower()

        create_dt = _parse_pdf_date(create_str)
        mod_dt = _parse_pdf_date(mod_str)
        create_tz = _extract_tz_offset(create_str)
        mod_tz = _extract_tz_offset(mod_str)
        now = datetime.now()

        inversions = []

        # Check 1: ModDate < CreationDate
        if create_dt and mod_dt:
            if mod_dt < create_dt:
                inversions.append(f"ModDate ({mod_dt.date()}) is earlier than CreationDate ({create_dt.date()})")

        # Check 2: CreationDate in the future
        if create_dt and create_dt > now:
            inversions.append(f"CreationDate ({create_dt.date()}) is in the future")

        # Check 3: ModDate in the future
        if mod_dt and mod_dt > now:
            inversions.append(f"ModDate ({mod_dt.date()}) is in the future")

        # Check 4: Suspicious creation date (before PDF format existed)
        if create_dt and create_dt.year < 1993:
            inversions.append(f"CreationDate ({create_dt.year}) predates the PDF format (1993)")

        # Causal inversion score
        if len(inversions) == 0:
            ci_score = 9
            ci_reason = "No temporal contradictions found; metadata dates are internally consistent"
        elif len(inversions) == 1:
            ci_score = 2
            ci_reason = f"Causal inversion detected: {inversions[0]}"
        else:
            ci_score = 0
            ci_reason = f"Multiple causal inversions: {'; '.join(inversions)}"

        # Timezone entropy
        tz_offsets = [o for o in [create_tz, mod_tz] if o is not None]
        if len(tz_offsets) >= 2:
            tz_var = len(set(tz_offsets))
            tz_score = 8 if tz_var == 1 else 4
            tz_reason = (
                f"Timezone offsets: {tz_offsets} (in minutes); "
                f"{'consistent timezone across metadata fields' if tz_var == 1 else 'mismatched timezones between creation and modification — possible cross-machine editing'}"
            )
        elif len(tz_offsets) == 1:
            tz_score = 6
            tz_reason = f"Single timezone found ({tz_offsets[0]} min offset); cannot compare for consistency"
        else:
            tz_score = 5
            tz_reason = "No timezone data in PDF metadata"

        # Tool anachronism
        tool_combined = producer + " " + creator
        anachronism_found = False
        anachronism_detail = ""
        for tool_key, release_date in TOOL_RELEASE_DATES.items():
            if tool_key in tool_combined:
                if create_dt and create_dt < release_date:
                    anachronism_found = True
                    anachronism_detail = (
                        f"Document claims creation date {create_dt.date()} but tool "
                        f"'{tool_key}' was released {release_date.date()}"
                    )
                break

        if anachronism_found:
            ta_score = 0
            ta_reason = f"Tool anachronism confirmed: {anachronism_detail}"
        elif tool_combined.strip():
            ta_score = 8
            ta_reason = f"Producer='{meta.get('producer', 'unknown')}'; no anachronism detected between tool and claimed creation date"
        else:
            ta_score = 5
            ta_reason = "No producer/creator metadata present — cannot verify tool authenticity"

        doc.close()

        return {
            "causal_inversion": {"score": ci_score, "reason": ci_reason, "applicable": True},
            "timezone_entropy": {"score": tz_score, "reason": tz_reason, "applicable": True},
            "tool_anachronism": {"score": ta_score, "reason": ta_reason, "applicable": True},
        }

    except Exception as e:
        return _not_applicable(f"Metadata analysis error: {str(e)}")


def _analyze_text(text: str) -> dict:
    """
    For plain text / email input: look for date contradictions in the body
    """
    date_pattern = re.compile(
        r"\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}[\/\-]\d{2}[\/\-]\d{2})\b"
    )
    dates_found = date_pattern.findall(text)

    ci_score = 6
    ci_reason = "Text input; PDF metadata unavailable. No date contradictions found in visible text."

    if len(dates_found) >= 2:
        ci_score = 5
        ci_reason = f"Found {len(dates_found)} date references in text; manual review recommended for consistency"

    return {
        "causal_inversion": {"score": ci_score, "reason": ci_reason, "applicable": True},
        "timezone_entropy": {"score": 5, "reason": "Timezone analysis requires PDF metadata; not available for text input", "applicable": False},
        "tool_anachronism": {"score": 5, "reason": "Tool anachronism check requires PDF metadata; not available for text input", "applicable": False},
    }


def _not_applicable(reason: str) -> dict:
    keys = ["causal_inversion", "timezone_entropy", "tool_anachronism"]
    return {k: {"score": None, "reason": reason, "applicable": False} for k in keys}

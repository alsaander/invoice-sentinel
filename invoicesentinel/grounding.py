import logging
import re
from difflib import SequenceMatcher
from typing import List

from invoicesentinel.config import Config
from invoicesentinel.models import LineItem

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> str:
    """Lowercase, strip accents via simple ASCII fold."""
    text = text.lower()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n",
    }
    for a, b in replacements.items():
        text = text.replace(a, b)
    return text


def _significant_words(text: str) -> List[str]:
    """Return words of length >= 4 after tokenization."""
    norm = _tokenize(text)
    return [w for w in re.findall(r"[a-z0-9]+", norm) if len(w) >= 4]


def check_grounding(
    items: List[LineItem],
    raw_text: str,
    cfg: Config,
) -> List[LineItem]:
    """For each item, check that its description is grounded in raw_text
    using word-level matching. Items that fail get severity=UNGROUNDED
    and grounded='false'.

    Scoring strategy:
    1. If the entire description appears as a substring -> grounded.
    2. Tokenize description into significant words (>=4 chars); if
       >= threshold % of them appear in raw_text word set -> grounded.
    3. Fallback: SequenceMatcher ratio on full strings.
    """
    norm_text = _tokenize(raw_text)
    text_words = set(_significant_words(raw_text))
    threshold = cfg.thresholds.grounding_min_score

    for li in items:
        if li.severity in ("PARSE_ERROR",):
            continue

        desc = li.description or ""
        if not desc:
            li.grounded = "false"
            li.severity = "UNGROUNDED"
            continue

        norm_desc = _tokenize(desc)

        # 1. Exact substring of normalized text
        if norm_desc in norm_text:
            li.grounded = "true"
            continue

        desc_words = _significant_words(desc)

        # 2. Word-level token match
        if desc_words:
            match_count = sum(1 for w in desc_words if w in text_words)
            word_score = int(match_count / len(desc_words) * 100)
            if word_score >= threshold:
                li.grounded = "true"
                continue

        # 3. SequenceMatcher fallback
        seq = SequenceMatcher(None, norm_desc, norm_text)
        seq_score = int(seq.ratio() * 100)

        if seq_score >= threshold:
            li.grounded = "true"
        else:
            li.grounded = "false"
            li.severity = "UNGROUNDED"
            logger.info(
                "Item '%s' failed grounding check "
                "(word_score=%d, seq_score=%d < %d) — flagged as UNGROUNDED",
                desc, word_score if desc_words else 0, seq_score, threshold,
            )

    return items


def is_any_ungrounded(items: List[LineItem]) -> bool:
    return any(li.severity == "UNGROUNDED" for li in items)


def all_ungrounded(items: List[LineItem]) -> bool:
    relevant = [li for li in items if li.severity not in ("PARSE_ERROR", "EXTRACTION_FAILED")]
    if not relevant:
        return False
    return all(li.severity == "UNGROUNDED" for li in relevant)

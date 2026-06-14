import csv
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    row: Dict[str, str]
    specificity_score: int
    confidence: str  # "specific" or "broad"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii").lower().strip()


def _keyword_tokens(keyword: str) -> List[str]:
    return [t for t in keyword.split() if t]


def load_reference_prices(path: str) -> List[Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        logger.warning("Reference prices file not found: %s", path)
        return []
    with open(p, newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            keyword = row.get("keyword", "").strip()
            if keyword and not keyword.startswith("#"):
                rows.append(row)
        return rows


def _score_description_match(keyword: str, desc_norm: str) -> int:
    tokens = _keyword_tokens(keyword)
    if not tokens:
        return 0
    score = 0
    for token in tokens:
        if token in desc_norm:
            score += 1
    return score


def find_match(
    description: str,
    category: str,
    prices: List[Dict[str, str]],
) -> Optional[MatchResult]:
    """Find the best reference price match using specificity scoring.

    Scoring:
      1. For each row, count how many keyword tokens appear as substrings
         in the normalized description.
      2. The row with the highest score is the description-based winner.
      3. If no row scores >= 1, fall back to category-based matching
         (keyword in normalized category) — this is the 'broad' fallback.
      4. Ties broken by longer keyword (more specific wins).

    Confidence:
      - 'specific': matched via description with at least one token
      - 'broad': matched via category fallback only
    """
    desc_norm = _normalize(description)
    cat_norm = _normalize(category)

    best_desc: Optional[MatchResult] = None

    for row in prices:
        keyword = _normalize(row.get("keyword", ""))
        if not keyword:
            continue

        score = _score_description_match(keyword, desc_norm)
        if score > 0:
            candidate = MatchResult(
                row=row,
                specificity_score=score,
                confidence="specific",
            )
            if best_desc is None or _is_more_specific(candidate, best_desc):
                best_desc = candidate

    if best_desc is not None:
        return best_desc

    # Category fallback (broad): keyword matches category name
    for row in prices:
        keyword = _normalize(row.get("keyword", ""))
        if not keyword:
            continue
        if keyword in cat_norm:
            return MatchResult(
                row=row,
                specificity_score=0,
                confidence="broad",
            )

    return None


def _is_more_specific(a: MatchResult, b: MatchResult) -> bool:
    """Prefer higher score; if equal, prefer longer keyword (more tokens = more specific)."""
    if a.specificity_score != b.specificity_score:
        return a.specificity_score > b.specificity_score
    kw_a = _normalize(a.row.get("keyword", ""))
    kw_b = _normalize(b.row.get("keyword", ""))
    return len(kw_a) > len(kw_b)


def build_reference_price_block(match: Dict[str, str]) -> str:
    ref_min = match.get("price_min", "")
    ref_max = match.get("price_max", "")
    currency = match.get("currency", "USD")
    return (
        f"Nota: existe un precio de referencia local de {ref_min}-{ref_max} {currency}"
        f" para artículos similares; considéralo como ancla principal."
    )


def format_reference_source(match: Dict[str, str]) -> str:
    keyword = match.get("keyword", "").strip()
    if not keyword:
        keyword = "unknown"
    return f"reference_csv:{keyword}"

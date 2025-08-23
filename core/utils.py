
import re, unicodedata

def normalize_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9%\-_/ ]+", "", s)
    return s.strip()

def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

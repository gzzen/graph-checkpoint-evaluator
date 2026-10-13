"""ValueSpec matching: compare expected values with structured values or scan free text for them."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

# A number as it appears in tool output or prose: optional sign (ASCII or Unicode minus), thousands
# separators, decimals, scientific notation ("1.9E-05", "5.107 x 10^-47") and an optional percent sign.
NUMBER_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<mant>[-−+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[-−+]?\.\d+)"
    r"(?:(?:\s*[eE]\s*(?P<exp>[-−+]?\d+))|(?:\s*[x×*]\s*10\s*\^\s*(?P<exp10>[-−+]?\d+)))?"
    r"(?P<pct>\s*%)?"
)
_EPS = 1e-12


@dataclass(frozen=True)
class NumberHit:
    value: float
    percent: bool
    start: int
    end: int
    raw: str


@dataclass(frozen=True)
class TextHit:
    start: int
    end: int
    raw: str
    value: Any


def scan_numbers(text: str) -> list[NumberHit]:
    hits = []
    for m in NUMBER_RE.finditer(text or ""):
        mant = m.group("mant").replace("−", "-").replace(",", "")
        exp = m.group("exp") or m.group("exp10")
        try:
            value = float(mant) * (10.0 ** int(exp.replace("−", "-")) if exp else 1.0)
        except (ValueError, OverflowError):
            continue
        hits.append(NumberHit(value, bool(m.group("pct")), m.start(), m.end(), m.group(0)))
    return hits


def parse_number(value: Any) -> tuple[float, bool] | None:
    """A float (and whether it was a percentage) if `value` is, or reads as, exactly one number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return (float(value), False) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        hits = scan_numbers(value.strip())
        if len(hits) == 1 and hits[0].raw.strip() == value.strip():
            return hits[0].value, hits[0].percent
    return None


def _decimals(x: float) -> int:
    text = repr(float(x))
    if "e" in text or "E" in text:
        mant, exp = text.lower().split("e")
        frac = len(mant.split(".")[1].rstrip("0")) if "." in mant else 0
        return max(0, frac - int(exp))
    return len(text.split(".")[1].rstrip("0")) if "." in text else 0


def number_tolerance(spec: dict) -> float:
    value = float(spec["value"])
    tols = [_EPS, 1e-9 * abs(value)]
    if spec.get("abs_tol") is not None:
        tols.append(float(spec["abs_tol"]))
    if spec.get("rel_tol") is not None:
        tols.append(float(spec["rel_tol"]) * abs(value))
    if spec.get("decimals") is not None:
        tols.append(0.5 * 10.0 ** -int(spec["decimals"]))
    if spec.get("sig_figs") is not None and value != 0:
        magnitude = math.floor(math.log10(abs(value)))
        tols.append(0.5 * 10.0 ** (magnitude - int(spec["sig_figs"]) + 1))
    return max(tols) * (1 + 1e-9)


def _percent_tolerance(spec: dict) -> float:
    if spec.get("abs_tol") is not None:
        return float(spec["abs_tol"]) + _EPS
    decimals = spec.get("decimals")
    if decimals is None:
        decimals = _decimals(spec["value"])
    return 0.5 * 10.0 ** -int(decimals) * (1 + 1e-9) + _EPS


def _num_ok(spec: dict, x: float, is_percent: bool = False) -> bool:
    kind = spec["type"]
    if kind == "number":
        target = float(spec["value"])
        cands = [x, abs(x)] if spec.get("allow_sign_flip") else [x]
        tol = number_tolerance(spec)
        return any(abs(c - (abs(target) if spec.get("allow_sign_flip") else target)) <= tol for c in cands)
    if kind == "integer":
        return abs(x - round(x)) < 1e-6 and abs(round(x) - int(spec["value"])) <= int(spec.get("tol", 0))
    if kind == "range":
        lo, hi = sorted((float(spec["min"]), float(spec["max"])))
        pad = 1e-9 * max(abs(lo), abs(hi), 1e-300)
        return lo - pad <= x <= hi + pad
    if kind == "percent":
        target, tol = float(spec["value"]), _percent_tolerance(spec)
        cands = [x] if is_percent else [x, x * 100.0]
        return any(abs(c - target) <= tol for c in cands)
    if kind == "set" and spec.get("mode") == "size":
        return abs(x - int(spec["size"])) < 1e-6
    return False


NUMERIC_TYPES = {"number", "integer", "range", "percent"}


def normalize_label(text: str) -> str:
    return re.sub(r"[\W_]+", " ", str(text).lower()).strip()


def _label_variants(spec: dict) -> list[str]:
    return [normalize_label(v) for v in [spec["value"], *spec.get("synonyms", [])] if normalize_label(v)]


def _label_ok(spec: dict, observed: Any) -> bool:
    obs = f" {normalize_label(observed)} "
    return any(f" {v} " in obs for v in _label_variants(spec))


def _as_items(observed: Any) -> list[str] | None:
    if isinstance(observed, (list, tuple, set)):
        return [normalize_label(x) for x in observed]
    if isinstance(observed, str):
        return [normalize_label(x) for x in re.split(r"[,;\n]+", observed) if normalize_label(x)]
    return None


def _set_ok(spec: dict, observed: Any) -> bool:
    mode = spec.get("mode", "exact")
    if mode == "size":
        if isinstance(observed, (int, float)) and not isinstance(observed, bool):
            return _num_ok(spec, float(observed))
        items = _as_items(observed)
        return items is not None and len(set(items)) == int(spec["size"])
    items = _as_items(observed)
    if items is None:
        return False
    got, want = set(items), {normalize_label(x) for x in spec.get("items", [])}
    if mode == "exact":
        return got == want
    if mode == "contains":
        return want <= got
    if mode == "jaccard":
        union = got | want
        return bool(union) and len(got & want) / len(union) >= float(spec.get("threshold", 0.9))
    return False


def _string_ok(spec: dict, observed: Any) -> bool:
    text = str(observed)
    flags = 0 if spec.get("case_sensitive") else re.IGNORECASE
    if spec.get("regex"):
        return re.search(spec["regex"], text, flags) is not None
    if spec.get("case_sensitive"):
        return text.strip() == spec["value"].strip()
    return " ".join(text.lower().split()) == " ".join(spec["value"].lower().split())


def _bool_ok(spec: dict, observed: Any) -> bool:
    if isinstance(observed, bool):
        return observed == spec["value"]
    if isinstance(observed, str):
        norm = observed.strip().lower()
        truthy, falsy = {"true", "yes", "y", "1"}, {"false", "no", "n", "0"}
        return (norm in truthy) if spec["value"] else (norm in falsy)
    return False


def matches(spec: dict, observed: Any) -> bool:
    """Whether a structured observed value (number, string, list, bool) satisfies `spec`.

    A string reads as a number only if it *is* one number ("0.013", "10.6%", "1.9E-05"); use
    `answer_matches` for prose answers that may contain other numbers.
    """
    kind = spec["type"]
    if kind == "any_of":
        return any(matches(opt, observed) for opt in spec["options"])
    if kind in NUMERIC_TYPES:
        parsed = parse_number(observed)
        return parsed is not None and _num_ok(spec, *parsed)
    if kind == "set":
        return _set_ok(spec, observed)
    if kind == "label":
        return observed is not None and not isinstance(observed, (dict, list)) and _label_ok(spec, observed)
    if kind == "string":
        return observed is not None and _string_ok(spec, observed)
    if kind == "boolean":
        return _bool_ok(spec, observed)
    raise ValueError(f"unknown ValueSpec type {kind!r}")


def find_in_text(spec: dict, text: str) -> list[TextHit]:
    """All places in free text where a value satisfying `spec` appears."""
    if not text:
        return []
    kind = spec["type"]
    if kind == "any_of":
        hits: list[TextHit] = []
        for opt in spec["options"]:
            hits += find_in_text(opt, text)
        return sorted(set(hits), key=lambda h: h.start)
    if kind in NUMERIC_TYPES or (kind == "set" and spec.get("mode") == "size"):
        return [TextHit(h.start, h.end, h.raw, h.value) for h in scan_numbers(text) if _num_ok(spec, h.value, h.percent)]
    if kind == "label":
        out = []
        for variant in _label_variants(spec):
            pattern = r"(?<![A-Za-z0-9])" + r"[\W_]+".join(map(re.escape, variant.split())) + r"(?![A-Za-z0-9])"
            out += [TextHit(m.start(), m.end(), m.group(0), m.group(0)) for m in re.finditer(pattern, text, re.IGNORECASE)]
        return sorted(set(out), key=lambda h: h.start)
    if kind == "string":
        pattern = spec.get("regex") or re.escape(spec["value"])
        flags = 0 if spec.get("case_sensitive") else re.IGNORECASE
        return [TextHit(m.start(), m.end(), m.group(0), m.group(0)) for m in re.finditer(pattern, text, flags)]
    if kind == "set":
        want = [normalize_label(x) for x in spec.get("items", [])]
        norm = f" {normalize_label(text)} "
        found = [w for w in want if f" {w} " in norm]
        if want and len(found) / len(want) >= float(spec.get("threshold", 1.0 if spec.get("mode") != "jaccard" else 0.9)):
            return [TextHit(0, len(text), text[:80], found)]
        return []
    return []


def answer_matches(spec: dict, answer: Any) -> bool:
    """Lenient check for a final answer, which may be prose ("p < 2.2e-16", "~35% increase")."""
    if answer is None:
        return False
    if matches(spec, answer):
        return True
    return isinstance(answer, str) and bool(find_in_text(spec, answer))


def is_low_specificity(spec: dict) -> bool:
    """Values too common to accept from free text without name or context support."""
    kind = spec["type"]
    if kind == "any_of":
        return any(is_low_specificity(o) for o in spec["options"])
    if kind == "integer":
        # small counts recur in data previews (days, IDs, tallies); accept them only when labelled
        return abs(int(spec["value"])) < 1000
    if kind == "number":
        v = float(spec["value"])
        # two significant digits (0.26, 2.5) are met by timings, version strings and unrelated statistics
        return (v == int(v) and abs(v) < 10) or number_tolerance(spec) >= 0.01 * max(abs(v), _EPS)
    if kind == "range":
        lo, hi = sorted((float(spec["min"]), float(spec["max"])))
        # e.g. (24, 26): small integer ranges are hit by ordinary tallies (counts of events, genes, rows)
        integer_counts = lo == int(lo) and hi == int(hi) and hi - lo >= 1 and max(abs(lo), abs(hi)) <= 1000
        return lo <= 0 <= hi or integer_counts or (hi - lo) >= 0.1 * max(abs(lo), abs(hi))
    if kind == "percent":
        # whole-percent precision ("5%", "28%") is met by "5-taxon", percentile headers or raw measurements
        return float(spec["value"]) in {0.0, 100.0} or _percent_tolerance(spec) >= 0.5
    if kind == "label":
        return len(normalize_label(spec["value"])) < 4
    if kind == "set":
        return spec.get("mode") == "size" and int(spec.get("size", 0)) < 10
    return kind == "boolean"


def infer_spec(text: str) -> dict:
    """Best-effort ValueSpec from a reference answer string ("0.0002", "(1.50,1.54)", "10.6%", "CD14")."""
    s = str(text).strip()
    m = re.fullmatch(r"\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)", s)
    if m:
        lo, hi = parse_number(m.group(1)), parse_number(m.group(2))
        if lo and hi:
            return {"type": "range", "min": lo[0], "max": hi[0]}
    parsed = parse_number(s)
    if parsed is not None:
        value, pct = parsed
        core = s.rstrip("%").strip().replace(",", "")
        sci = re.search(r"(?:[eE]|10\s*\^)", core)
        if pct:
            return {"type": "percent", "value": value, "decimals": _shown_decimals(core)}
        if sci:
            mantissa = re.split(r"\s*(?:[eE]|[x×*]\s*10\s*\^)", core)[0].lstrip("+-−")
            digits = len(mantissa.replace(".", "").lstrip("0")) or 1
            return {"type": "number", "value": value, "sig_figs": digits}
        if re.fullmatch(r"[-−+]?\d+", core):
            return {"type": "integer", "value": int(value)}
        return {"type": "number", "value": value, "decimals": _shown_decimals(core)}
    return {"type": "label", "value": s}


def _shown_decimals(core: str) -> int:
    return len(core.split(".")[1]) if "." in core else 0

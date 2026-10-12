import pytest

from gce.values import answer_matches, find_in_text, infer_spec, is_low_specificity, matches, scan_numbers


@pytest.mark.parametrize(
    "text, expected",
    [
        ("p.adj: 0.0002045181", [0.0002045181]),
        ("1.9E-05", [1.9e-05]),
        ("5.107 x 10^-47", [5.107e-47]),
        ("4,550 variants", [4550.0]),
        ("log2FC −4.96", [-4.96]),
        ("10.6%", [10.6]),
        ("X-squared = 901.45, df = 4", [901.45, 4.0]),
    ],
)
def test_scan_numbers(text, expected):
    assert [h.value for h in scan_numbers(text)] == pytest.approx(expected)


def test_scan_numbers_ignores_identifiers():
    assert scan_numbers("gene981902at2759 and PA14_09540") == []


def test_number_rounding_aware():
    spec = {"type": "number", "value": 0.0002, "decimals": 4}
    assert matches(spec, 0.0002045181)
    assert not matches(spec, 0.0003246646)


def test_number_sig_figs_scientific():
    spec = {"type": "number", "value": 1.9e-05, "sig_figs": 2}
    assert matches(spec, "1.90E-05")
    assert matches(spec, 1.903e-05)
    assert not matches(spec, 1.847038e-05)


def test_number_exact_without_tolerance():
    assert matches({"type": "number", "value": 2.53}, 2.53)
    assert not matches({"type": "number", "value": 2.53}, 2.531)


def test_integer_and_thousands_separator():
    assert matches({"type": "integer", "value": 4550}, "4,550")
    assert matches({"type": "integer", "value": 166}, 166.0)
    assert not matches({"type": "integer", "value": 166}, 166.4)


def test_range_with_swapped_bounds():
    spec = {"type": "range", "min": 1.13e-10, "max": 1.13e-12}  # bix-54-q4 style
    assert matches(spec, 1.131e-11)
    assert not matches(spec, 1.2e-10)


def test_percent_accepts_fraction_and_percent_forms():
    spec = {"type": "percent", "value": 10.6}
    assert matches(spec, "10.6%")
    assert matches(spec, 0.1058)
    assert matches(spec, 10.62)
    assert not matches(spec, "1.06%")


def test_label_normalization_and_synonyms():
    spec = {"type": "label", "value": "CD14 Monocytes", "synonyms": ["CD14 Mono"]}
    assert matches(spec, "cd14-monocytes")
    assert matches(spec, "CD14 Mono")
    assert not matches(spec, "CD16 Monocytes")
    assert find_in_text(spec, "cell type: CD14 Mono, n=5")


def test_set_modes():
    assert matches({"type": "set", "items": ["Ribosome", "Riboflavin metabolism"]}, "riboflavin metabolism; ribosome")
    assert matches({"type": "set", "items": ["a", "b", "c"], "mode": "contains"}, ["a", "b", "c", "d"])
    assert matches({"type": "set", "size": 3, "mode": "size"}, ["x", "y", "z"])
    assert matches({"type": "set", "items": list("abcdefghij"), "mode": "jaccard", "threshold": 0.8}, list("abcdefghi"))


def test_find_in_text_positions():
    hits = find_in_text({"type": "integer", "value": 846}, "final_blood_vs_baseline_blood   846\nother 11663")
    assert [h.value for h in hits] == [846.0]


def test_answer_matches_prose():
    assert answer_matches({"type": "string", "regex": r"p(?:-value)?\s*<\s*2\.2e-16"}, "p-value < 2.2e-16")
    assert answer_matches({"type": "range", "min": 33, "max": 36}, "about a 35% increase")
    assert not answer_matches({"type": "integer", "value": 7}, None)


def test_any_of():
    spec = {"type": "any_of", "options": [{"type": "integer", "value": 677}, {"type": "integer", "value": 679}]}
    assert matches(spec, 679) and not matches(spec, 680)


@pytest.mark.parametrize(
    "text, spec",
    [
        ("0.0002", {"type": "number", "value": 0.0002, "decimals": 4}),
        ("1.9E-05", {"type": "number", "value": 1.9e-05, "sig_figs": 2}),
        ("(1.50,1.54)", {"type": "range", "min": 1.5, "max": 1.54}),
        ("10.6%", {"type": "percent", "value": 10.6, "decimals": 1}),
        ("4,550", {"type": "integer", "value": 4550}),
        ("CD14 Monocytes", {"type": "label", "value": "CD14 Monocytes"}),
    ],
)
def test_infer_spec(text, spec):
    assert infer_spec(text) == spec


def test_low_specificity():
    assert is_low_specificity({"type": "integer", "value": 2})
    assert is_low_specificity({"type": "range", "min": 0.8, "max": 1.0})
    assert is_low_specificity({"type": "range", "min": 24, "max": 26})  # integer tallies fall in by chance
    assert not is_low_specificity({"type": "range", "min": 157500, "max": 158000})
    assert not is_low_specificity({"type": "integer", "value": 4550})
    assert not is_low_specificity({"type": "number", "value": 0.0002045, "sig_figs": 4})
    assert is_low_specificity({"type": "number", "value": 0.21, "decimals": 2})  # "0.207704 secs" is not a result
    assert is_low_specificity({"type": "percent", "value": 5.0, "decimals": 0})  # "5-taxon"
    assert not is_low_specificity({"type": "percent", "value": 15.6, "decimals": 1})
    assert not is_low_specificity({"type": "number", "value": 1.5242, "abs_tol": 0.005})

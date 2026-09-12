"""Checking a paper's deposited grants against the awarding body.

The award numbers in a sidecar are the publisher's, deposited to
CrossRef as arrays parallel to the funder names — and on papers with
several funders they arrive mis-paired, so a Wellcome number ends up
filed under an Ontario ministry. GtR records the award itself, which
makes the deposit checkable for UK grants.

Measured over the reference library: 679 award numbers are not
UKRI-shaped and cannot be checked this way, 31 are UKRI-shaped but
unknown to GtR, and of the 60 that GtR does hold, it contradicts
none. The mis-pairing is real but concentrated in records no public
database will settle.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import funding, gtr

PROJECT = {"reference": "BB/L007010/1", "funder": "BBSRC",
           "title": "CCP4 Grant Renewal", "pi_name": "Garib Murshudov",
           "amount_gbp": 339104, "start": 2014, "end": 2019}


def paper(grants):
    return {"title": "T", "year": 2020, "authorships": [], "grants": grants}


# --- which references are even askable -------------------------------

@pytest.mark.parametrize("ref", [
    "BB/L007010/1", "MR/J002976/1", "EP/G037280/1", "ST/R002754/1",
])
def test_ukri_references_are_recognised(ref):
    assert gtr.looks_like_ukri_reference(ref)


@pytest.mark.parametrize("ref", [
    "R01GM109046", "2r01dk116780-05", "086185/Z/08/Z", "115766", "", None,
])
def test_everything_else_is_left_alone(ref):
    """Asking GtR about an NIH number is a request that can only
    fail."""
    assert not gtr.looks_like_ukri_reference(ref)


def test_references_are_compared_case_and_space_insensitively():
    assert gtr.normalise_reference(" bb/l007010/1 ") == "BB/L007010/1"


# --- reconciling the two ways a funder is named ----------------------

def test_an_acronym_and_its_long_form_are_one_funder():
    """GtR answers "BBSRC"; CrossRef deposits carry the long form,
    and every correct record would look wrong without this."""
    assert funding.funder_matches(
        "Biotechnology and Biological Sciences Research Council", "BBSRC")
    assert funding.funder_matches("Medical Research Council", "MRC")


def test_two_different_funders_do_not_match():
    assert not funding.funder_matches("Wellcome Trust", "BBSRC")


# --- verdicts ---------------------------------------------------------

def test_a_deposit_the_awarding_body_agrees_with():
    out = funding.check_grants(
        paper([{"funder": "Biotechnology and Biological Sciences "
                          "Research Council", "award_id": "BB/L007010/1"}]),
        lookup=lambda ref: PROJECT)

    assert out[0]["verdict"] == "confirmed"
    assert out[0]["project"]["pi_name"] == "Garib Murshudov"


def test_a_deposit_it_contradicts():
    out = funding.check_grants(
        paper([{"funder": "Wellcome Trust", "award_id": "BB/L007010/1"}]),
        lookup=lambda ref: PROJECT)

    assert out[0]["verdict"] == "wrong-funder"


def test_a_cross_council_fund_is_not_a_contradiction():
    """GCRF money was spent through the councils, so an ST/ reference
    deposited as STFC is right even when GtR names GCRF as the lead
    funder. Both names are true."""
    gcrf = dict(PROJECT, reference="ST/R002754/1", funder="GCRF")

    out = funding.check_grants(
        paper([{"funder": "Science and Technology Facilities Council",
                "award_id": "ST/R002754/1"}]),
        lookup=lambda ref: gcrf)

    assert out[0]["verdict"] == "confirmed"


def test_a_non_ukri_award_is_unknowable_not_wrong():
    out = funding.check_grants(
        paper([{"funder": "National Institutes of Health",
                "award_id": "R01GM109046"}]),
        lookup=lambda ref: pytest.fail("should not have been asked"))

    assert out[0]["verdict"] == "not-ukri"


def test_a_ukri_shaped_award_gtr_has_never_heard_of():
    out = funding.check_grants(
        paper([{"funder": "MRC", "award_id": "MR/ZZ999999/1"}]),
        lookup=lambda ref: None)

    assert out[0]["verdict"] == "unknown"


def test_awards_without_a_number_are_skipped():
    out = funding.check_grants(
        paper([{"funder": "MRC", "award_id": ""}, {"funder": "MRC"}]),
        lookup=lambda ref: PROJECT)

    assert out == []

"""
test_poc_handoff_pde.py
=======================
pytest test suite for poc_handoff_pde.py

Layout
------
Works in two configurations without any change:

  Flat (ZIP default):
    project/
      poc_handoff_pde.py
      test_poc_handoff_pde.py    ← pytest test_poc_handoff_pde.py -v

  Subdirectory:
    project/
      poc_handoff_pde.py
      tests/
        test_poc_handoff_pde.py  ← pytest tests/test_poc_handoff_pde.py -v

Test categories
---------------
1.  Verdicts          — RELEASE / HOLD / QA_REVIEW / REJECT baseline cases
2.  Evidence gap      — HOLD when records are missing or seal is broken
3.  Temp excursion    — QA_REVIEW in the alpha-to-beta zone
4.  Beta breach       — REJECT when any metric exceeds beta
5.  Invalid input     — InputValidationError raised before any verdict
6.  Chain stop        — stop_after_first_nonrelease behaviour
7.  Threshold boundary — values safely below / clearly above alpha and beta
8.  Strict bool       — string / int bool fields raise InputValidationError
9.  NaN / inf         — non-finite numbers raise InputValidationError
10. measure_id        — missing / mismatched measure_id raises InputValidationError
11. CLI format        — invalid input shows INPUT ERROR, no traceback, exit 1
12. _exceeds helper   — centralised comparison rule is correctly applied

Red-team scenario tests (categories correspond to RED_TEAM_REPORT.md)
----------------------------------------------------------------------
RT-A. Coercion attacks         — string "0"/"1" for bool fields; None; bool in numeric
RT-B. Sequencing/ordering      — reversed pair; duplicate at start; reversal in long sequence
RT-C. Evidence contradictions  — partial sub-flags false; temp_missing with nonempty data
RT-D. Chain masking prevention — REJECT/HOLD upstream prevents clean downstream leg masking
RT-E. Boundary conditions      — gap and d_minus at/just-above beta thresholds
"""

from __future__ import annotations

import math
import os
import subprocess
import sys

import pytest


# ---------------------------------------------------------------------------
# Path resolution — works for flat (ZIP) or tests/ subdirectory layout
# ---------------------------------------------------------------------------

def _find_root() -> str:
    """Find the directory containing poc_handoff_pde.py.

    Checks the same directory as this test file first (flat/ZIP layout),
    then one level up (tests/ subdirectory layout).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, "poc_handoff_pde.py")):
        return here
    parent = os.path.dirname(here)
    if os.path.isfile(os.path.join(parent, "poc_handoff_pde.py")):
        return parent
    raise FileNotFoundError(
        f"poc_handoff_pde.py not found in {here!r} or {parent!r}. "
        "Place the test file alongside the main script (flat layout) or "
        "exactly one directory below it (tests/ layout)."
    )


_ROOT = _find_root()
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from poc_handoff_pde import (  # noqa: E402
    InputValidationError,
    DispositionLabel,
    MEASURE_ID,
    _exceeds,
    validate_handoff_input,
    run_chain,
    last_good,
    first_fail,
)

CHARTER = "CHARTER-LS-2TO8-POC-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base(**overrides) -> dict:
    """Return a minimal, fully-valid handoff dict."""
    base = dict(
        shipment_id="SHIP-TEST-001",
        measure_id=MEASURE_ID,
        duration_minutes=18.0,
        temperatures=[[0, 4.5], [5, 5.1], [10, 5.0], [15, 4.8], [18, 5.2]],
        temp_missing=False,
        max_temp_sample_gap_minutes=5.0,
        handoff_from="Shipper-A",
        handoff_to="Carrier-X",
        evidence_ok=True,
        custody_complete=True,
        seal_intact=True,
        logger_calibrated=True,
        temp_records_complete=True,
    )
    base.update(overrides)
    return base


def _chain(*handoffs, stop=True):
    return run_chain(list(handoffs), CHARTER, stop_after_first_nonrelease=stop)


def _label(results, idx=0) -> DispositionLabel:
    return results[idx].decision.label


# ===========================================================================
# 1. Verdict baseline
# ===========================================================================

class TestVerdicts:

    def test_normal_leg_is_release(self):
        results, stopped = _chain(_base())
        assert not stopped
        assert _label(results) == DispositionLabel.RELEASE

    def test_all_four_verdicts_are_reachable(self):
        # RELEASE
        r, _ = _chain(_base())
        assert _label(r) == DispositionLabel.RELEASE

        # HOLD (seal broken)
        r, _ = _chain(_base(seal_intact=False))
        assert _label(r) == DispositionLabel.HOLD

        # QA_REVIEW (temperature excursion in alpha..beta zone)
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[5,6.2],[10,8.8],[15,9.5],[20,8.2],[25,7.0],[30,6.0],[35,5.5]],
            duration_minutes=35.0, max_temp_sample_gap_minutes=5.0,
        ))
        assert _label(r) == DispositionLabel.QA_REVIEW

        # REJECT (peak 11.5°C → d_plus 3.5 > beta 2.0)
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[10,11.5],[20,5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.REJECT


# ===========================================================================
# 2. Evidence gap → HOLD
# ===========================================================================

class TestEvidenceGap:

    def test_temp_missing_gives_hold(self):
        r, _ = _chain(_base(temperatures=[], temp_missing=True, temp_records_complete=False))
        assert _label(r) == DispositionLabel.HOLD

    def test_seal_broken_gives_hold(self):
        r, _ = _chain(_base(seal_intact=False))
        assert _label(r) == DispositionLabel.HOLD

    def test_logger_uncalibrated_gives_hold(self):
        r, _ = _chain(_base(logger_calibrated=False))
        assert _label(r) == DispositionLabel.HOLD

    def test_custody_incomplete_gives_hold(self):
        r, _ = _chain(_base(custody_complete=False))
        assert _label(r) == DispositionLabel.HOLD

    def test_evidence_ok_false_gives_hold_regardless_of_sub_flags(self):
        # evidence_ok=false overrides all sub-flags
        r, _ = _chain(_base(evidence_ok=False, seal_intact=False, custody_complete=False))
        assert _label(r) == DispositionLabel.HOLD


# ===========================================================================
# 3. Temperature excursion → QA_REVIEW
# ===========================================================================

class TestTempExcursion:

    def test_temperature_spike_in_gray_zone_gives_qa_review(self):
        # d_plus = 9.5 - 8.0 = 1.5  (alpha=0.3, beta=2.0 → gray zone)
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[5,6.2],[10,8.8],[15,9.5],[20,8.2],[25,7.0],[30,6.0],[35,5.5]],
            duration_minutes=35.0, max_temp_sample_gap_minutes=5.0,
        ))
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_large_sample_gap_in_gray_zone_gives_qa_review(self):
        # max_gap_minutes = 25 > alpha(10), ≤ beta(45) → gray zone
        r, _ = _chain(_base(
            temperatures=[[0, 5.1], [15, 5.3]],
            duration_minutes=40.0, max_temp_sample_gap_minutes=25.0,
        ))
        assert _label(r) == DispositionLabel.QA_REVIEW


# ===========================================================================
# 4. Beta breach → REJECT
# ===========================================================================

class TestBetaBreach:

    def test_d_plus_exceeds_beta_gives_reject(self):
        # peak 11.5°C → d_plus = 3.5 > beta(2.0)
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[10,11.5],[20,5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.REJECT

    def test_d_minus_exceeds_beta_gives_reject(self):
        # trough -1.0°C → d_minus = 3.0 > beta(2.0)
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[10,-1.0],[20,5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.REJECT

    def test_duration_exceeds_beta_gives_reject(self):
        # duration 95 > beta(90)
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[45,5.1],[90,5.0],[95,4.9]],
            duration_minutes=95.0, max_temp_sample_gap_minutes=45.0,
        ))
        assert _label(r) == DispositionLabel.REJECT


# ===========================================================================
# 5. Invalid input → InputValidationError (not a verdict)
# ===========================================================================

class TestInvalidInput:
    """All cases below must raise InputValidationError before any verdict is issued.

    Distinct from HOLD/REJECT: these are upstream data-quality failures.
    """

    def _ve(self, h):
        with pytest.raises(InputValidationError):
            validate_handoff_input(h)

    def test_missing_shipment_id(self):
        h = _base(); del h["shipment_id"]; self._ve(h)

    def test_empty_shipment_id(self):
        self._ve(_base(shipment_id="   "))

    def test_missing_measure_id(self):
        h = _base(); del h["measure_id"]; self._ve(h)

    def test_wrong_measure_id(self):
        self._ve(_base(measure_id="wrong.id"))

    def test_empty_handoff_from(self):
        self._ve(_base(handoff_from=""))

    def test_empty_handoff_to(self):
        self._ve(_base(handoff_to=""))

    def test_missing_duration(self):
        h = _base(); del h["duration_minutes"]; self._ve(h)

    def test_negative_duration(self):
        self._ve(_base(duration_minutes=-1.0))

    def test_negative_gap(self):
        self._ve(_base(max_temp_sample_gap_minutes=-5.0))

    def test_temperatures_not_list(self):
        self._ve(_base(temperatures="5.0,5.1"))

    def test_temperature_element_wrong_shape(self):
        self._ve(_base(temperatures=[[0, 5.0, "extra"]]))

    def test_negative_timestamp(self):
        self._ve(_base(temperatures=[[-1, 5.0], [5, 5.1]]))

    def test_unsorted_timestamps(self):
        self._ve(_base(temperatures=[[10, 5.0], [0, 5.1], [20, 5.2]]))

    def test_duplicate_timestamps(self):
        self._ve(_base(temperatures=[[0, 5.0], [5, 5.1], [5, 5.2], [10, 5.0]]))

    def test_empty_temps_with_temp_missing_false(self):
        # Claims records exist (temp_missing=false) but provides none
        self._ve(_base(temperatures=[], temp_missing=False))

    def test_temp_missing_true_and_records_complete_true(self):
        self._ve(_base(temperatures=[], temp_missing=True, temp_records_complete=True))

    def test_none_handoff_from_is_valid(self):
        # None / absent is allowed; only empty string is rejected
        h = _base(); h.pop("handoff_from", None)
        validate_handoff_input(h)  # must not raise

    # Via run_chain
    def test_invalid_input_raises_from_run_chain(self):
        with pytest.raises(InputValidationError):
            run_chain([_base(duration_minutes=-5)], CHARTER)


# ===========================================================================
# 6. Chain stop control
# ===========================================================================

class TestChainStop:

    def _pass_leg(self): return _base()
    def _hold_leg(self): return _base(seal_intact=False)
    def _qa_leg(self):
        return _base(
            temperatures=[[0,5.0],[5,6.2],[10,8.8],[15,9.5],[20,8.2],[25,7.0],[30,6.0],[35,5.5]],
            duration_minutes=35.0, max_temp_sample_gap_minutes=5.0)
    def _reject_leg(self):
        return _base(temperatures=[[0,5.0],[10,11.5],[20,5.0]],
                     duration_minutes=20.0, max_temp_sample_gap_minutes=10.0)

    def test_chain_stops_at_hold(self):
        r, stopped = _chain(self._pass_leg(), self._hold_leg(), self._pass_leg())
        assert stopped and len(r) == 2
        assert _label(r, 1) == DispositionLabel.HOLD

    def test_chain_stops_at_qa_review(self):
        r, stopped = _chain(self._pass_leg(), self._qa_leg(), self._pass_leg())
        assert stopped and len(r) == 2
        assert _label(r, 1) == DispositionLabel.QA_REVIEW

    def test_chain_stops_at_reject(self):
        r, stopped = _chain(self._pass_leg(), self._reject_leg(), self._pass_leg())
        assert stopped and len(r) == 2
        assert _label(r, 1) == DispositionLabel.REJECT

    def test_continue_after_fail_evaluates_all_legs(self):
        r, stopped = _chain(self._pass_leg(), self._hold_leg(), self._pass_leg(), stop=False)
        assert not stopped and len(r) == 3

    def test_all_pass_does_not_stop_early(self):
        r, stopped = _chain(self._pass_leg(), self._pass_leg(), self._pass_leg())
        assert not stopped and len(r) == 3

    def test_last_good_identifies_correct_leg(self):
        r, _ = _chain(self._pass_leg(), self._hold_leg(), self._pass_leg())
        lg = last_good(r)
        ff = first_fail(r)
        assert lg is not None and lg.seq == 1
        assert ff is not None and ff.seq == 2

    def test_last_good_is_none_when_first_leg_fails(self):
        r, _ = _chain(self._hold_leg(), self._pass_leg())
        assert last_good(r) is None

    def test_first_fail_is_none_when_all_pass(self):
        r, _ = _chain(self._pass_leg(), self._pass_leg(), stop=False)
        assert first_fail(r) is None

    def test_stopped_early_flag_is_false_when_all_pass(self):
        _, stopped = run_chain([self._pass_leg(), self._pass_leg()], CHARTER, True)
        assert not stopped


# ===========================================================================
# 7. Threshold boundary values
# ===========================================================================

class TestThresholdBoundary:
    """
    Profile: temperature_lower=2.0, upper=8.0
      alpha: d_plus=0.3, gap=10, duration=30
      beta:  d_plus=2.0, gap=45, duration=90

    Comparison rule (via _exceeds): observed > threshold → breach.
    Equal to threshold is NOT a breach.

    IEEE-754 note: "exactly alpha" (e.g. d_plus==0.3) is avoided in tests because
    8.3-8.0 = 0.3000000000000007 in floating-point — measurements very close to 0.3°C
    may round either side. Tests use values safely inside or clearly outside each zone.
    Beta values (2.0, 30.0, 90.0) ARE exactly representable and their boundary tests are stable.
    """

    def _leg(self, peak=None, duration=18.0, gap=5.0):
        t = [[0, 5.0], [10, peak], [20, 5.0]] if peak is not None else \
            [[0, 4.5], [5, 5.1], [10, 5.0], [15, 4.8], [18, 5.2]]
        return _base(temperatures=t, duration_minutes=duration,
                     max_temp_sample_gap_minutes=gap)

    # d_plus boundary
    def test_d_plus_clearly_below_alpha_gives_release(self):
        # peak 8.1°C → d_plus ≈ 0.1, well below alpha(0.3)
        r, _ = _chain(self._leg(peak=8.1))
        assert _label(r) == DispositionLabel.RELEASE

    def test_d_plus_clearly_above_alpha_gives_qa_review(self):
        # peak 8.5°C → d_plus = 0.5, above alpha(0.3), below beta(2.0)
        r, _ = _chain(self._leg(peak=8.5))
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_d_plus_exactly_beta_gives_qa_review(self):
        # peak 10.0°C → d_plus = 2.0 exactly (10.0-8.0 is exact in IEEE-754)
        # Comparison rule: 2.0 > 2.0 is False → NOT fatal → QA_REVIEW
        r, _ = _chain(self._leg(peak=10.0))
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_d_plus_just_above_beta_gives_reject(self):
        # peak 10.1°C → d_plus = 2.1 > beta(2.0) → REJECT
        r, _ = _chain(self._leg(peak=10.1))
        assert _label(r) == DispositionLabel.REJECT

    # duration boundary
    def test_duration_exactly_alpha_gives_release(self):
        # duration = 30.0 exactly; 30.0 > 30.0 is False → RELEASE
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[10,5.1],[20,5.0],[30,4.9]],
            duration_minutes=30.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.RELEASE

    def test_duration_just_above_alpha_gives_qa_review(self):
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[10,5.1],[20,5.0],[30,4.9]],
            duration_minutes=30.001, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_duration_exactly_beta_gives_qa_review(self):
        # 90.0 > 90.0 is False → NOT fatal
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[30,5.1],[60,5.0],[90,4.9]],
            duration_minutes=90.0, max_temp_sample_gap_minutes=30.0,
        ))
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_duration_just_above_beta_gives_reject(self):
        r, _ = _chain(_base(
            temperatures=[[0,5.0],[30,5.1],[60,5.0],[90,4.9]],
            duration_minutes=90.001, max_temp_sample_gap_minutes=30.0,
        ))
        assert _label(r) == DispositionLabel.REJECT


# ===========================================================================
# 8. Strict bool — string / int bool values must raise InputValidationError
# ===========================================================================

class TestStrictBool:
    """JSON bools only. Strings ("false") and numbers (0, 1) are rejected.

    Motivation: bool("false") == True in Python, so a data pipeline that
    serialises booleans as strings would silently flip evidence flags.
    """

    _BOOL_FIELDS = [
        "evidence_ok", "custody_complete", "seal_intact",
        "logger_calibrated", "temp_records_complete",
    ]

    @pytest.mark.parametrize("field", _BOOL_FIELDS)
    def test_string_false_rejected(self, field):
        with pytest.raises(InputValidationError, match=field):
            validate_handoff_input(_base(**{field: "false"}))

    @pytest.mark.parametrize("field", _BOOL_FIELDS)
    def test_string_true_rejected(self, field):
        with pytest.raises(InputValidationError, match=field):
            validate_handoff_input(_base(**{field: "true"}))

    @pytest.mark.parametrize("field", _BOOL_FIELDS)
    def test_integer_zero_rejected(self, field):
        with pytest.raises(InputValidationError, match=field):
            validate_handoff_input(_base(**{field: 0}))

    @pytest.mark.parametrize("field", _BOOL_FIELDS)
    def test_integer_one_rejected(self, field):
        with pytest.raises(InputValidationError, match=field):
            validate_handoff_input(_base(**{field: 1}))

    def test_temp_missing_string_rejected(self):
        with pytest.raises(InputValidationError, match="temp_missing"):
            validate_handoff_input(_base(temp_missing="false"))

    def test_genuine_python_false_accepted(self):
        # Python False (from JSON false) must be accepted
        validate_handoff_input(_base(seal_intact=False))  # HOLD verdict, but no IVE

    def test_genuine_python_true_accepted(self):
        validate_handoff_input(_base(evidence_ok=True))

    def test_string_bool_cannot_bypass_hold(self):
        # "false" for seal_intact must not reach evaluation — must be an input error
        with pytest.raises(InputValidationError):
            run_chain([_base(seal_intact="false")], CHARTER)

    def test_evidence_ok_false_all_sub_true_gives_hold_not_ive(self, capsys):
        """Contradictory but structurally valid — verdict is HOLD, not InputValidationError."""
        h = _base(evidence_ok=False)  # sub-flags still True (default)
        validate_handoff_input(h)     # must not raise
        r, _ = run_chain([h], CHARTER)
        assert _label(r) == DispositionLabel.HOLD
        captured = capsys.readouterr()
        assert "WARN" in captured.err  # warning must be emitted


# ===========================================================================
# 9. NaN / inf → InputValidationError
# ===========================================================================

class TestNanInf:
    """Non-finite numeric values must be rejected before any evaluation."""

    @pytest.mark.parametrize("val", [math.nan, math.inf, -math.inf])
    def test_duration_non_finite(self, val):
        with pytest.raises(InputValidationError, match="duration_minutes"):
            validate_handoff_input(_base(duration_minutes=val))

    @pytest.mark.parametrize("val", [math.nan, math.inf, -math.inf])
    def test_gap_non_finite(self, val):
        with pytest.raises(InputValidationError, match="max_temp_sample_gap_minutes"):
            validate_handoff_input(_base(max_temp_sample_gap_minutes=val))

    def test_temperature_timestamp_nan(self):
        with pytest.raises(InputValidationError, match="temperatures"):
            validate_handoff_input(_base(temperatures=[[math.nan, 5.0], [5, 5.1]]))

    def test_temperature_value_nan(self):
        with pytest.raises(InputValidationError, match="temperatures"):
            validate_handoff_input(_base(temperatures=[[0, math.nan], [5, 5.1]]))

    def test_temperature_timestamp_inf(self):
        with pytest.raises(InputValidationError, match="temperatures"):
            validate_handoff_input(_base(temperatures=[[math.inf, 5.0]]))

    def test_temperature_value_inf(self):
        with pytest.raises(InputValidationError, match="temperatures"):
            validate_handoff_input(_base(temperatures=[[0, math.inf]]))

    def test_bool_true_in_duration_rejected(self):
        """Python True is bool (subclass of int) → would become 1.0 silently."""
        with pytest.raises(InputValidationError, match="duration_minutes"):
            validate_handoff_input(_base(duration_minutes=True))

    def test_bool_false_in_gap_rejected(self):
        with pytest.raises(InputValidationError, match="max_temp_sample_gap_minutes"):
            validate_handoff_input(_base(max_temp_sample_gap_minutes=False))

    def test_nan_cannot_reach_evaluator(self):
        """NaN in temperature must not produce any verdict — it must error first."""
        with pytest.raises(InputValidationError):
            run_chain([_base(temperatures=[[0, math.nan], [5, 5.1]])], CHARTER)


# ===========================================================================
# 10. measure_id — missing or mismatched must raise InputValidationError
# ===========================================================================

class TestMeasureId:

    def test_missing_measure_id_raises(self):
        h = _base(); del h["measure_id"]
        with pytest.raises(InputValidationError, match="measure_id is missing"):
            validate_handoff_input(h)

    def test_none_measure_id_raises(self):
        with pytest.raises(InputValidationError, match="measure_id is missing"):
            validate_handoff_input(_base(measure_id=None))

    def test_wrong_measure_id_raises(self):
        with pytest.raises(InputValidationError, match="measure_id mismatch"):
            validate_handoff_input(_base(measure_id="wrong.id.v99"))

    def test_empty_measure_id_raises(self):
        with pytest.raises(InputValidationError, match="measure_id mismatch"):
            validate_handoff_input(_base(measure_id=""))

    def test_correct_measure_id_passes(self):
        validate_handoff_input(_base(measure_id=MEASURE_ID))  # must not raise

    def test_measure_id_mismatch_via_run_chain(self):
        with pytest.raises(InputValidationError):
            run_chain([_base(measure_id="bad.id")], CHARTER)


# ===========================================================================
# 11. CLI error format
# ===========================================================================

class TestCLIFormat:
    """Invalid input must display INPUT ERROR on stderr, no traceback, exit 1.

    Uses _ROOT (resolved at module load) so both flat and tests/ layouts work.
    """

    def _cli(self, filename: str):
        script = os.path.join(_ROOT, "poc_handoff_pde.py")
        json_path = os.path.join(_ROOT, filename)
        result = subprocess.run(
            [sys.executable, script, json_path],
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout, result.stderr

    def test_invalid_empty_temps_exits_1(self):
        rc, _, _ = self._cli("samples/sample_invalid_empty_temps.json")
        assert rc == 1

    def test_invalid_empty_temps_shows_input_error(self):
        _, _, err = self._cli("samples/sample_invalid_empty_temps.json")
        assert "INPUT ERROR:" in err

    def test_invalid_empty_temps_no_traceback(self):
        _, _, err = self._cli("samples/sample_invalid_empty_temps.json")
        assert "Traceback" not in err
        assert 'File "' not in err

    def test_invalid_negatives_shows_input_error(self):
        rc, _, err = self._cli("samples/sample_invalid_negatives.json")
        assert rc == 1 and "INPUT ERROR:" in err and "Traceback" not in err

    def test_invalid_timestamps_shows_input_error(self):
        rc, _, err = self._cli("samples/sample_invalid_timestamps.json")
        assert rc == 1 and "INPUT ERROR:" in err and "Traceback" not in err

    def test_valid_json_exits_0(self):
        # sample_beta_breach_reject.json is valid input (REJECT verdict, clean exit)
        rc, _, err = self._cli("samples/sample_beta_breach_reject.json")
        assert rc == 0, f"expected exit 0, got {rc}\nstderr: {err}"


# ===========================================================================
# 12. _exceeds helper — centralised comparison rule
# ===========================================================================

class TestExceedsHelper:
    """_exceeds() is the single source of truth for all alpha/beta comparisons."""

    def test_strictly_greater_returns_true(self):
        assert _exceeds(2.001, 2.0) is True

    def test_equal_returns_false(self):
        assert _exceeds(2.0, 2.0) is False

    def test_less_returns_false(self):
        assert _exceeds(1.999, 2.0) is False

    def test_zero_vs_zero_returns_false(self):
        assert _exceeds(0.0, 0.0) is False

    def test_negative_observed_returns_false(self):
        assert _exceeds(-1.0, 0.0) is False

    def test_large_values(self):
        assert _exceeds(90.001, 90.0) is True
        assert _exceeds(90.0, 90.0) is False

    def test_exactly_alpha_d_plus_boundary(self):
        # 10.0 - 8.0 = 2.0 exactly (IEEE-754); test the beta boundary
        # _exceeds(2.0, 2.0) → False → not fatal → QA_REVIEW
        r, _ = _chain(_base(
            temperatures=[[0, 5.0], [10, 10.0], [20, 5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_just_above_beta_d_plus(self):
        # _exceeds(2.1, 2.0) → True → fatal → REJECT
        r, _ = _chain(_base(
            temperatures=[[0, 5.0], [10, 10.1], [20, 5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.REJECT


# ===========================================================================
# RED-TEAM SCENARIO TESTS
# Organised by attack category. Scenario IDs (SC-NNN) correspond to
# RED_TEAM_REPORT.md. These tests confirm that the PoC handles tampering and
# integrity failures as documented. The judgement logic is not modified.
#
# Attack categories:
#   - coercion attacks
#   - sequencing / ordering attacks
#   - evidence contradiction cases
#   - chain masking prevention
#   - boundary-condition checks
# ===========================================================================


# ---------------------------------------------------------------------------
# Coercion attacks (SC-003, SC-004 — string "0" / "1"; None in bool field)
# SC-001/SC-002/SC-005/SC-006 are covered in TestStrictBool above.
# ---------------------------------------------------------------------------

class TestRedTeamCoercionAttacks:
    """SC-003, SC-004: string "0" and "1" are not JSON booleans.

    Most serialisation libraries represent False/True as "0"/"1" when
    writing to CSV or legacy formats. These must be rejected the same way
    as "false" / "true" — bool("0") and bool("1") are both True in Python.
    """

    _BOOL_FIELDS = [
        "evidence_ok", "custody_complete", "seal_intact",
        "logger_calibrated", "temp_records_complete",
    ]

    # SC-003 — string "0" for bool fields
    @pytest.mark.parametrize("field", _BOOL_FIELDS)
    def test_sc003_string_zero_rejected(self, field):
        """String "0" must be rejected; bool("0") is True, which would
        silently invert an evidence flag that should be False."""
        with pytest.raises(InputValidationError, match=field):
            validate_handoff_input(_base(**{field: "0"}))

    # SC-004 — string "1" for bool fields
    @pytest.mark.parametrize("field", _BOOL_FIELDS)
    def test_sc004_string_one_rejected(self, field):
        """String "1" must be rejected; bool("1") is True, masking any
        coercion that should have produced False."""
        with pytest.raises(InputValidationError, match=field):
            validate_handoff_input(_base(**{field: "1"}))

    def test_none_as_bool_evidence_ok_blocked(self):
        """None in a bool field must be rejected — it is not JSON true/false."""
        with pytest.raises(InputValidationError, match="evidence_ok"):
            validate_handoff_input(_base(evidence_ok=None))

    def test_bool_true_in_temperature_value_blocked(self):
        """Python True is a bool subclass of int and would become 1.0 silently.
        It must be rejected as a temperature value."""
        with pytest.raises(InputValidationError, match="temperatures"):
            validate_handoff_input(_base(temperatures=[[0, True], [5, 5.1]]))


# ---------------------------------------------------------------------------
# Sequencing / ordering attacks (SC-011, SC-012)
# SC-011/SC-012 are also covered in TestInvalidInput; the cases here
# exercise specific attack-shaped inputs distinct from those above.
# ---------------------------------------------------------------------------

class TestRedTeamSequencingOrderingAttacks:
    """Timestamp ordering manipulation.

    A reversed or duplicated timestamp sequence passes Python's float()
    conversion cleanly but corrupts the area-under-curve calculation in
    _excursion(). Rejection must happen in validate_handoff_input().
    """

    def test_sc011_single_reversed_pair_blocked(self):
        """Two-entry sequence where second timestamp is earlier than first."""
        with pytest.raises(InputValidationError, match="strictly increasing"):
            validate_handoff_input(_base(temperatures=[[10, 5.0], [5, 5.1]]))

    def test_sc012_duplicate_at_first_position_blocked(self):
        """Duplicate at the very start of the sequence."""
        with pytest.raises(InputValidationError, match="strictly increasing"):
            validate_handoff_input(_base(
                temperatures=[[0, 5.0], [0, 5.2], [5, 5.1], [10, 5.0]]
            ))

    def test_large_sequence_with_one_reversal_blocked(self):
        """Long sequence with a single timestamp reversal near the end.
        All-or-nothing: the whole input must be rejected."""
        temps = [[i * 5, 5.0] for i in range(10)]
        temps.append([40, 5.0])   # t=40 <= t[9]=45; reversal
        with pytest.raises(InputValidationError, match="strictly increasing"):
            validate_handoff_input(_base(temperatures=temps))


# ---------------------------------------------------------------------------
# Evidence contradiction cases (SC-010)
# SC-010 is covered in TestStrictBool; these cases exercise additional
# contradiction shapes.
# ---------------------------------------------------------------------------

class TestRedTeamEvidenceContradictions:
    """Evidence flag contradictions.

    Some contradictions raise InputValidationError (temp_missing /
    temp_records_complete). Others pass validation but the evidence gate
    ensures the correct HOLD verdict is still issued.
    """

    def test_sc010_partial_sub_flags_false_still_gives_hold(self):
        """evidence_ok=true but some sub-flags false → HOLD.

        The evidence gate checks each requirement flag individually.
        Setting evidence_ok=true does not bypass sub-flag checks.
        """
        # logger_calibrated=False alone triggers evidence gate
        r, _ = _chain(_base(evidence_ok=True, logger_calibrated=False))
        assert _label(r) == DispositionLabel.HOLD

    def test_temp_missing_true_nonempty_temps_bypasses_validation_but_gives_hold(self):
        """temp_missing=true with non-empty temperatures slips through the
        validator (validation gap: validator does not check that
        temp_missing=true implies temperatures=[]).

        Despite the validation gap, the metrics calculation sets
        temp_records_complete=False (because temp_missing=true), and the
        evidence gate correctly issues HOLD.

        This tests that the overall verdict is correct even where the
        validator has a consistency gap. See RED_TEAM_REPORT.md Known Gap
        notes for SC-026 (fabricated valid data) for related context.
        """
        h = _base(
            temperatures=[[0, 5.0], [5, 5.1]],  # data provided despite temp_missing
            temp_missing=True,
            temp_records_complete=False,
        )
        # Validator does not raise — this input passes through
        validate_handoff_input(h)
        # Despite passing validation, HOLD is the correct verdict
        r, _ = _chain(h)
        assert _label(r) == DispositionLabel.HOLD

    def test_all_evidence_sub_flags_false_gives_hold_regardless_of_evidence_ok(self):
        """All sub-flags false, evidence_ok=true: evidence gate fails → HOLD."""
        r, _ = _chain(_base(
            evidence_ok=True,
            custody_complete=False,
            seal_intact=False,
            logger_calibrated=False,
            temp_records_complete=False,
        ))
        assert _label(r) == DispositionLabel.HOLD


# ---------------------------------------------------------------------------
# Chain masking prevention (SC-020, SC-021)
# SC-020/SC-021 are partly covered in TestChainStop; the cases here
# frame them explicitly as masking-attempt scenarios.
# ---------------------------------------------------------------------------

class TestRedTeamChainMaskingPrevention:
    """Chain masking: a structurally valid downstream leg must not rescue
    an upstream HOLD or REJECT.

    With stop_after_first_nonrelease=True (the default), the chain halts at
    the first non-RELEASE verdict. A superficially clean later leg is never
    evaluated and cannot appear in the summary.
    """

    def _clean_leg(self):
        return _base()

    def _hold_leg(self):
        return _base(seal_intact=False)

    def _reject_leg(self):
        return _base(
            temperatures=[[0, 5.0], [10, 11.5], [20, 5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        )

    def test_sc021_reject_then_clean_stops_at_reject(self):
        """SC-021: REJECT at leg 1 must prevent leg 2 (clean) from being
        evaluated. The clean leg cannot rescue or mask the REJECT."""
        r, stopped = _chain(self._reject_leg(), self._clean_leg())
        assert stopped is True
        assert len(r) == 1
        assert _label(r) == DispositionLabel.REJECT

    def test_sc020_hold_upstream_clean_downstream_not_evaluated(self):
        """SC-020: HOLD at leg 2 must prevent leg 3 (clean) from appearing
        in the result set."""
        r, stopped = _chain(self._clean_leg(), self._hold_leg(), self._clean_leg())
        assert stopped is True
        assert len(r) == 2
        assert _label(r, 1) == DispositionLabel.HOLD

    def test_multiple_clean_legs_then_reject_stops_chain(self):
        """Several clean legs followed by REJECT: chain stops at the REJECT;
        no phantom results for legs that were never evaluated."""
        r, stopped = _chain(
            self._clean_leg(), self._clean_leg(), self._reject_leg(), self._clean_leg()
        )
        assert stopped is True
        assert len(r) == 3
        assert _label(r, 2) == DispositionLabel.REJECT

    def test_continue_after_fail_evaluates_clean_leg_after_reject(self):
        """With stop=False, all legs are evaluated — the clean leg is
        visible in the result but the REJECT verdict is preserved."""
        r, stopped = _chain(
            self._reject_leg(), self._clean_leg(), stop=False
        )
        assert stopped is False
        assert len(r) == 2
        assert _label(r, 0) == DispositionLabel.REJECT
        assert _label(r, 1) == DispositionLabel.RELEASE


# ---------------------------------------------------------------------------
# Boundary-condition checks (SC-022 to SC-025; gap and d_minus variants)
# Core d_plus and duration boundaries are in TestThresholdBoundary.
# These extend coverage to the gap and d_minus metrics.
# ---------------------------------------------------------------------------

class TestRedTeamBoundaryConditions:
    """Threshold edge cases for gap and d_minus metrics.

    Comparison rule (single source of truth in _exceeds):
      observed > threshold → breach
      observed == threshold → NOT a breach (stays in zone)
    """

    # gap metric boundary
    def test_gap_exactly_at_beta_boundary_gives_qa_review(self):
        """max_gap=45.0 exactly; 45.0 > 45.0 is False → QA_REVIEW, not REJECT."""
        r, _ = _chain(_base(
            temperatures=[[0, 5.0], [45, 5.1]],
            duration_minutes=50.0, max_temp_sample_gap_minutes=45.0,
        ))
        # duration=50.0 > alpha(30) but ≤ beta(90) → gray zone already;
        # gap=45.0 == beta → not fatal; overall QA_REVIEW
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_gap_just_above_beta_gives_reject(self):
        """max_gap=45.001 > beta(45.0) → REJECT."""
        r, _ = _chain(_base(
            temperatures=[[0, 5.0], [46, 5.1]],
            duration_minutes=50.0, max_temp_sample_gap_minutes=45.001,
        ))
        assert _label(r) == DispositionLabel.REJECT

    # d_minus metric boundary
    def test_d_minus_exactly_at_beta_gives_qa_review(self):
        """trough = 0.0°C → d_minus = 2.0 - 0.0 = 2.0 exactly.
        2.0 > 2.0 is False → NOT fatal → QA_REVIEW."""
        r, _ = _chain(_base(
            temperatures=[[0, 5.0], [10, 0.0], [20, 5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.QA_REVIEW

    def test_d_minus_just_above_beta_gives_reject(self):
        """trough = -0.1°C → d_minus = 2.1 > beta(2.0) → REJECT."""
        r, _ = _chain(_base(
            temperatures=[[0, 5.0], [10, -0.1], [20, 5.0]],
            duration_minutes=20.0, max_temp_sample_gap_minutes=10.0,
        ))
        assert _label(r) == DispositionLabel.REJECT

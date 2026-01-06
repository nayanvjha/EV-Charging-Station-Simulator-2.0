"""
Unit tests for the Smart Charging Policy Engine

Tests all decision paths for evaluate_charging_policy function,
including edge cases and constraint interactions.
"""

import pytest
from charging_policy import evaluate_charging_policy, evaluate_meter_value_decision


class TestEvaluateChargingPolicy:
    """Test suite for evaluate_charging_policy function."""

    def test_energy_cap_reached(self):
        """Rule 1: Should pause when energy cap is reached."""
        station_state = {
            "energy_dispensed": 30.0,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "pause"
        assert "Energy cap reached" in result["reason"]
        assert "30.0/30.0" in result["reason"]

    def test_energy_cap_exceeded(self):
        """Should pause when energy exceeds max (not just equal)."""
        station_state = {
            "energy_dispensed": 35.0,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "pause"
        assert "Energy cap reached" in result["reason"]

    def test_price_too_high(self):
        """Rule 2: Should wait when price exceeds threshold."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 25.0, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "wait"
        assert "Price too high" in result["reason"]
        assert "₹25.00" in result["reason"]
        assert "₹20.00" in result["reason"]

    def test_price_exactly_at_threshold(self):
        """Should charge when price equals threshold (below means <, not <=)."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 20.0, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        # Price at threshold (20.0) should charge, since check is > (not >=)
        assert result["action"] == "charge"
        assert "Conditions OK" in result["reason"]

    def test_price_just_below_threshold(self):
        """Should charge when price is just below threshold."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 19.99, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"
        assert "Conditions OK" in result["reason"]

    def test_peak_hour_blocked(self):
        """Rule 3: Should wait during peak hours when disabled."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 19}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "wait"
        assert "Peak hour block" in result["reason"]
        assert "hour 19" in result["reason"]

    def test_multiple_peak_hours(self):
        """Should block charging during any hour in peak_hours list."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [8, 9, 10, 18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 9}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "wait"
        assert "Peak hour block" in result["reason"]

    def test_peak_hour_allowed(self):
        """Should charge during peak hours when allowed."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,  # Peak allowed
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 19}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"
        assert "Conditions OK" in result["reason"]

    def test_off_peak_hour_blocked_policy(self):
        """Should charge during off-peak hours even when peak is blocked."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,  # Peak blocked
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}  # Off-peak hour

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"
        assert "Conditions OK" in result["reason"]

    def test_all_conditions_ok(self):
        """Rule 4: Should charge when all conditions are met."""
        station_state = {
            "energy_dispensed": 15.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 18.50, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"
        assert "Conditions OK" in result["reason"]

    def test_decision_order_energy_cap_first(self):
        """Energy cap check should take priority over other constraints."""
        station_state = {
            "energy_dispensed": 30.0,  # At cap
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 50.0, "hour": 19}  # Both price and peak violated

        result = evaluate_charging_policy(station_state, profile, env)

        # Should return pause (energy cap), not wait (price)
        assert result["action"] == "pause"
        assert "Energy cap reached" in result["reason"]

    def test_decision_order_price_before_peak(self):
        """Price check should take priority over peak hours."""
        station_state = {
            "energy_dispensed": 15.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 25.0, "hour": 19}  # Both price and peak violated

        result = evaluate_charging_policy(station_state, profile, env)

        # Should return wait for price (higher priority)
        assert result["action"] == "wait"
        assert "Price too high" in result["reason"]

    def test_zero_energy_dispensed(self):
        """Should work with zero energy dispensed (fresh session)."""
        station_state = {
            "energy_dispensed": 0.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"

    def test_low_price_threshold(self):
        """Should work with low price thresholds."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 5.0,  # Very low threshold
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 5.50, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "wait"

    def test_high_energy_cap(self):
        """Should work with high energy caps."""
        station_state = {
            "energy_dispensed": 100.0,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 200.0,  # High cap
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"

    def test_midnight_hour(self):
        """Should handle hour 0 (midnight) correctly."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 0}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"

    def test_late_evening_hour(self):
        """Should handle hour 23 (late evening) correctly."""
        station_state = {
            "energy_dispensed": 10.0,
            "charging": False,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 23}

        result = evaluate_charging_policy(station_state, profile, env)

        assert result["action"] == "charge"


class TestEvaluateMeterValueDecision:
    """Test suite for evaluate_meter_value_decision function."""

    def test_meter_energy_cap_reached(self):
        """Should stop when Wh energy reaches max."""
        station_state = {
            "energy_dispensed": 28.0,  # 28 kWh
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}
        current_energy_wh = 30000  # 30 kWh
        max_energy_wh = 30000

        result = evaluate_meter_value_decision(
            station_state, profile, env, current_energy_wh, max_energy_wh
        )

        assert result["action"] == "stop"
        assert "Energy cap reached" in result["reason"]

    def test_meter_continue_charging(self):
        """Should continue when all conditions are met."""
        station_state = {
            "energy_dispensed": 15.0,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}
        current_energy_wh = 15000  # 15 kWh
        max_energy_wh = 30000

        result = evaluate_meter_value_decision(
            station_state, profile, env, current_energy_wh, max_energy_wh
        )

        assert result["action"] == "continue"

    def test_meter_stop_price_constraint(self):
        """Should stop if price constraint triggered during meter loop."""
        station_state = {
            "energy_dispensed": 15.0,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 25.0, "hour": 14}  # Price too high
        current_energy_wh = 15000
        max_energy_wh = 30000

        result = evaluate_meter_value_decision(
            station_state, profile, env, current_energy_wh, max_energy_wh
        )

        assert result["action"] == "stop"
        assert "Price too high" in result["reason"]

    def test_meter_stop_peak_constraint(self):
        """Should stop if peak hour constraint triggered during meter loop."""
        station_state = {
            "energy_dispensed": 15.0,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 19}  # Peak hour, not allowed
        current_energy_wh = 15000
        max_energy_wh = 30000

        result = evaluate_meter_value_decision(
            station_state, profile, env, current_energy_wh, max_energy_wh
        )

        assert result["action"] == "stop"
        assert "Peak hour block" in result["reason"]

    def test_meter_precise_energy_values(self):
        """Should handle precise Wh values correctly."""
        station_state = {
            "energy_dispensed": 10.5,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 20.0,
            "allow_peak_hours": True,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}
        current_energy_wh = 19750  # 19.75 kWh
        max_energy_wh = 20000

        result = evaluate_meter_value_decision(
            station_state, profile, env, current_energy_wh, max_energy_wh
        )

        assert result["action"] == "continue"


class TestPolicyReturnStructure:
    """Test that return values always have correct structure."""

    def test_return_has_action_and_reason(self):
        """All decisions should return dict with 'action' and 'reason' keys."""
        station_state = {
            "energy_dispensed": 15.0,
            "charging": True,
            "session_active": True
        }
        profile = {
            "charge_if_price_below": 20.0,
            "max_energy_kwh": 30.0,
            "allow_peak_hours": False,
            "peak_hours": [18, 19, 20]
        }
        env = {"current_price": 15.0, "hour": 14}

        result = evaluate_charging_policy(station_state, profile, env)

        assert isinstance(result, dict)
        assert "action" in result
        assert "reason" in result
        assert isinstance(result["action"], str)
        assert isinstance(result["reason"], str)

    def test_action_is_valid_enum(self):
        """Action should always be one of: charge, wait, pause."""
        test_cases = [
            # (station_state, profile, env)
            (
                {"energy_dispensed": 30.0, "charging": True, "session_active": True},
                {"charge_if_price_below": 20.0, "max_energy_kwh": 30.0, "allow_peak_hours": True, "peak_hours": [18, 19, 20]},
                {"current_price": 15.0, "hour": 14}
            ),
            (
                {"energy_dispensed": 15.0, "charging": False, "session_active": True},
                {"charge_if_price_below": 20.0, "max_energy_kwh": 30.0, "allow_peak_hours": True, "peak_hours": [18, 19, 20]},
                {"current_price": 25.0, "hour": 14}
            ),
            (
                {"energy_dispensed": 15.0, "charging": False, "session_active": True},
                {"charge_if_price_below": 20.0, "max_energy_kwh": 30.0, "allow_peak_hours": False, "peak_hours": [18, 19, 20]},
                {"current_price": 15.0, "hour": 19}
            ),
            (
                {"energy_dispensed": 15.0, "charging": False, "session_active": True},
                {"charge_if_price_below": 20.0, "max_energy_kwh": 30.0, "allow_peak_hours": False, "peak_hours": [18, 19, 20]},
                {"current_price": 15.0, "hour": 14}
            ),
        ]

        for station_state, profile, env in test_cases:
            result = evaluate_charging_policy(station_state, profile, env)
            assert result["action"] in ["charge", "wait", "pause"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

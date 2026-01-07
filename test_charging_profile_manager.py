"""
Unit tests for ChargingProfileManager

Tests OCPP 1.6 Smart Charging profile parsing, validation, storage,
composite schedule calculation, and profile behavior (Absolute, Recurring, Relative).
"""

import pytest
from datetime import datetime, timezone, timedelta
from charging_profile_manager import (
    ChargingProfileManager,
    ChargingProfile,
    ChargingSchedule,
    ChargingSchedulePeriod,
    ChargingProfilePurpose,
    ChargingProfileKind,
    ChargingRateUnit,
    RecurrencyKind,
    parse_charging_profile,
    validate_charging_profile,
)


class TestProfileParsing:
    """Test parsing of OCPP profile dictionaries."""

    def test_parse_minimal_profile(self):
        """Parse profile with only required fields."""
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 11000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        profile = parse_charging_profile(profile_dict)

        assert profile.charging_profile_id == 1
        assert profile.stack_level == 0
        assert profile.charging_profile_purpose == ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE
        assert profile.charging_profile_kind == ChargingProfileKind.ABSOLUTE
        assert profile.charging_schedule.charging_rate_unit == ChargingRateUnit.WATTS
        assert len(profile.charging_schedule.charging_schedule_period) == 1
        assert profile.charging_schedule.charging_schedule_period[0].limit == 11000

    def test_parse_full_profile(self):
        """Parse profile with all optional fields."""
        profile_dict = {
            "chargingProfileId": 2,
            "transactionId": 123,
            "stackLevel": 5,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Recurring",
            "recurrencyKind": "Daily",
            "validFrom": "2026-01-01T00:00:00Z",
            "validTo": "2026-12-31T23:59:59Z",
            "chargingSchedule": {
                "duration": 3600,
                "startSchedule": "2026-01-08T08:00:00Z",
                "chargingRateUnit": "A",
                "minChargingRate": 6.0,
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 16.0, "numberPhases": 3},
                    {"startPeriod": 1800, "limit": 10.0, "numberPhases": 1}
                ]
            }
        }

        profile = parse_charging_profile(profile_dict)

        assert profile.charging_profile_id == 2
        assert profile.transaction_id == 123
        assert profile.stack_level == 5
        assert profile.recurrency_kind == RecurrencyKind.DAILY
        assert profile.valid_from == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert profile.valid_to == datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        assert profile.charging_schedule.duration == 3600
        assert profile.charging_schedule.min_charging_rate == 6.0
        assert len(profile.charging_schedule.charging_schedule_period) == 2
        assert profile.charging_schedule.charging_schedule_period[1].number_phases == 1

    def test_parse_missing_required_field(self):
        """Parsing fails when required field is missing."""
        profile_dict = {
            "chargingProfileId": 1,
            # Missing stackLevel
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}]
            }
        }

        with pytest.raises(ValueError, match="Missing required field: stackLevel"):
            parse_charging_profile(profile_dict)

    def test_parse_invalid_purpose(self):
        """Parsing fails with invalid chargingProfilePurpose."""
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "InvalidPurpose",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        with pytest.raises(ValueError, match="Invalid chargingProfilePurpose"):
            parse_charging_profile(profile_dict)

    def test_parse_invalid_rate_unit(self):
        """Parsing fails with invalid chargingRateUnit."""
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "kW",  # Invalid, only W or A allowed
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        with pytest.raises(ValueError, match="Invalid chargingRateUnit"):
            parse_charging_profile(profile_dict)

    def test_parse_missing_period_field(self):
        """Parsing fails when period is missing required field."""
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0}  # Missing limit
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        with pytest.raises(ValueError, match="Missing required field in period: limit"):
            parse_charging_profile(profile_dict)


class TestProfileValidation:
    """Test profile validation logic."""

    def test_valid_profile(self):
        """Valid profile passes validation."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        is_valid, msg = validate_charging_profile(profile)
        assert is_valid
        assert msg == ""

    def test_negative_profile_id(self):
        """Validation fails for non-positive chargingProfileId."""
        profile = ChargingProfile(
            charging_profile_id=0,  # Invalid
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "chargingProfileId must be positive" in msg

    def test_negative_stack_level(self):
        """Validation fails for negative stackLevel."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=-1,  # Invalid
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "stackLevel must be non-negative" in msg

    def test_empty_periods(self):
        """Validation fails for empty chargingSchedulePeriod."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[],  # Empty
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "chargingSchedulePeriod array cannot be empty" in msg

    def test_unsorted_periods(self):
        """Validation fails if periods are not sorted by startPeriod."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000),
                    ChargingSchedulePeriod(start_period=3600, limit=8000),
                    ChargingSchedulePeriod(start_period=1800, limit=10000),  # Out of order
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "must be sorted by startPeriod ascending" in msg

    def test_non_positive_limit(self):
        """Validation fails for non-positive limit."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=0)  # Invalid
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "non-positive limit" in msg

    def test_tx_profile_missing_transaction_id(self):
        """Validation fails for TxProfile without transactionId."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            ),
            transaction_id=None  # Missing
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "transactionId is required for TxProfile" in msg

    def test_recurring_missing_recurrency_kind(self):
        """Validation fails for Recurring without recurrencyKind."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.RECURRING,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            ),
            recurrency_kind=None  # Missing
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "recurrencyKind is required for Recurring" in msg

    def test_absolute_missing_start_schedule(self):
        """Validation fails for Absolute without startSchedule."""
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=None  # Missing
            )
        )

        is_valid, msg = validate_charging_profile(profile)
        assert not is_valid
        assert "startSchedule is required for Absolute" in msg


class TestAddProfile:
    """Test adding profiles to the manager."""

    def test_add_valid_profile(self):
        """Successfully add a valid profile."""
        manager = ChargingProfileManager()
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        success, msg = manager.add_profile(1, profile)

        assert success
        assert "accepted" in msg.lower()
        assert len(manager.get_profiles_for_connector(1)) == 1

    def test_add_invalid_profile(self):
        """Reject invalid profile."""
        manager = ChargingProfileManager()
        profile = ChargingProfile(
            charging_profile_id=-1,  # Invalid
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        success, msg = manager.add_profile(1, profile)

        assert not success
        assert "Validation failed" in msg
        assert len(manager.get_profiles_for_connector(1)) == 0

    def test_stack_level_conflict_same_purpose(self):
        """Reject profile with same purpose and stackLevel."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=0,  # Same stack level
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,  # Same purpose
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=8000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        success1, _ = manager.add_profile(1, profile1)
        success2, msg2 = manager.add_profile(1, profile2)

        assert success1
        assert not success2
        assert "StackLevel conflict" in msg2
        assert len(manager.get_profiles_for_connector(1)) == 1

    def test_no_conflict_different_purpose(self):
        """Allow same stackLevel for different purposes."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=0,  # Same stack level
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,  # Different purpose
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=8000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        success1, _ = manager.add_profile(1, profile1)
        success2, _ = manager.add_profile(1, profile2)

        assert success1
        assert success2
        assert len(manager.get_profiles_for_connector(1)) == 2

    def test_update_existing_profile_id(self):
        """Updating profile with same ID replaces the old one."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile1_updated = ChargingProfile(
            charging_profile_id=1,  # Same ID
            stack_level=1,  # Different stack level
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=8000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(1, profile1_updated)

        profiles = manager.get_profiles_for_connector(1)
        assert len(profiles) == 1
        assert profiles[0].stack_level == 1
        assert profiles[0].charging_schedule.charging_schedule_period[0].limit == 8000


class TestProfileStorage:
    """Test profile storage per connector."""

    def test_separate_connector_storage(self):
        """Profiles stored separately per connector."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=8000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(2, profile2)

        assert len(manager.get_profiles_for_connector(1)) == 1
        assert len(manager.get_profiles_for_connector(2)) == 1
        assert manager.get_profiles_for_connector(1)[0].charging_profile_id == 1
        assert manager.get_profiles_for_connector(2)[0].charging_profile_id == 2

    def test_connector_zero_charge_point_level(self):
        """Connector 0 represents charge point level profiles."""
        manager = ChargingProfileManager()
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=22000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(0, profile)

        assert len(manager.get_profiles_for_connector(0)) == 1
        assert 0 in manager.get_all_connector_ids()


class TestClearProfile:
    """Test clear_profile filtering logic."""

    def test_clear_by_profile_id(self):
        """Clear specific profile by ID."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=1,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=8000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(1, profile2)

        cleared = manager.clear_profile(1, profile_id=1)

        assert cleared == 1
        assert len(manager.get_profiles_for_connector(1)) == 1
        assert manager.get_profiles_for_connector(1)[0].charging_profile_id == 2

    def test_clear_by_purpose(self):
        """Clear all profiles with specific purpose."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=22000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(1, profile2)

        cleared = manager.clear_profile(
            1, purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE
        )

        assert cleared == 1
        assert len(manager.get_profiles_for_connector(1)) == 1
        assert manager.get_profiles_for_connector(1)[0].charging_profile_purpose == \
            ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE

    def test_clear_by_stack_level(self):
        """Clear all profiles with specific stackLevel."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=1,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=8000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(1, profile2)

        cleared = manager.clear_profile(1, stack_level=0)

        assert cleared == 1
        assert len(manager.get_profiles_for_connector(1)) == 1
        assert manager.get_profiles_for_connector(1)[0].stack_level == 1

    def test_clear_all_profiles(self):
        """Clear all profiles when no filters provided."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=1,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=22000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(1, profile2)

        cleared = manager.clear_profile(1)

        assert cleared == 2
        assert len(manager.get_profiles_for_connector(1)) == 0

    def test_clear_multiple_criteria(self):
        """Clear using multiple criteria (AND logic)."""
        manager = ChargingProfileManager()
        
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )
        
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=22000)
                ],
                start_schedule=datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(1, profile2)

        # Clear with purpose=TxDefaultProfile AND stackLevel=0
        cleared = manager.clear_profile(
            1,
            purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            stack_level=0
        )

        assert cleared == 1
        assert len(manager.get_profiles_for_connector(1)) == 1
        assert manager.get_profiles_for_connector(1)[0].charging_profile_id == 2

    def test_clear_nonexistent_connector(self):
        """Clear from nonexistent connector returns 0."""
        manager = ChargingProfileManager()

        cleared = manager.clear_profile(99)

        assert cleared == 0


class TestCompositeSchedule:
    """Test composite schedule calculation."""

    def test_simple_single_profile(self):
        """Composite schedule from single profile."""
        manager = ChargingProfileManager()
        
        start_time = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000),
                    ChargingSchedulePeriod(start_period=1800, limit=7000)
                ],
                start_schedule=start_time
            )
        )

        manager.add_profile(1, profile)

        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=start_time
        )

        assert schedule is not None
        assert schedule.charging_rate_unit == ChargingRateUnit.WATTS
        assert len(schedule.charging_schedule_period) == 2
        assert schedule.charging_schedule_period[0].start_period == 0
        assert schedule.charging_schedule_period[0].limit == 11000
        assert schedule.charging_schedule_period[1].start_period == 1800
        assert schedule.charging_schedule_period[1].limit == 7000

    def test_multiple_profiles_minimum_limit(self):
        """Composite schedule takes minimum limit from overlapping profiles."""
        manager = ChargingProfileManager()
        
        start_time = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        
        # ChargePointMax: 22kW
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=22000)
                ],
                start_schedule=start_time
            )
        )
        
        # TxDefault: 11kW (lower)
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=start_time
            )
        )

        manager.add_profile(0, profile1)
        manager.add_profile(1, profile2)

        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=start_time
        )

        assert schedule is not None
        # Should use minimum limit (11kW)
        assert schedule.charging_schedule_period[0].limit == 11000

    def test_no_profiles_returns_none(self):
        """Returns None when no profiles exist."""
        manager = ChargingProfileManager()

        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS
        )

        assert schedule is None

    def test_expired_profiles_excluded(self):
        """Expired profiles not included in composite schedule."""
        manager = ChargingProfileManager()
        
        now = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        expired = datetime(2026, 1, 8, 9, 0, 0, tzinfo=timezone.utc)
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=expired
            ),
            valid_to=expired  # Expired at 9:00
        )

        manager.add_profile(1, profile)

        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=now  # Check at 10:00
        )

        assert schedule is None

    def test_not_yet_valid_profiles_excluded(self):
        """Profiles with future validFrom excluded."""
        manager = ChargingProfileManager()
        
        now = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        future = datetime(2026, 1, 8, 11, 0, 0, tzinfo=timezone.utc)
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=future
            ),
            valid_from=future  # Valid from 11:00
        )

        manager.add_profile(1, profile)

        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=now  # Check at 10:00
        )

        assert schedule is None


class TestStackLevelPriority:
    """Test purpose and stackLevel priority."""

    def test_lower_stack_level_higher_priority(self):
        """Lower stackLevel takes priority within same purpose."""
        manager = ChargingProfileManager()
        
        start_time = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        
        # Stack level 1 (lower priority)
        profile1 = ChargingProfile(
            charging_profile_id=1,
            stack_level=1,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=start_time
            )
        )
        
        # Stack level 0 (higher priority)
        profile2 = ChargingProfile(
            charging_profile_id=2,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=7000)
                ],
                start_schedule=start_time
            )
        )

        manager.add_profile(1, profile1)
        manager.add_profile(1, profile2)

        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=start_time
        )

        assert schedule is not None
        # Stack level 0 (7kW) has priority, but minimum is taken anyway
        assert schedule.charging_schedule_period[0].limit == 7000


class TestProfileKinds:
    """Test Absolute, Recurring, Relative profile behavior."""

    def test_absolute_profile_applies_at_exact_time(self):
        """Absolute profile applies at its startSchedule time."""
        manager = ChargingProfileManager()
        
        profile_start = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                duration=3600,
                start_schedule=profile_start
            )
        )

        manager.add_profile(1, profile)

        # Check at profile start time
        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=1800,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=profile_start
        )

        assert schedule is not None
        assert schedule.charging_schedule_period[0].limit == 11000

    def test_absolute_profile_before_start_time(self):
        """Absolute profile doesn't apply before its startSchedule."""
        manager = ChargingProfileManager()
        
        profile_start = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        before_start = datetime(2026, 1, 8, 9, 0, 0, tzinfo=timezone.utc)
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                start_schedule=profile_start
            )
        )

        manager.add_profile(1, profile)

        # Check before profile start
        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=1800,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=before_start
        )

        assert schedule is None

    def test_recurring_daily_profile(self):
        """Daily recurring profile repeats every day."""
        manager = ChargingProfileManager()
        
        # Profile set to start at 08:00
        profile_start = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.RECURRING,
            recurrency_kind=RecurrencyKind.DAILY,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000),
                    ChargingSchedulePeriod(start_period=3600, limit=7000)
                ],
                duration=7200,
                start_schedule=profile_start
            )
        )

        manager.add_profile(1, profile)

        # Check on a different day at 08:30 (30 min into schedule)
        check_time = datetime(2026, 1, 8, 8, 30, 0, tzinfo=timezone.utc)
        
        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=check_time
        )

        assert schedule is not None
        # At 8:30, still in first period (0-3600s from 8:00)
        assert schedule.charging_schedule_period[0].limit == 11000

    def test_recurring_weekly_profile(self):
        """Weekly recurring profile repeats on same day of week."""
        manager = ChargingProfileManager()
        
        # Profile set to Monday 08:00 (Jan 6, 2026 is a Tuesday)
        profile_start = datetime(2025, 12, 29, 8, 0, 0, tzinfo=timezone.utc)  # Monday
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.RECURRING,
            recurrency_kind=RecurrencyKind.WEEKLY,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                duration=3600,
                start_schedule=profile_start
            )
        )

        manager.add_profile(1, profile)

        # Check on next Monday at 08:30
        check_time = datetime(2026, 1, 5, 8, 30, 0, tzinfo=timezone.utc)  # Monday
        
        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=1800,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=check_time
        )

        assert schedule is not None
        assert schedule.charging_schedule_period[0].limit == 11000

    def test_relative_profile_excluded_without_transaction(self):
        """Relative profiles excluded from composite schedule (no transaction context)."""
        manager = ChargingProfileManager()
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_PROFILE,
            charging_profile_kind=ChargingProfileKind.RELATIVE,
            transaction_id=123,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ]
            )
        )

        manager.add_profile(1, profile)

        # Relative profiles need transaction start time, not available in composite schedule
        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.WATTS
        )

        assert schedule is None

    def test_absolute_profile_duration_limit(self):
        """Absolute profile doesn't apply beyond its duration."""
        manager = ChargingProfileManager()
        
        profile_start = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        
        profile = ChargingProfile(
            charging_profile_id=1,
            stack_level=0,
            charging_profile_purpose=ChargingProfilePurpose.TX_DEFAULT_PROFILE,
            charging_profile_kind=ChargingProfileKind.ABSOLUTE,
            charging_schedule=ChargingSchedule(
                charging_rate_unit=ChargingRateUnit.WATTS,
                charging_schedule_period=[
                    ChargingSchedulePeriod(start_period=0, limit=11000)
                ],
                duration=1800,  # 30 minutes
                start_schedule=profile_start
            )
        )

        manager.add_profile(1, profile)

        # Check at 40 minutes after start (beyond duration)
        check_time = profile_start + timedelta(minutes=40)
        
        schedule = manager.get_composite_schedule(
            connector_id=1,
            duration=600,
            charging_rate_unit=ChargingRateUnit.WATTS,
            start_time=check_time
        )

        assert schedule is None

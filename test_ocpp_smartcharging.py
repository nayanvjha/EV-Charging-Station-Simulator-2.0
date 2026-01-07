"""
Unit tests for OCPP 1.6 Smart Charging message handlers

Tests SetChargingProfile, GetCompositeSchedule, and ClearChargingProfile
OCPP operations, profile stacking behavior, and TxProfile priority.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from ocpp.v16.enums import ChargingProfileStatus, ClearChargingProfileStatus
from station import SimulatedChargePoint
from charging_profile_manager import (
    ChargingProfileManager,
    ChargingProfilePurpose,
    ChargingProfileKind,
    ChargingRateUnit,
)


@pytest.fixture
def mock_connection():
    """Mock WebSocket connection for ChargePoint."""
    mock_ws = MagicMock()
    mock_ws.send = AsyncMock()
    return mock_ws


@pytest.fixture
def charge_point(mock_connection):
    """Create a SimulatedChargePoint for testing."""
    cp = SimulatedChargePoint("TEST_STATION", mock_connection)
    return cp


class TestSetChargingProfile:
    """Test SetChargingProfile OCPP message handling."""

    @pytest.mark.asyncio
    async def test_set_valid_profile_accepted(self, charge_point):
        """Valid profile is accepted."""
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 22000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        response = await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile_dict
        )

        assert response.status == ChargingProfileStatus.accepted
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 1

    @pytest.mark.asyncio
    async def test_set_invalid_profile_rejected(self, charge_point):
        """Invalid profile is rejected."""
        profile_dict = {
            "chargingProfileId": -1,  # Invalid ID
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 22000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        response = await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile_dict
        )

        assert response.status == ChargingProfileStatus.rejected
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 0

    @pytest.mark.asyncio
    async def test_set_profile_stack_level_conflict_rejected(self, charge_point):
        """Profile with conflicting stackLevel is rejected."""
        # Add first profile
        profile1 = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 11000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile1
        )

        # Try to add second profile with same purpose and stackLevel
        profile2 = {
            "chargingProfileId": 2,
            "stackLevel": 0,  # Conflict
            "chargingProfilePurpose": "TxDefaultProfile",  # Same purpose
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 8000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        response = await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile2
        )

        assert response.status == ChargingProfileStatus.rejected
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 1

    @pytest.mark.asyncio
    async def test_set_profile_missing_required_field_rejected(self, charge_point):
        """Profile missing required field is rejected."""
        profile_dict = {
            "chargingProfileId": 1,
            # Missing stackLevel
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 22000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        response = await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile_dict
        )

        assert response.status == ChargingProfileStatus.rejected

    @pytest.mark.asyncio
    async def test_set_profile_connector_zero(self, charge_point):
        """Profile can be set on connector 0 (charge point level)."""
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 44000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        response = await charge_point.on_set_charging_profile(
            connector_id=0,
            cs_charging_profiles=profile_dict
        )

        assert response.status == ChargingProfileStatus.accepted
        assert len(charge_point.profile_manager.get_profiles_for_connector(0)) == 1

    @pytest.mark.asyncio
    async def test_set_tx_profile(self, charge_point):
        """TxProfile with transactionId is accepted."""
        profile_dict = {
            "chargingProfileId": 1,
            "transactionId": 123,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 11000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }

        response = await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile_dict
        )

        assert response.status == ChargingProfileStatus.accepted

    @pytest.mark.asyncio
    async def test_set_recurring_profile(self, charge_point):
        """Recurring profile with recurrencyKind is accepted."""
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Recurring",
            "recurrencyKind": "Daily",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 11000},
                    {"startPeriod": 3600, "limit": 7000}
                ],
                "startSchedule": "2026-01-08T08:00:00Z",
                "duration": 7200
            }
        }

        response = await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile_dict
        )

        assert response.status == ChargingProfileStatus.accepted


class TestGetCompositeSchedule:
    """Test GetCompositeSchedule OCPP message handling."""

    @pytest.mark.asyncio
    async def test_get_composite_schedule_single_profile(self, charge_point):
        """GetCompositeSchedule returns valid schedule from single profile."""
        # Add profile
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 11000},
                    {"startPeriod": 1800, "limit": 7000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile_dict
        )

        # Get composite schedule
        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        assert response.status == "Accepted"
        assert response.connector_id == 1
        assert response.charging_schedule is not None
        assert len(response.charging_schedule["chargingSchedulePeriod"]) == 2

    @pytest.mark.asyncio
    async def test_get_composite_schedule_no_profiles_rejected(self, charge_point):
        """GetCompositeSchedule returns Rejected when no profiles exist."""
        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        assert response.status == "Rejected"

    @pytest.mark.asyncio
    async def test_get_composite_schedule_with_rate_unit(self, charge_point):
        """GetCompositeSchedule respects requested chargingRateUnit."""
        # Add profile
        profile_dict = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 11000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=profile_dict
        )

        # Get composite schedule with specific rate unit
        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit="W"
        )

        assert response.status == "Accepted"
        assert response.charging_schedule["chargingRateUnit"] == "W"

    @pytest.mark.asyncio
    async def test_get_composite_schedule_includes_connector_zero(self, charge_point):
        """GetCompositeSchedule includes connector 0 (charge point) profiles."""
        # Add charge point level profile
        cp_profile = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 22000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        # Add connector level profile
        conn_profile = {
            "chargingProfileId": 2,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 11000}
                ],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(
            connector_id=0,
            cs_charging_profiles=cp_profile
        )
        
        await charge_point.on_set_charging_profile(
            connector_id=1,
            cs_charging_profiles=conn_profile
        )

        # Get composite schedule - should use minimum (11kW)
        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        assert response.status == "Accepted"
        # Should take minimum of both profiles
        assert response.charging_schedule["chargingSchedulePeriod"][0]["limit"] == 11000


class TestClearChargingProfile:
    """Test ClearChargingProfile OCPP message handling."""

    @pytest.mark.asyncio
    async def test_clear_by_profile_id(self, charge_point):
        """ClearChargingProfile removes specific profile by ID."""
        # Add two profiles
        profile1 = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        profile2 = {
            "chargingProfileId": 2,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 8000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile1)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile2)

        # Clear profile 1
        response = await charge_point.on_clear_charging_profile(id=1, connector_id=1)

        assert response.status == ClearChargingProfileStatus.accepted
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 1
        assert charge_point.profile_manager.get_profiles_for_connector(1)[0].charging_profile_id == 2

    @pytest.mark.asyncio
    async def test_clear_by_purpose(self, charge_point):
        """ClearChargingProfile removes profiles by purpose."""
        # Add profiles with different purposes
        profile1 = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        profile2 = {
            "chargingProfileId": 2,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 22000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile1)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile2)

        # Clear TxDefaultProfile
        response = await charge_point.on_clear_charging_profile(
            connector_id=1,
            charging_profile_purpose="TxDefaultProfile"
        )

        assert response.status == ClearChargingProfileStatus.accepted
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 1
        profiles = charge_point.profile_manager.get_profiles_for_connector(1)
        assert profiles[0].charging_profile_purpose == ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE

    @pytest.mark.asyncio
    async def test_clear_by_stack_level(self, charge_point):
        """ClearChargingProfile removes profiles by stackLevel."""
        # Add profiles with different stack levels
        profile1 = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        profile2 = {
            "chargingProfileId": 2,
            "stackLevel": 1,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 8000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile1)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile2)

        # Clear stack level 0
        response = await charge_point.on_clear_charging_profile(
            connector_id=1,
            stack_level=0
        )

        assert response.status == ClearChargingProfileStatus.accepted
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 1
        assert charge_point.profile_manager.get_profiles_for_connector(1)[0].stack_level == 1

    @pytest.mark.asyncio
    async def test_clear_all_profiles(self, charge_point):
        """ClearChargingProfile removes all profiles when no filters provided."""
        # Add multiple profiles
        profile1 = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        profile2 = {
            "chargingProfileId": 2,
            "stackLevel": 1,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 22000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile1)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile2)

        # Clear all
        response = await charge_point.on_clear_charging_profile(connector_id=1)

        assert response.status == ClearChargingProfileStatus.accepted
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 0

    @pytest.mark.asyncio
    async def test_clear_connector_zero_clears_all(self, charge_point):
        """ClearChargingProfile with connector_id=0 clears from all connectors."""
        # Add profiles on multiple connectors
        profile1 = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        profile2 = {
            "chargingProfileId": 2,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile1)
        await charge_point.on_set_charging_profile(connector_id=2, cs_charging_profiles=profile2)

        # Clear from connector 0 (all connectors)
        response = await charge_point.on_clear_charging_profile(connector_id=0)

        assert response.status == ClearChargingProfileStatus.accepted
        assert len(charge_point.profile_manager.get_profiles_for_connector(1)) == 0
        assert len(charge_point.profile_manager.get_profiles_for_connector(2)) == 0

    @pytest.mark.asyncio
    async def test_clear_no_matching_profiles_unknown(self, charge_point):
        """ClearChargingProfile returns Unknown when no profiles match."""
        response = await charge_point.on_clear_charging_profile(
            connector_id=1,
            id=999  # Non-existent profile
        )

        assert response.status == ClearChargingProfileStatus.unknown


class TestProfileStacking:
    """Test multiple profiles stacking correctly."""

    @pytest.mark.asyncio
    async def test_multiple_profiles_stack_minimum(self, charge_point):
        """Multiple profiles stack with minimum limit applied."""
        # ChargePointMax: 22kW
        cp_profile = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 22000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        # TxDefault: 11kW (lower)
        tx_profile = {
            "chargingProfileId": 2,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=0, cs_charging_profiles=cp_profile)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=tx_profile)

        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        assert response.status == "Accepted"
        # Should use minimum (11kW)
        assert response.charging_schedule["chargingSchedulePeriod"][0]["limit"] == 11000

    @pytest.mark.asyncio
    async def test_tx_profile_overrides_tx_default(self, charge_point):
        """TxProfile overrides TxDefaultProfile when both present."""
        # TxDefault: 11kW
        tx_default = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        # TxProfile: 7kW (lower, higher priority)
        tx_profile = {
            "chargingProfileId": 2,
            "transactionId": 123,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 7000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=tx_default)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=tx_profile)

        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        assert response.status == "Accepted"
        # TxProfile takes priority, use minimum (7kW)
        assert response.charging_schedule["chargingSchedulePeriod"][0]["limit"] == 7000

    @pytest.mark.asyncio
    async def test_three_tier_stacking(self, charge_point):
        """ChargePointMax + TxProfile + TxDefault all stack correctly."""
        # ChargePointMax: 22kW
        cp_max = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 22000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        # TxDefault: 11kW
        tx_default = {
            "chargingProfileId": 2,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        # TxProfile: 16kW (between the two)
        tx_profile = {
            "chargingProfileId": 3,
            "transactionId": 123,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Absolute",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16000}],
                "startSchedule": "2026-01-08T10:00:00Z"
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=0, cs_charging_profiles=cp_max)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=tx_default)
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=tx_profile)

        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        assert response.status == "Accepted"
        # Should use minimum of all three (11kW from TxDefault)
        assert response.charging_schedule["chargingSchedulePeriod"][0]["limit"] == 11000


class TestExpiredProfiles:
    """Test expired profiles are ignored."""

    @pytest.mark.asyncio
    async def test_expired_profile_ignored(self, charge_point):
        """Expired profile (past validTo) is not included in composite schedule."""
        # Profile that expired 1 hour ago
        expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
        
        profile = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "validTo": expired_time.isoformat(),
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": expired_time.isoformat()
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile)

        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        # Should be rejected since profile is expired
        assert response.status == "Rejected"

    @pytest.mark.asyncio
    async def test_future_profile_ignored(self, charge_point):
        """Profile with future validFrom is not yet active."""
        # Profile that becomes valid 1 hour from now
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        profile = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "validFrom": future_time.isoformat(),
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": future_time.isoformat()
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile)

        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        # Should be rejected since profile is not yet valid
        assert response.status == "Rejected"

    @pytest.mark.asyncio
    async def test_valid_profile_within_time_window(self, charge_point):
        """Profile within validFrom/validTo window is included."""
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        profile = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Absolute",
            "validFrom": past_time.isoformat(),
            "validTo": future_time.isoformat(),
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 11000}],
                "startSchedule": past_time.isoformat()
            }
        }
        
        await charge_point.on_set_charging_profile(connector_id=1, cs_charging_profiles=profile)

        response = await charge_point.on_get_composite_schedule(
            connector_id=1,
            duration=3600
        )

        assert response.status == "Accepted"
        assert response.charging_schedule["chargingSchedulePeriod"][0]["limit"] == 11000

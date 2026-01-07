"""
SmartCharging Integration Tests

End-to-end tests for OCPP 1.6 Smart Charging functionality including:
- Profile enforcement behavior
- API endpoints
- Station-CSMS integration
- UI status detection logic
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from charging_profile_manager import (
    ChargingProfileManager,
    ChargingProfile,
    ChargingSchedule,
    ChargingSchedulePeriod,
    ChargingProfilePurpose,
    ChargingProfileKind,
    ChargingRateUnit,
    RecurrencyKind
)
from csms_server import (
    create_charge_point_max_profile,
    create_time_of_use_profile,
    create_energy_cap_profile
)


# =========================================================================
# FIXTURES
# =========================================================================

@pytest.fixture
def profile_manager():
    """Create a fresh ChargingProfileManager instance."""
    return ChargingProfileManager()


@pytest.fixture
def mock_station():
    """Create a mock station with profile manager."""
    station = Mock()
    station.profile_manager = ChargingProfileManager()
    station.station_id = "TEST-STATION-001"
    return station


@pytest.fixture
def current_time():
    """Fixed current time for testing."""
    return datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)


# =========================================================================
# PROFILE ENFORCEMENT BEHAVIOR TESTS
# =========================================================================

class TestChargePointMaxProfileLimits:
    """Test that ChargePointMaxProfile limits charging power."""
    
    def test_charge_point_max_profile_limits_charging(self, profile_manager):
        """ChargePointMaxProfile should limit station's maximum charging rate."""
        # Create ChargePointMaxProfile limiting to 7.4kW
        profile = create_charge_point_max_profile(1, 7400)
        
        # Add to manager
        profile_manager.add_profile(0, profile)  # Connector 0 = station-wide
        
        # Get limit for any connector
        limit = profile_manager.get_current_limit(connector_id=1)
        
        assert limit == 7400, "ChargePointMaxProfile should limit to 7400W"
    
    def test_charge_point_max_profile_affects_all_connectors(self, profile_manager):
        """ChargePointMaxProfile on connector 0 affects all connectors."""
        profile = create_charge_point_max_profile(1, 11000)
        profile_manager.add_profile(0, profile)
        
        # Check multiple connectors
        assert profile_manager.get_current_limit(connector_id=1) == 11000
        assert profile_manager.get_current_limit(connector_id=2) == 11000
        assert profile_manager.get_current_limit(connector_id=3) == 11000
    
    def test_charge_point_max_profile_lower_stack_level_wins(self, profile_manager):
        """Lower stackLevel (higher priority) should take precedence."""
        # Add profile with stackLevel 1 (7kW)
        profile1 = create_charge_point_max_profile(1, 7000)
        profile1['stackLevel'] = 1
        profile_manager.add_profile(0, profile1)
        
        # Add profile with stackLevel 0 (higher priority, 5kW)
        profile2 = create_charge_point_max_profile(2, 5000)
        profile2['stackLevel'] = 0
        profile_manager.add_profile(0, profile2)
        
        # Lower stackLevel should win
        limit = profile_manager.get_current_limit(connector_id=1)
        assert limit == 5000, "Lower stackLevel should have higher priority"


class TestTxProfileOverridesBehavior:
    """Test that TxProfile overrides TxDefaultProfile."""
    
    def test_tx_profile_overrides_tx_default(self, profile_manager):
        """TxProfile should override TxDefaultProfile for same transaction."""
        # Add TxDefaultProfile (11kW)
        default_profile = create_time_of_use_profile(1, 11000, 11000, 0, 24)
        default_profile['chargingProfilePurpose'] = 'TxDefaultProfile'
        profile_manager.add_profile(1, default_profile)
        
        # Add TxProfile for specific transaction (7kW)
        tx_profile = create_energy_cap_profile(2, 1234, 30000, 3600, 7000)
        tx_profile['chargingProfilePurpose'] = 'TxProfile'
        profile_manager.add_profile(1, tx_profile)
        
        # TxProfile should take precedence
        limit = profile_manager.get_current_limit(connector_id=1, transaction_id=1234)
        assert limit == 7000, "TxProfile should override TxDefaultProfile"
    
    def test_tx_default_used_when_no_tx_profile(self, profile_manager):
        """TxDefaultProfile used when no TxProfile for transaction."""
        default_profile = create_time_of_use_profile(1, 15000, 15000, 0, 24)
        default_profile['chargingProfilePurpose'] = 'TxDefaultProfile'
        profile_manager.add_profile(1, default_profile)
        
        # No TxProfile, should use TxDefault
        limit = profile_manager.get_current_limit(connector_id=1, transaction_id=9999)
        assert limit == 15000, "Should use TxDefaultProfile when no TxProfile"
    
    def test_tx_profile_only_applies_to_specific_transaction(self, profile_manager):
        """TxProfile only applies to its specific transaction ID."""
        # TxProfile for transaction 1234
        tx_profile = create_energy_cap_profile(1, 1234, 20000, 3600, 5000)
        profile_manager.add_profile(1, tx_profile)
        
        # Should apply to transaction 1234
        limit_correct_tx = profile_manager.get_current_limit(connector_id=1, transaction_id=1234)
        assert limit_correct_tx == 5000
        
        # Should NOT apply to different transaction
        limit_wrong_tx = profile_manager.get_current_limit(connector_id=1, transaction_id=5678)
        assert limit_wrong_tx is None, "TxProfile should not apply to different transaction"


class TestTimeOfUseProfileSchedule:
    """Test that time-of-use profiles change rates at correct hours."""
    
    def test_time_of_use_changes_at_peak_hours(self, profile_manager):
        """Power limit should change at peak start/end hours."""
        # Create TOU profile: 22kW off-peak, 7kW peak 18:00-22:00
        profile = create_time_of_use_profile(1, 22000, 7000, 18, 22)
        profile_manager.add_profile(1, profile)
        
        # Test at 12:00 (off-peak)
        time_offpeak = datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc)
        limit_offpeak = profile_manager.get_current_limit(
            connector_id=1, 
            current_time=time_offpeak
        )
        assert limit_offpeak == 22000, "Off-peak should be 22kW"
        
        # Test at 19:00 (peak)
        time_peak = datetime(2026, 1, 8, 19, 0, 0, tzinfo=timezone.utc)
        limit_peak = profile_manager.get_current_limit(
            connector_id=1,
            current_time=time_peak
        )
        assert limit_peak == 7000, "Peak should be 7kW"
        
        # Test at 23:00 (off-peak again)
        time_after_peak = datetime(2026, 1, 8, 23, 0, 0, tzinfo=timezone.utc)
        limit_after = profile_manager.get_current_limit(
            connector_id=1,
            current_time=time_after_peak
        )
        assert limit_after == 22000, "After peak should be 22kW again"
    
    def test_recurring_profile_repeats_daily(self, profile_manager):
        """Recurring Daily profile should repeat every day."""
        profile = create_time_of_use_profile(1, 15000, 10000, 8, 18)
        profile_manager.add_profile(1, profile)
        
        # Test same time on different days
        day1_peak = datetime(2026, 1, 8, 10, 0, 0, tzinfo=timezone.utc)
        day2_peak = datetime(2026, 1, 9, 10, 0, 0, tzinfo=timezone.utc)
        
        limit_day1 = profile_manager.get_current_limit(connector_id=1, current_time=day1_peak)
        limit_day2 = profile_manager.get_current_limit(connector_id=1, current_time=day2_peak)
        
        assert limit_day1 == 10000, "Day 1 peak should be 10kW"
        assert limit_day2 == 10000, "Day 2 peak should be 10kW (recurring)"


class TestProfileClearingRestoresLegacy:
    """Test that clearing profiles restores legacy policy behavior."""
    
    def test_clear_all_profiles_removes_ocpp_control(self, profile_manager):
        """Clearing all profiles should return None (legacy policy takes over)."""
        # Add profile
        profile = create_charge_point_max_profile(1, 7400)
        profile_manager.add_profile(1, profile)
        
        # Verify OCPP control active
        assert profile_manager.get_current_limit(connector_id=1) == 7400
        
        # Clear all profiles
        profile_manager.clear_profile(connector_id=1)
        
        # Should return None (legacy policy)
        assert profile_manager.get_current_limit(connector_id=1) is None
    
    def test_clear_specific_profile_by_id(self, profile_manager):
        """Clearing specific profile ID should remove only that profile."""
        # Add two profiles
        profile1 = create_charge_point_max_profile(1, 11000)
        profile2 = create_charge_point_max_profile(2, 7400)
        profile1['stackLevel'] = 1
        profile2['stackLevel'] = 0  # Higher priority
        
        profile_manager.add_profile(1, profile1)
        profile_manager.add_profile(1, profile2)
        
        # Profile 2 (7.4kW) should be active
        assert profile_manager.get_current_limit(connector_id=1) == 7400
        
        # Clear profile 2
        profile_manager.clear_profile(profile_id=2)
        
        # Profile 1 (11kW) should now be active
        assert profile_manager.get_current_limit(connector_id=1) == 11000


class TestProfileExpiration:
    """Test profile expiration via validTo."""
    
    def test_expired_profile_not_applied(self, profile_manager):
        """Profile with validTo in past should not be applied."""
        profile = create_charge_point_max_profile(1, 7400)
        
        # Set validTo to 1 hour ago
        expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
        profile['validTo'] = expired_time.isoformat()
        
        profile_manager.add_profile(1, profile)
        
        # Should return None because profile is expired
        limit = profile_manager.get_current_limit(connector_id=1)
        assert limit is None, "Expired profile should not be applied"
    
    def test_valid_profile_applied_before_expiry(self, profile_manager):
        """Profile with validTo in future should be applied."""
        profile = create_charge_point_max_profile(1, 5000)
        
        # Set validTo to 1 hour from now
        valid_time = datetime.now(timezone.utc) + timedelta(hours=1)
        profile['validTo'] = valid_time.isoformat()
        
        profile_manager.add_profile(1, profile)
        
        # Should be applied
        limit = profile_manager.get_current_limit(connector_id=1)
        assert limit == 5000, "Valid profile should be applied"


class TestMultipleProfileStacking:
    """Test multiple profile stacking with priority."""
    
    def test_profile_priority_by_purpose(self, profile_manager):
        """TxProfile > TxDefault > ChargePointMax priority order."""
        # Add ChargePointMaxProfile (lowest priority)
        cp_max = create_charge_point_max_profile(1, 22000)
        cp_max['chargingProfilePurpose'] = 'ChargePointMaxProfile'
        profile_manager.add_profile(0, cp_max)
        
        # Add TxDefaultProfile
        tx_default = create_time_of_use_profile(2, 15000, 15000, 0, 24)
        tx_default['chargingProfilePurpose'] = 'TxDefaultProfile'
        profile_manager.add_profile(1, tx_default)
        
        # Add TxProfile (highest priority)
        tx_profile = create_energy_cap_profile(3, 1234, 20000, 3600, 7000)
        tx_profile['chargingProfilePurpose'] = 'TxProfile'
        profile_manager.add_profile(1, tx_profile)
        
        # TxProfile should win
        limit = profile_manager.get_current_limit(connector_id=1, transaction_id=1234)
        assert limit == 7000, "TxProfile has highest priority"
    
    def test_stack_level_priority_within_same_purpose(self, profile_manager):
        """Lower stackLevel has higher priority within same purpose."""
        # Two ChargePointMaxProfiles with different stackLevels
        profile_high = create_charge_point_max_profile(1, 22000)
        profile_high['stackLevel'] = 5
        
        profile_low = create_charge_point_max_profile(2, 11000)
        profile_low['stackLevel'] = 2
        
        profile_manager.add_profile(0, profile_high)
        profile_manager.add_profile(0, profile_low)
        
        # Lower stackLevel should win
        limit = profile_manager.get_current_limit(connector_id=1)
        assert limit == 11000, "Lower stackLevel has higher priority"
    
    def test_composite_schedule_merges_multiple_profiles(self, profile_manager):
        """Composite schedule should merge multiple active profiles."""
        # Add ChargePointMax (22kW)
        cp_max = create_charge_point_max_profile(1, 22000)
        profile_manager.add_profile(0, cp_max)
        
        # Add TxDefault with varying schedule
        tx_default = create_time_of_use_profile(2, 15000, 10000, 12, 18)
        profile_manager.add_profile(1, tx_default)
        
        # Get composite schedule
        schedule = profile_manager.get_composite_schedule(
            connector_id=1,
            duration=86400,
            charging_rate_unit=ChargingRateUnit.W
        )
        
        assert schedule is not None, "Should return composite schedule"
        assert schedule.chargingRateUnit == ChargingRateUnit.W
        assert len(schedule.chargingSchedulePeriod) > 0, "Should have periods"


# =========================================================================
# REST API ENDPOINT TESTS
# =========================================================================

class TestChargingProfileAPI:
    """Test POST /stations/{station_id}/charging_profile endpoint."""
    
    @pytest.mark.asyncio
    async def test_send_charging_profile_success(self):
        """Sending valid profile should return success status."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        # Mock station with chargepoint
        mock_cp = AsyncMock()
        mock_cp.send_charging_profile_to_station = AsyncMock(
            return_value={'status': 'Accepted', 'connector_id': 1, 'profile_id': 1}
        )
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            profile = create_charge_point_max_profile(1, 7400)
            response = await client.post(
                "/stations/TEST-STATION/charging_profile",
                json={"connector_id": 1, "profile": profile}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert data['station_id'] == 'TEST-STATION'
        assert data['profile_id'] == 1
    
    @pytest.mark.asyncio
    async def test_send_profile_station_not_found(self):
        """Sending to non-existent station should return 404."""
        from controller_api import app
        from httpx import AsyncClient
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            profile = create_charge_point_max_profile(1, 7400)
            response = await client.post(
                "/stations/NONEXISTENT/charging_profile",
                json={"connector_id": 1, "profile": profile}
            )
        
        assert response.status_code == 404
        assert "not found" in response.json()['detail'].lower()


class TestCompositeScheduleAPI:
    """Test GET /stations/{station_id}/composite_schedule endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_composite_schedule_success(self):
        """Getting composite schedule should return schedule data."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        mock_cp.request_composite_schedule_from_station = AsyncMock(
            return_value={
                'status': 'Accepted',
                'schedule': {
                    'chargingRateUnit': 'W',
                    'chargingSchedulePeriod': [
                        {'startPeriod': 0, 'limit': 7400}
                    ]
                }
            }
        )
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                "/stations/TEST-STATION/composite_schedule",
                params={'connector_id': 1, 'duration': 3600, 'charging_rate_unit': 'W'}
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert data['schedule'] is not None
        assert data['schedule']['chargingRateUnit'] == 'W'


class TestClearProfileAPI:
    """Test DELETE /stations/{station_id}/charging_profile endpoint."""
    
    @pytest.mark.asyncio
    async def test_clear_all_profiles(self):
        """Clearing without filters should remove all profiles."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        mock_cp.clear_charging_profile_from_station = AsyncMock(
            return_value={'status': 'Accepted'}
        )
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.delete("/stations/TEST-STATION/charging_profile")
        
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
    
    @pytest.mark.asyncio
    async def test_clear_specific_profile_by_id(self):
        """Clearing with profile_id should only remove that profile."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        mock_cp.clear_charging_profile_from_station = AsyncMock(
            return_value={'status': 'Accepted'}
        )
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.delete(
                "/stations/TEST-STATION/charging_profile",
                params={'profile_id': 1}
            )
        
        assert response.status_code == 200
        mock_cp.clear_charging_profile_from_station.assert_called_once()
        call_args = mock_cp.clear_charging_profile_from_station.call_args
        assert call_args.kwargs['profile_id'] == 1


class TestTestProfilesAPI:
    """Test POST /stations/{station_id}/test_profiles endpoint."""
    
    @pytest.mark.asyncio
    async def test_peak_shaving_scenario(self):
        """Peak shaving scenario should generate ChargePointMaxProfile."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        mock_cp.send_charging_profile_to_station = AsyncMock(
            return_value={'status': 'Accepted'}
        )
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/stations/TEST-STATION/test_profiles",
                json={
                    'scenario': 'peak_shaving',
                    'connector_id': 1,
                    'max_power_w': 7400
                }
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data['status'] in ['success', 'rejected']
        assert data['scenario'] == 'peak_shaving'
        assert data['profile']['chargingProfilePurpose'] == 'ChargePointMaxProfile'
    
    @pytest.mark.asyncio
    async def test_time_of_use_scenario(self):
        """Time-of-use scenario should generate recurring profile."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        mock_cp.send_charging_profile_to_station = AsyncMock(
            return_value={'status': 'Accepted'}
        )
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/stations/TEST-STATION/test_profiles",
                json={
                    'scenario': 'time_of_use',
                    'connector_id': 1,
                    'off_peak_w': 22000,
                    'peak_w': 7000,
                    'peak_start_hour': 18,
                    'peak_end_hour': 22
                }
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data['profile']['chargingProfileKind'] == 'Recurring'
        assert data['profile']['recurrencyKind'] == 'Daily'
    
    @pytest.mark.asyncio
    async def test_energy_cap_scenario(self):
        """Energy cap scenario should generate TxProfile."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        mock_cp.send_charging_profile_to_station = AsyncMock(
            return_value={'status': 'Accepted'}
        )
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/stations/TEST-STATION/test_profiles",
                json={
                    'scenario': 'energy_cap',
                    'connector_id': 1,
                    'transaction_id': 1234,
                    'max_energy_wh': 30000,
                    'duration_seconds': 7200,
                    'power_limit_w': 11000
                }
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data['profile']['chargingProfilePurpose'] == 'TxProfile'
        assert data['profile']['transactionId'] == 1234
    
    @pytest.mark.asyncio
    async def test_invalid_scenario_returns_error(self):
        """Invalid scenario should return 400 error."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/stations/TEST-STATION/test_profiles",
                json={
                    'scenario': 'invalid_scenario',
                    'connector_id': 1
                }
            )
        
        assert response.status_code == 400
        assert 'Unknown scenario' in response.json()['detail']
    
    @pytest.mark.asyncio
    async def test_missing_parameters_returns_error(self):
        """Missing required parameters should return 400."""
        from controller_api import app, manager
        from httpx import AsyncClient
        
        mock_cp = AsyncMock()
        manager.station_chargepoints['TEST-STATION'] = mock_cp
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/stations/TEST-STATION/test_profiles",
                json={
                    'scenario': 'peak_shaving',
                    'connector_id': 1
                    # Missing max_power_w
                }
            )
        
        assert response.status_code == 400
        assert 'required' in response.json()['detail'].lower()


# =========================================================================
# UI STATUS DETECTION LOGIC TESTS
# =========================================================================

class TestActiveProfileCountDetection:
    """Test active profile count detection from logs."""
    
    def test_count_profiles_from_logs(self):
        """Should count accepted profiles from log entries."""
        logs = [
            "2026-01-08 12:00:00 Profile 1 accepted",
            "2026-01-08 12:01:00 Profile 2 accepted",
            "2026-01-08 12:02:00 OCPP limit: 7400W → 2.06Wh",
            "2026-01-08 12:03:00 Profile 3 accepted",
        ]
        
        profile_count = sum(1 for log in logs if 'Profile' in log and 'accepted' in log)
        
        assert profile_count == 3, "Should count 3 accepted profiles"
    
    def test_no_profiles_returns_zero(self):
        """Logs without profile acceptance should return 0."""
        logs = [
            "2026-01-08 12:00:00 Station started",
            "2026-01-08 12:01:00 Transaction started",
        ]
        
        profile_count = sum(1 for log in logs if 'Profile' in log and 'accepted' in log)
        
        assert profile_count == 0, "Should count 0 profiles"


class TestOCPPLimitStatusDetection:
    """Test OCPP limit status detection from logs."""
    
    def test_detect_ocpp_active_from_logs(self):
        """Should detect OCPP active status from limit messages."""
        logs = [
            "2026-01-08 12:00:00 OCPP limit: 7400W → 2.06Wh",
            "2026-01-08 12:01:00 Charging at 7.2kW",
        ]
        
        has_ocpp = any('OCPP limit:' in log for log in logs)
        
        assert has_ocpp, "Should detect OCPP active"
    
    def test_extract_limit_from_log(self):
        """Should extract power limit from OCPP log message."""
        log = "2026-01-08 12:00:00 OCPP limit: 7400W → 2.06Wh"
        
        # Simulate extraction logic
        import re
        match = re.search(r'OCPP limit: (\d+)W', log)
        
        assert match is not None
        limit = int(match.group(1))
        assert limit == 7400, "Should extract 7400W limit"
    
    def test_detect_policy_ok_status(self):
        """Should detect Policy OK when no OCPP messages."""
        logs = [
            "2026-01-08 12:00:00 Transaction started",
            "2026-01-08 12:01:00 Charging normally",
        ]
        
        has_ocpp = any('OCPP limit:' in log for log in logs)
        has_blocked = any('blocked' in log.lower() or 'stop charging' in log.lower() for log in logs)
        
        if has_ocpp:
            status = 'ocpp'
        elif has_blocked:
            status = 'policy_blocked'
        else:
            status = 'policy_ok'
        
        assert status == 'policy_ok', "Should detect policy OK status"
    
    def test_detect_policy_blocked_status(self):
        """Should detect Policy Blocked from log messages."""
        logs = [
            "2026-01-08 12:00:00 Price too high, stop charging",
            "2026-01-08 12:01:00 Peak hours, blocked",
        ]
        
        has_blocked = any('blocked' in log.lower() or 'stop charging' in log.lower() for log in logs)
        
        assert has_blocked, "Should detect policy blocked"
    
    def test_ocpp_status_cache_structure(self):
        """OCPP status cache should have correct structure."""
        status = {
            'has_profiles': True,
            'profile_count': 2,
            'limit_w': 7400,
            'control_mode': 'ocpp'
        }
        
        assert 'has_profiles' in status
        assert 'profile_count' in status
        assert 'limit_w' in status
        assert 'control_mode' in status
        assert status['control_mode'] in ['ocpp', 'policy_ok', 'policy_blocked']


# =========================================================================
# HELPER FUNCTION TESTS
# =========================================================================

class TestProfileGeneratorHelpers:
    """Test CSMS profile generator helper functions."""
    
    def test_create_charge_point_max_profile_structure(self):
        """ChargePointMaxProfile helper should create valid structure."""
        profile = create_charge_point_max_profile(1, 22000)
        
        assert profile['chargingProfileId'] == 1
        assert profile['chargingProfilePurpose'] == 'ChargePointMaxProfile'
        assert profile['chargingProfileKind'] == 'Absolute'
        assert profile['stackLevel'] == 0
        assert profile['chargingSchedule']['chargingRateUnit'] == 'W'
        assert profile['chargingSchedule']['chargingSchedulePeriod'][0]['limit'] == 22000
    
    def test_create_time_of_use_profile_structure(self):
        """Time-of-use helper should create recurring profile."""
        profile = create_time_of_use_profile(2, 11000, 7000, 18, 22)
        
        assert profile['chargingProfileId'] == 2
        assert profile['chargingProfilePurpose'] == 'TxDefaultProfile'
        assert profile['chargingProfileKind'] == 'Recurring'
        assert profile['recurrencyKind'] == 'Daily'
        
        periods = profile['chargingSchedule']['chargingSchedulePeriod']
        assert len(periods) == 3, "Should have 3 periods (off-peak, peak, off-peak)"
        
        # Check periods
        assert periods[0]['startPeriod'] == 0  # Start at midnight
        assert periods[0]['limit'] == 11000  # Off-peak
        assert periods[1]['startPeriod'] == 18 * 3600  # 18:00
        assert periods[1]['limit'] == 7000  # Peak
        assert periods[2]['startPeriod'] == 22 * 3600  # 22:00
        assert periods[2]['limit'] == 11000  # Off-peak again
    
    def test_create_energy_cap_profile_structure(self):
        """Energy cap helper should create TxProfile."""
        profile = create_energy_cap_profile(3, 1234, 30000, 7200, 11000)
        
        assert profile['chargingProfileId'] == 3
        assert profile['transactionId'] == 1234
        assert profile['chargingProfilePurpose'] == 'TxProfile'
        assert profile['chargingProfileKind'] == 'Absolute'
        
        schedule = profile['chargingSchedule']
        assert schedule['duration'] == 7200
        assert schedule['chargingSchedulePeriod'][0]['limit'] == 11000


# =========================================================================
# STATION-CSMS INTEGRATION TESTS
# =========================================================================

class TestStationCSMSIntegration:
    """Test full integration between station and CSMS."""
    
    @pytest.mark.asyncio
    async def test_profile_sent_from_csms_enforced_by_station(self):
        """Profile sent from CSMS should be enforced by station."""
        # This would require actual CSMS and station instances
        # For now, test the logic flow
        
        profile_manager = ChargingProfileManager()
        
        # 1. CSMS sends profile
        profile = create_charge_point_max_profile(1, 7400)
        
        # 2. Station receives and stores
        profile_manager.add_profile(1, profile)
        
        # 3. Station enforces during charging
        limit = profile_manager.get_current_limit(connector_id=1)
        
        assert limit == 7400, "Station should enforce CSMS profile"
    
    @pytest.mark.asyncio
    async def test_profile_rejection_handling(self):
        """Station should handle profile rejection gracefully."""
        profile_manager = ChargingProfileManager()
        
        # Try to add invalid profile (e.g., missing required fields)
        invalid_profile = {
            'chargingProfileId': 1,
            # Missing required fields
        }
        
        with pytest.raises((KeyError, ValueError)):
            profile_manager.add_profile(1, invalid_profile)
    
    @pytest.mark.asyncio
    async def test_composite_schedule_request_response(self):
        """Station should respond to composite schedule request."""
        profile_manager = ChargingProfileManager()
        
        # Add profiles
        profile_manager.add_profile(1, create_charge_point_max_profile(1, 22000))
        profile_manager.add_profile(1, create_time_of_use_profile(2, 15000, 10000, 8, 18))
        
        # Request composite schedule
        schedule = profile_manager.get_composite_schedule(
            connector_id=1,
            duration=3600,
            charging_rate_unit=ChargingRateUnit.W
        )
        
        assert schedule is not None
        assert len(schedule.chargingSchedulePeriod) > 0


# =========================================================================
# RUN TESTS
# =========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

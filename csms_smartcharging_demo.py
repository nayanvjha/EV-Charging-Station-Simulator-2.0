"""
Demo script for CSMS SmartCharging Testing Helpers

This script demonstrates how to use the new CSMS SmartCharging helpers
to control charging stations remotely via OCPP 1.6 Smart Charging profiles.

Usage:
    python csms_smartcharging_demo.py
"""

import asyncio
from datetime import datetime, timezone
from csms_server import (
    create_charge_point_max_profile,
    create_time_of_use_profile,
    create_energy_cap_profile,
)


def demo_profile_generators():
    """Demonstrate the profile generator helper functions."""
    
    print("=" * 70)
    print("OCPP Smart Charging Profile Generators Demo")
    print("=" * 70)
    print()
    
    # 1. ChargePointMaxProfile - Limit entire station
    print("1. ChargePointMaxProfile (Station-wide 22kW limit)")
    print("-" * 70)
    profile1 = create_charge_point_max_profile(
        profile_id=1,
        max_power_w=22000
    )
    print(f"   Profile ID: {profile1['chargingProfileId']}")
    print(f"   Purpose: {profile1['chargingProfilePurpose']}")
    print(f"   Limit: {profile1['chargingSchedule']['chargingSchedulePeriod'][0]['limit']}W")
    print(f"   Use Case: Limit entire charge point to 22kW maximum power")
    print()
    
    # 2. Time-of-Use Profile - Different limits for peak/off-peak
    print("2. TxDefaultProfile (Time-of-Use: 11kW off-peak, 7kW peak)")
    print("-" * 70)
    profile2 = create_time_of_use_profile(
        profile_id=2,
        off_peak_w=11000,
        peak_w=7000,
        peak_start_hour=8,
        peak_end_hour=18
    )
    print(f"   Profile ID: {profile2['chargingProfileId']}")
    print(f"   Purpose: {profile2['chargingProfilePurpose']}")
    print(f"   Kind: {profile2['chargingProfileKind']} ({profile2['recurrencyKind']})")
    print(f"   Schedule:")
    for period in profile2['chargingSchedule']['chargingSchedulePeriod']:
        hour = period['startPeriod'] // 3600
        print(f"     - From hour {hour:02d}:00 → {period['limit']}W")
    print(f"   Use Case: Daily recurring schedule with peak hour reduction")
    print()
    
    # 3. Energy Cap Profile - Limit specific transaction
    print("3. TxProfile (Transaction 1234: 30kWh cap, 2-hour duration)")
    print("-" * 70)
    profile3 = create_energy_cap_profile(
        profile_id=3,
        transaction_id=1234,
        max_energy_wh=30000,
        duration_seconds=7200,
        power_limit_w=11000
    )
    print(f"   Profile ID: {profile3['chargingProfileId']}")
    print(f"   Purpose: {profile3['chargingProfilePurpose']}")
    print(f"   Transaction ID: {profile3['transactionId']}")
    print(f"   Power Limit: {profile3['chargingSchedule']['chargingSchedulePeriod'][0]['limit']}W")
    print(f"   Duration: {profile3['chargingSchedule']['duration']}s ({profile3['chargingSchedule']['duration']/3600:.1f}h)")
    print(f"   Use Case: Limit specific transaction to 30kWh over 2 hours")
    print()


async def demo_csms_operations():
    """
    Demonstrate CSMS-initiated SmartCharging operations.
    
    Note: This requires a running station to actually execute.
    This demo shows the API usage patterns.
    """
    
    print("=" * 70)
    print("CSMS SmartCharging Operations API Usage")
    print("=" * 70)
    print()
    
    print("Example: Setting a charging profile")
    print("-" * 70)
    print("""
    # Create CSMS charge point connection (when station connects)
    csms_cp = CentralSystemChargePoint(station_id, websocket)
    
    # Generate a profile
    profile = create_charge_point_max_profile(1, 22000)
    
    # Send to station
    response = await csms_cp.send_charging_profile_to_station(
        connector_id=0,  # 0 = charge point level
        profile_dict=profile
    )
    
    if response['status'] == 'Accepted':
        print(f"✓ Profile {response['profile_id']} set successfully")
    else:
        print(f"✗ Profile rejected or error: {response}")
    """)
    print()
    
    print("Example: Requesting composite schedule")
    print("-" * 70)
    print("""
    # Request the merged schedule from all active profiles
    response = await csms_cp.request_composite_schedule_from_station(
        connector_id=1,
        duration=3600,  # 1 hour
        charging_rate_unit="W"
    )
    
    if response['status'] == 'Accepted':
        schedule = response['chargingSchedule']
        periods = schedule['chargingSchedulePeriod']
        print(f"Composite schedule has {len(periods)} periods:")
        for p in periods:
            print(f"  - From {p['startPeriod']}s: {p['limit']}W")
    """)
    print()
    
    print("Example: Clearing profiles")
    print("-" * 70)
    print("""
    # Clear specific profile
    response = await csms_cp.clear_charging_profile_from_station(
        profile_id=1
    )
    
    # Clear all profiles on a connector
    response = await csms_cp.clear_charging_profile_from_station(
        connector_id=1
    )
    
    # Clear all TxDefaultProfile profiles
    response = await csms_cp.clear_charging_profile_from_station(
        purpose="TxDefaultProfile"
    )
    
    # Clear all profiles everywhere
    response = await csms_cp.clear_charging_profile_from_station(
        connector_id=0
    )
    
    if response['status'] == 'Accepted':
        print("✓ Profiles cleared successfully")
    """)
    print()


def demo_use_cases():
    """Show common use case scenarios."""
    
    print("=" * 70)
    print("Common Use Case Scenarios")
    print("=" * 70)
    print()
    
    print("Use Case 1: Limit entire station during grid constraints")
    print("-" * 70)
    print("Scenario: Grid operator requests 50% power reduction")
    profile = create_charge_point_max_profile(100, 11000)  # Down from 22kW
    print(f"Solution: Set ChargePointMaxProfile with {profile['chargingSchedule']['chargingSchedulePeriod'][0]['limit']}W limit")
    print("Effect: All connectors combined cannot exceed 11kW")
    print()
    
    print("Use Case 2: Dynamic pricing with time-of-use rates")
    print("-" * 70)
    print("Scenario: Encourage off-peak charging with higher peak rates")
    profile = create_time_of_use_profile(101, 11000, 7000, 8, 18)
    print("Solution: TxDefaultProfile with daily schedule:")
    print("  - 00:00-08:00: 11kW (off-peak)")
    print("  - 08:00-18:00:  7kW (peak)")
    print("  - 18:00-24:00: 11kW (off-peak)")
    print("Effect: Automatic rate reduction during peak hours")
    print()
    
    print("Use Case 3: Fleet vehicle energy cap")
    print("-" * 70)
    print("Scenario: Fleet vehicle needs exactly 30kWh, no more")
    profile = create_energy_cap_profile(102, 5678, 30000, 7200, 11000)
    print("Solution: TxProfile for transaction with:")
    print("  - Max power: 11kW")
    print("  - Duration: 2 hours")
    print("  - Energy cap enforced by station logic")
    print("Effect: Transaction limited to specific energy amount")
    print()
    
    print("Use Case 4: Load balancing across multiple stations")
    print("-" * 70)
    print("Scenario: Building has 44kW total capacity, 4 stations")
    print("Solution: Set ChargePointMaxProfile on each station:")
    for i in range(1, 5):
        profile = create_charge_point_max_profile(110 + i, 11000)
        print(f"  - Station {i}: Profile {110+i} → 11kW max")
    print("Effect: Total building load stays at/below 44kW")
    print()
    
    print("Use Case 5: Emergency power reduction")
    print("-" * 70)
    print("Scenario: Grid emergency requires immediate reduction")
    profile = create_charge_point_max_profile(200, 3500)  # 3.5kW minimum
    print(f"Solution: Broadcast ChargePointMaxProfile with {profile['chargingSchedule']['chargingSchedulePeriod'][0]['limit']}W to all stations")
    print("Effect: All stations immediately reduce to minimum safe charging rate")
    print()


def demo_profile_priority():
    """Explain profile priority and stacking."""
    
    print("=" * 70)
    print("OCPP Profile Priority and Stacking")
    print("=" * 70)
    print()
    
    print("Profile Purpose Priority (highest to lowest):")
    print("-" * 70)
    print("1. ChargePointMaxProfile  → Station-wide maximum")
    print("2. TxProfile               → Specific transaction (highest priority for that TX)")
    print("3. TxDefaultProfile        → Default for all transactions on connector")
    print()
    
    print("How Profiles Stack:")
    print("-" * 70)
    print("• Station takes MINIMUM limit from all applicable profiles")
    print("• ChargePointMax (connector 0) applies to ALL connectors")
    print("• TxProfile overrides TxDefaultProfile for that transaction")
    print("• Lower stackLevel = higher priority within same purpose")
    print()
    
    print("Example Stacking Scenario:")
    print("-" * 70)
    print("Active profiles:")
    print("  1. ChargePointMaxProfile (connector 0): 22kW")
    print("  2. TxDefaultProfile (connector 1):      11kW")
    print("  3. TxProfile (tx 1234, connector 1):     7kW")
    print()
    print("Result for transaction 1234 on connector 1:")
    print("  → min(22kW, 11kW, 7kW) = 7kW  ✓")
    print()
    print("Result for other transactions on connector 1:")
    print("  → min(22kW, 11kW) = 11kW  ✓")
    print()


async def main():
    """Run all demos."""
    
    print("\n")
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  CSMS SmartCharging Testing Helpers - Demonstration".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "=" * 68 + "╝")
    print("\n")
    
    # Run demos
    demo_profile_generators()
    await demo_csms_operations()
    demo_use_cases()
    demo_profile_priority()
    
    print("=" * 70)
    print("Demo Complete!")
    print("=" * 70)
    print()
    print("Next Steps:")
    print("  1. Start CSMS server: python csms_server.py")
    print("  2. Start station: python station.py")
    print("  3. Use CSMS connection object to send profiles")
    print("  4. Monitor station logs for OCPP limit enforcement")
    print()


if __name__ == "__main__":
    asyncio.run(main())

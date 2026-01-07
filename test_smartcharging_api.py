#!/usr/bin/env python3
"""
SmartCharging REST API Testing Demo

This script demonstrates all SmartCharging REST API endpoints added to controller_api.py.

Prerequisites:
1. Start the CSMS server: python csms_server.py
2. Start the controller API: uvicorn controller_api:app --reload
3. Start at least one station through the API or UI

API Endpoints:
- POST   /stations/{station_id}/charging_profile     - Send a charging profile
- GET    /stations/{station_id}/composite_schedule   - Get composite schedule
- DELETE /stations/{station_id}/charging_profile     - Clear profiles
- POST   /stations/{station_id}/test_profiles        - Generate & send test profiles
"""

import requests
import json
from datetime import datetime, timezone

# Configuration
API_BASE_URL = "http://localhost:8000"
STATION_ID = "PY-SIM-0001"  # Change this to match your running station


def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_response(response):
    """Pretty print API response."""
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(f"Response:\n{json.dumps(data, indent=2)}")
    except:
        print(f"Response: {response.text}")


def test_send_custom_profile():
    """Test 1: Send a custom charging profile."""
    print_section("Test 1: Send Custom Charging Profile (22kW limit)")
    
    # Create a ChargePointMaxProfile
    profile = {
        "chargingProfileId": 100,
        "stackLevel": 0,
        "chargingProfilePurpose": "ChargePointMaxProfile",
        "chargingProfileKind": "Absolute",
        "chargingSchedule": {
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [
                {"startPeriod": 0, "limit": 22000}
            ],
            "startSchedule": datetime.now(timezone.utc).isoformat()
        }
    }
    
    payload = {
        "connector_id": 1,
        "profile": profile
    }
    
    print(f"\nSending to: POST {API_BASE_URL}/stations/{STATION_ID}/charging_profile")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/stations/{STATION_ID}/charging_profile",
            json=payload,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_get_composite_schedule():
    """Test 2: Get composite schedule from station."""
    print_section("Test 2: Get Composite Schedule")
    
    params = {
        "connector_id": 1,
        "duration": 3600,  # 1 hour
        "charging_rate_unit": "W"
    }
    
    print(f"\nSending to: GET {API_BASE_URL}/stations/{STATION_ID}/composite_schedule")
    print(f"Query params: {params}")
    
    try:
        response = requests.get(
            f"{API_BASE_URL}/stations/{STATION_ID}/composite_schedule",
            params=params,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_clear_specific_profile():
    """Test 3: Clear a specific profile by ID."""
    print_section("Test 3: Clear Specific Profile (ID 100)")
    
    params = {
        "profile_id": 100,
        "connector_id": 1
    }
    
    print(f"\nSending to: DELETE {API_BASE_URL}/stations/{STATION_ID}/charging_profile")
    print(f"Query params: {params}")
    
    try:
        response = requests.delete(
            f"{API_BASE_URL}/stations/{STATION_ID}/charging_profile",
            params=params,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_clear_all_profiles():
    """Test 4: Clear all profiles."""
    print_section("Test 4: Clear All Profiles")
    
    print(f"\nSending to: DELETE {API_BASE_URL}/stations/{STATION_ID}/charging_profile")
    print("Query params: (none - clears all)")
    
    try:
        response = requests.delete(
            f"{API_BASE_URL}/stations/{STATION_ID}/charging_profile",
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_peak_shaving_scenario():
    """Test 5: Generate and send peak shaving profile."""
    print_section("Test 5: Test Profile - Peak Shaving (11kW)")
    
    payload = {
        "scenario": "peak_shaving",
        "connector_id": 1,
        "max_power_w": 11000
    }
    
    print(f"\nSending to: POST {API_BASE_URL}/stations/{STATION_ID}/test_profiles")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/stations/{STATION_ID}/test_profiles",
            json=payload,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_time_of_use_scenario():
    """Test 6: Generate and send time-of-use profile."""
    print_section("Test 6: Test Profile - Time of Use (11kW off-peak, 7kW peak 9-17)")
    
    payload = {
        "scenario": "time_of_use",
        "connector_id": 1,
        "off_peak_w": 11000,
        "peak_w": 7000,
        "peak_start_hour": 9,
        "peak_end_hour": 17
    }
    
    print(f"\nSending to: POST {API_BASE_URL}/stations/{STATION_ID}/test_profiles")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/stations/{STATION_ID}/test_profiles",
            json=payload,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_energy_cap_scenario():
    """Test 7: Generate and send energy cap profile."""
    print_section("Test 7: Test Profile - Energy Cap (TX 5678, 25kWh, 2h, 11kW)")
    
    payload = {
        "scenario": "energy_cap",
        "connector_id": 1,
        "transaction_id": 5678,
        "max_energy_wh": 25000,
        "duration_seconds": 7200,  # 2 hours
        "power_limit_w": 11000
    }
    
    print(f"\nSending to: POST {API_BASE_URL}/stations/{STATION_ID}/test_profiles")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/stations/{STATION_ID}/test_profiles",
            json=payload,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_error_station_not_found():
    """Test 8: Error handling - station not found."""
    print_section("Test 8: Error Handling - Station Not Found")
    
    fake_station = "PY-SIM-9999"
    payload = {
        "scenario": "peak_shaving",
        "connector_id": 1,
        "max_power_w": 11000
    }
    
    print(f"\nSending to: POST {API_BASE_URL}/stations/{fake_station}/test_profiles")
    print(f"Expected: 404 Not Found")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/stations/{fake_station}/test_profiles",
            json=payload,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_error_invalid_scenario():
    """Test 9: Error handling - invalid scenario."""
    print_section("Test 9: Error Handling - Invalid Scenario")
    
    payload = {
        "scenario": "invalid_scenario",
        "connector_id": 1
    }
    
    print(f"\nSending to: POST {API_BASE_URL}/stations/{STATION_ID}/test_profiles")
    print(f"Expected: 400 Bad Request")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/stations/{STATION_ID}/test_profiles",
            json=payload,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def test_error_missing_params():
    """Test 10: Error handling - missing required parameters."""
    print_section("Test 10: Error Handling - Missing Required Parameters")
    
    payload = {
        "scenario": "time_of_use",
        "connector_id": 1,
        # Missing: off_peak_w, peak_w, peak_start_hour, peak_end_hour
    }
    
    print(f"\nSending to: POST {API_BASE_URL}/stations/{STATION_ID}/test_profiles")
    print(f"Expected: 400 Bad Request")
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/stations/{STATION_ID}/test_profiles",
            json=payload,
            timeout=5
        )
        print_response(response)
    except Exception as e:
        print(f"Error: {e}")


def check_prerequisites():
    """Check if API and station are available."""
    print_section("Prerequisites Check")
    
    # Check API
    try:
        response = requests.get(f"{API_BASE_URL}/stations", timeout=2)
        if response.status_code == 200:
            print("✓ Controller API is running")
            stations = response.json()
            print(f"  Found {len(stations)} station(s)")
            
            if stations:
                print("\n  Available stations:")
                for station in stations:
                    print(f"    - {station['station_id']} (running: {station['running']})")
                    
                # Update global STATION_ID if needed
                global STATION_ID
                if not any(s['station_id'] == STATION_ID for s in stations):
                    STATION_ID = stations[0]['station_id']
                    print(f"\n  Using station: {STATION_ID}")
            else:
                print("\n  ⚠ No stations running. Start a station first:")
                print('    curl -X POST http://localhost:8000/stations/start \\')
                print('         -H "Content-Type: application/json" \\')
                print('         -d \'{"station_id": "PY-SIM-0001", "profile": "default"}\'')
                return False
        else:
            print(f"✗ API returned status {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Controller API is not accessible: {e}")
        print("\n  Start the API first:")
        print("    uvicorn controller_api:app --reload")
        return False
    
    return True


def main():
    """Run all tests."""
    print("=" * 80)
    print("  SmartCharging REST API Testing Demo")
    print("=" * 80)
    print(f"\nAPI Base URL: {API_BASE_URL}")
    print(f"Target Station: {STATION_ID}")
    
    if not check_prerequisites():
        print("\n⚠ Prerequisites not met. Exiting.")
        return
    
    print("\n" + "=" * 80)
    print("  Starting Tests")
    print("=" * 80)
    
    # Core functionality tests
    test_send_custom_profile()
    test_get_composite_schedule()
    
    # Test scenarios
    test_peak_shaving_scenario()
    test_time_of_use_scenario()
    test_energy_cap_scenario()
    
    # Clear operations
    test_clear_specific_profile()
    test_clear_all_profiles()
    
    # Error handling
    test_error_station_not_found()
    test_error_invalid_scenario()
    test_error_missing_params()
    
    print_section("Test Suite Complete")
    print("\nNext Steps:")
    print("  1. Check station logs: GET /stations/{station_id}/logs")
    print("  2. Monitor station behavior in the UI")
    print("  3. Verify profiles are enforced during charging")
    print("\nAPI Documentation:")
    print("  View interactive docs at: http://localhost:8000/docs")


if __name__ == "__main__":
    main()

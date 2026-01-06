"""
Smart Charging Policy Engine

A pure, testable function for determining charging decisions.
Centralizes all logic related to when a station should charge, wait, or pause
based on station state, smart charging profile, and environmental inputs.

This module supports:
- Price-based constraints (charge only if price below threshold)
- Peak hour constraints (allow/block charging during peak hours)
- Energy cap constraints (pause when max energy reached)
- Extensible decision framework for future constraints
"""


def evaluate_charging_policy(station_state: dict, profile: dict, env: dict) -> dict:
    """
    Pure function to evaluate whether a station should charge, wait, or pause.

    This function centralizes all smart charging decision-making into a single,
    testable, reusable logic unit. It returns a decision without modifying
    any external state.

    Args:
        station_state (dict): Current station state:
            - "energy_dispensed" (float): Total energy delivered in this session (kWh)
            - "charging" (bool): Whether the station is actively charging
            - "session_active" (bool): Whether a session has started

        profile (dict): Smart charging policy configuration:
            - "charge_if_price_below" (float): Price threshold (e.g., 25.0)
            - "max_energy_kwh" (float): Maximum energy per session (e.g., 30.0)
            - "allow_peak_hours" (bool): Whether to charge during peak hours
            - "peak_hours" (list of int): Hours considered peak (e.g., [18, 19, 20])

        env (dict): Environmental inputs:
            - "current_price" (float): Current electricity price (e.g., 18.50)
            - "hour" (int): Current hour of day (0-23)

    Returns:
        dict: Decision with two keys:
            - "action" (str): One of "charge", "wait", or "pause"
            - "reason" (str): Human-readable explanation of the decision

    Decision Rules (evaluated in order):
        1. If energy_dispensed >= max_energy_kwh → ("pause", "Energy cap reached")
        2. If current_price > charge_if_price_below → ("wait", "Price too high")
        3. If hour in peak_hours and not allow_peak_hours → ("wait", "Peak hour block")
        4. Otherwise → ("charge", "Conditions OK")

    Example:
        >>> station = {
        ...     "energy_dispensed": 15.0,
        ...     "charging": True,
        ...     "session_active": True
        ... }
        >>> policy = {
        ...     "charge_if_price_below": 20.0,
        ...     "max_energy_kwh": 30.0,
        ...     "allow_peak_hours": False,
        ...     "peak_hours": [18, 19, 20]
        ... }
        >>> env = {"current_price": 18.50, "hour": 19}
        >>> evaluate_charging_policy(station, policy, env)
        {'action': 'wait', 'reason': 'Peak hour block (hour 19)'}

    Notes:
        - This function is pure: no side effects, no logging, no external state
        - Logging should be handled by the caller based on the returned "reason"
        - The function does not control the station—only provides recommendations
        - Future enhancements can add new constraints while maintaining the interface
    """

    # Rule 1: Check energy cap
    if station_state["energy_dispensed"] >= profile["max_energy_kwh"]:
        return {
            "action": "pause",
            "reason": f"Energy cap reached ({station_state['energy_dispensed']:.1f}/"
                     f"{profile['max_energy_kwh']:.1f} kWh)"
        }

    # Rule 2: Check price constraint
    if env["current_price"] > profile["charge_if_price_below"]:
        return {
            "action": "wait",
            "reason": f"Price too high (₹{env['current_price']:.2f} > "
                     f"₹{profile['charge_if_price_below']:.2f})"
        }

    # Rule 3: Check peak hours constraint
    if env["hour"] in profile["peak_hours"] and not profile["allow_peak_hours"]:
        return {
            "action": "wait",
            "reason": f"Peak hour block (hour {env['hour']})"
        }

    # Rule 4: All conditions met
    return {
        "action": "charge",
        "reason": "Conditions OK"
    }


def evaluate_meter_value_decision(
    station_state: dict,
    profile: dict,
    env: dict,
    current_energy_wh: int,
    max_energy_wh: int
) -> dict:
    """
    Extended decision function for during meter value loop.
    
    Evaluates whether to continue charging during an active transaction.
    Includes energy cap check with precise Wh tracking.

    Args:
        station_state (dict): Current station state
        profile (dict): Smart charging policy
        env (dict): Environmental inputs
        current_energy_wh (int): Current energy accumulated in Wh
        max_energy_wh (int): Max energy for session in Wh

    Returns:
        dict: Decision with "action" ("continue" or "stop") and "reason"

    Decision Rules:
        1. If current_energy_wh >= max_energy_wh → ("stop", reason)
        2. Otherwise, evaluate main policy and return ("continue" or "stop")
    """

    # Check energy cap (precise Wh-based check)
    if current_energy_wh >= max_energy_wh:
        return {
            "action": "stop",
            "reason": f"Energy cap reached ({current_energy_wh/1000:.1f}/"
                     f"{max_energy_wh/1000:.1f} kWh)"
        }

    # Evaluate main policy
    main_decision = evaluate_charging_policy(station_state, profile, env)

    # Map main policy actions to meter loop actions
    if main_decision["action"] == "pause":
        return {"action": "stop", "reason": main_decision["reason"]}
    elif main_decision["action"] == "wait":
        return {"action": "stop", "reason": main_decision["reason"]}
    else:  # "charge"
        return {"action": "continue", "reason": main_decision["reason"]}

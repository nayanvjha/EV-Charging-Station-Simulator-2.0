"""
OCPP 1.6 Smart Charging Profile Manager

This module implements the ChargingProfile data structures and management
functionality as defined in OCPP 1.6 Specification Part 2, Section 5.

It provides:
- Dataclasses for ChargingProfile, ChargingSchedule, and ChargingSchedulePeriod
- Enums for ChargingProfilePurpose, ChargingProfileKind, RecurrencyKind, ChargingRateUnit
- ChargingProfileManager class for profile storage, validation, and schedule computation
- Helper functions for parsing and validating OCPP profile dictionaries

Key Features:
- Profile stacking with stackLevel priority (lower = higher priority)
- Composite schedule calculation merging multiple profiles
- Support for Absolute, Recurring, and Relative profile kinds
- Validation of profile structure per OCPP specification
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("charging_profile_manager")


# ============================================================================
# ENUMS
# ============================================================================

class ChargingProfilePurpose(Enum):
    """
    Purpose of the charging profile as defined in OCPP 1.6.
    
    - CHARGE_POINT_MAX_PROFILE: Limits the maximum power across all connectors
    - TX_DEFAULT_PROFILE: Default profile for all transactions on a connector
    - TX_PROFILE: Profile for a specific transaction (overrides TxDefault)
    """
    CHARGE_POINT_MAX_PROFILE = "ChargePointMaxProfile"
    TX_DEFAULT_PROFILE = "TxDefaultProfile"
    TX_PROFILE = "TxProfile"


class ChargingProfileKind(Enum):
    """
    Kind of charging profile determining how startSchedule is interpreted.
    
    - ABSOLUTE: startSchedule is an absolute ISO8601 datetime
    - RECURRING: Schedule repeats daily or weekly from startSchedule time-of-day
    - RELATIVE: Schedule starts relative to transaction start time
    """
    ABSOLUTE = "Absolute"
    RECURRING = "Recurring"
    RELATIVE = "Relative"


class RecurrencyKind(Enum):
    """
    Recurrency pattern for Recurring profiles.
    
    - DAILY: Schedule repeats every day at the same time
    - WEEKLY: Schedule repeats every week on the same day and time
    """
    DAILY = "Daily"
    WEEKLY = "Weekly"


class ChargingRateUnit(Enum):
    """
    Unit for charging rate limits.
    
    - WATTS: Power in watts (W)
    - AMPS: Current in amperes (A)
    """
    WATTS = "W"
    AMPS = "A"


# ============================================================================
# DATACLASSES
# ============================================================================

@dataclass
class ChargingSchedulePeriod:
    """
    A single period within a charging schedule.
    
    Attributes:
        start_period: Start of period in seconds from schedule start (0 = schedule start)
        limit: Power limit in watts or current limit in amps for this period
        number_phases: Optional number of phases to use (1, 2, or 3)
    """
    start_period: int
    limit: float
    number_phases: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to OCPP dictionary format."""
        result = {
            "startPeriod": self.start_period,
            "limit": self.limit
        }
        if self.number_phases is not None:
            result["numberPhases"] = self.number_phases
        return result


@dataclass
class ChargingSchedule:
    """
    Time-based charging schedule with power/current limits.
    
    Attributes:
        charging_rate_unit: Unit for limit values (W or A)
        charging_schedule_period: List of periods defining limits over time
        duration: Optional total duration in seconds
        start_schedule: Optional absolute start time (ISO8601)
        min_charging_rate: Optional minimum charging rate
    """
    charging_rate_unit: ChargingRateUnit
    charging_schedule_period: List[ChargingSchedulePeriod]
    duration: Optional[int] = None
    start_schedule: Optional[datetime] = None
    min_charging_rate: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert to OCPP dictionary format."""
        result = {
            "chargingRateUnit": self.charging_rate_unit.value,
            "chargingSchedulePeriod": [p.to_dict() for p in self.charging_schedule_period]
        }
        if self.duration is not None:
            result["duration"] = self.duration
        if self.start_schedule is not None:
            result["startSchedule"] = self.start_schedule.isoformat()
        if self.min_charging_rate is not None:
            result["minChargingRate"] = self.min_charging_rate
        return result


@dataclass
class ChargingProfile:
    """
    Complete charging profile as defined in OCPP 1.6.
    
    Attributes:
        charging_profile_id: Unique identifier for this profile
        stack_level: Priority level (0 = highest priority)
        charging_profile_purpose: Purpose of this profile
        charging_profile_kind: How startSchedule is interpreted
        charging_schedule: The schedule defining power/current limits
        transaction_id: Transaction this profile applies to (required for TxProfile)
        recurrency_kind: Daily or Weekly for Recurring profiles
        valid_from: Optional start of validity period
        valid_to: Optional end of validity period
    """
    charging_profile_id: int
    stack_level: int
    charging_profile_purpose: ChargingProfilePurpose
    charging_profile_kind: ChargingProfileKind
    charging_schedule: ChargingSchedule
    transaction_id: Optional[int] = None
    recurrency_kind: Optional[RecurrencyKind] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to OCPP dictionary format."""
        result = {
            "chargingProfileId": self.charging_profile_id,
            "stackLevel": self.stack_level,
            "chargingProfilePurpose": self.charging_profile_purpose.value,
            "chargingProfileKind": self.charging_profile_kind.value,
            "chargingSchedule": self.charging_schedule.to_dict()
        }
        if self.transaction_id is not None:
            result["transactionId"] = self.transaction_id
        if self.recurrency_kind is not None:
            result["recurrencyKind"] = self.recurrency_kind.value
        if self.valid_from is not None:
            result["validFrom"] = self.valid_from.isoformat()
        if self.valid_to is not None:
            result["validTo"] = self.valid_to.isoformat()
        return result


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """
    Parse ISO8601 datetime string to Python datetime object.
    
    Args:
        value: ISO8601 datetime string or None
        
    Returns:
        datetime object with timezone info, or None if input is None
        
    Raises:
        ValueError: If datetime string is invalid
    """
    if value is None:
        return None
    
    try:
        # Handle various ISO8601 formats
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        
        # Try parsing with fromisoformat (Python 3.7+)
        dt = datetime.fromisoformat(value)
        
        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        return dt
    except Exception as e:
        raise ValueError(f"Invalid datetime format: {value}") from e


def parse_charging_profile(profile_dict: dict) -> ChargingProfile:
    """
    Convert OCPP message dictionary to ChargingProfile dataclass.
    
    Handles ISO8601 datetime string parsing, enum value conversion,
    and nested schedule/period parsing.
    
    Args:
        profile_dict: Dictionary from OCPP SetChargingProfile message
        
    Returns:
        ChargingProfile dataclass instance
        
    Raises:
        ValueError: If required fields are missing or invalid
        
    Example:
        >>> profile_dict = {
        ...     "chargingProfileId": 1,
        ...     "stackLevel": 0,
        ...     "chargingProfilePurpose": "ChargePointMaxProfile",
        ...     "chargingProfileKind": "Absolute",
        ...     "chargingSchedule": {
        ...         "chargingRateUnit": "W",
        ...         "chargingSchedulePeriod": [
        ...             {"startPeriod": 0, "limit": 11000}
        ...         ]
        ...     }
        ... }
        >>> profile = parse_charging_profile(profile_dict)
        >>> profile.charging_profile_id
        1
    """
    # Validate required fields
    required_fields = [
        "chargingProfileId", "stackLevel", "chargingProfilePurpose",
        "chargingProfileKind", "chargingSchedule"
    ]
    for field_name in required_fields:
        if field_name not in profile_dict:
            raise ValueError(f"Missing required field: {field_name}")
    
    # Parse charging schedule
    schedule_dict = profile_dict["chargingSchedule"]
    if "chargingRateUnit" not in schedule_dict:
        raise ValueError("Missing required field in chargingSchedule: chargingRateUnit")
    if "chargingSchedulePeriod" not in schedule_dict:
        raise ValueError("Missing required field in chargingSchedule: chargingSchedulePeriod")
    
    # Parse charging rate unit
    try:
        rate_unit = ChargingRateUnit(schedule_dict["chargingRateUnit"])
    except ValueError:
        raise ValueError(f"Invalid chargingRateUnit: {schedule_dict['chargingRateUnit']}")
    
    # Parse periods
    periods = []
    for period_dict in schedule_dict["chargingSchedulePeriod"]:
        if "startPeriod" not in period_dict:
            raise ValueError("Missing required field in period: startPeriod")
        if "limit" not in period_dict:
            raise ValueError("Missing required field in period: limit")
        
        periods.append(ChargingSchedulePeriod(
            start_period=int(period_dict["startPeriod"]),
            limit=float(period_dict["limit"]),
            number_phases=period_dict.get("numberPhases")
        ))
    
    # Create schedule
    schedule = ChargingSchedule(
        charging_rate_unit=rate_unit,
        charging_schedule_period=periods,
        duration=schedule_dict.get("duration"),
        start_schedule=_parse_datetime(schedule_dict.get("startSchedule")),
        min_charging_rate=schedule_dict.get("minChargingRate")
    )
    
    # Parse purpose
    try:
        purpose = ChargingProfilePurpose(profile_dict["chargingProfilePurpose"])
    except ValueError:
        raise ValueError(f"Invalid chargingProfilePurpose: {profile_dict['chargingProfilePurpose']}")
    
    # Parse kind
    try:
        kind = ChargingProfileKind(profile_dict["chargingProfileKind"])
    except ValueError:
        raise ValueError(f"Invalid chargingProfileKind: {profile_dict['chargingProfileKind']}")
    
    # Parse optional recurrency kind
    recurrency_kind = None
    if "recurrencyKind" in profile_dict:
        try:
            recurrency_kind = RecurrencyKind(profile_dict["recurrencyKind"])
        except ValueError:
            raise ValueError(f"Invalid recurrencyKind: {profile_dict['recurrencyKind']}")
    
    # Create profile
    return ChargingProfile(
        charging_profile_id=int(profile_dict["chargingProfileId"]),
        stack_level=int(profile_dict["stackLevel"]),
        charging_profile_purpose=purpose,
        charging_profile_kind=kind,
        charging_schedule=schedule,
        transaction_id=profile_dict.get("transactionId"),
        recurrency_kind=recurrency_kind,
        valid_from=_parse_datetime(profile_dict.get("validFrom")),
        valid_to=_parse_datetime(profile_dict.get("validTo"))
    )


def validate_charging_profile(profile: ChargingProfile) -> Tuple[bool, str]:
    """
    Validate a ChargingProfile according to OCPP 1.6 specification.
    
    Checks:
    - chargingProfileId is positive integer
    - stackLevel is non-negative integer
    - chargingSchedulePeriod array is not empty
    - Periods are sorted by startPeriod ascending
    - All limits are positive numbers
    - transactionId present if purpose is TxProfile
    - recurrencyKind present if kind is Recurring
    - startSchedule present if kind is Absolute
    
    Args:
        profile: ChargingProfile to validate
        
    Returns:
        Tuple of (is_valid: bool, error_message: str)
        error_message is empty string if valid
        
    Example:
        >>> valid, msg = validate_charging_profile(profile)
        >>> if not valid:
        ...     print(f"Validation failed: {msg}")
    """
    # Check chargingProfileId is positive
    if profile.charging_profile_id <= 0:
        return False, f"chargingProfileId must be positive, got {profile.charging_profile_id}"
    
    # Check stackLevel is non-negative
    if profile.stack_level < 0:
        return False, f"stackLevel must be non-negative, got {profile.stack_level}"
    
    # Check periods not empty
    periods = profile.charging_schedule.charging_schedule_period
    if not periods:
        return False, "chargingSchedulePeriod array cannot be empty"
    
    # Check periods are sorted by startPeriod ascending
    for i in range(1, len(periods)):
        if periods[i].start_period < periods[i-1].start_period:
            return False, (
                f"chargingSchedulePeriod must be sorted by startPeriod ascending. "
                f"Period {i} has startPeriod {periods[i].start_period} < "
                f"previous period startPeriod {periods[i-1].start_period}"
            )
    
    # Check all limits are positive
    for i, period in enumerate(periods):
        if period.limit <= 0:
            return False, f"Period {i} has non-positive limit: {period.limit}"
    
    # Check transactionId present for TxProfile
    if profile.charging_profile_purpose == ChargingProfilePurpose.TX_PROFILE:
        if profile.transaction_id is None:
            return False, "transactionId is required for TxProfile purpose"
    
    # Check recurrencyKind present for Recurring kind
    if profile.charging_profile_kind == ChargingProfileKind.RECURRING:
        if profile.recurrency_kind is None:
            return False, "recurrencyKind is required for Recurring profile kind"
    
    # Check startSchedule present for Absolute kind
    if profile.charging_profile_kind == ChargingProfileKind.ABSOLUTE:
        if profile.charging_schedule.start_schedule is None:
            return False, "startSchedule is required for Absolute profile kind"
    
    return True, ""


# ============================================================================
# CHARGING PROFILE MANAGER CLASS
# ============================================================================

class ChargingProfileManager:
    """
    Manages charging profiles for a charge point.
    
    Provides storage, validation, and retrieval of charging profiles
    according to OCPP 1.6 specification. Handles:
    - Profile storage per connector
    - StackLevel conflict detection
    - Composite schedule calculation
    - Current limit retrieval
    
    Attributes:
        profiles: Dictionary mapping connector_id to list of ChargingProfile
        
    Example:
        >>> manager = ChargingProfileManager()
        >>> success, msg = manager.add_profile(1, profile)
        >>> if success:
        ...     limit = manager.get_current_limit(1)
    """
    
    def __init__(self):
        """Initialize the ChargingProfileManager with empty profile storage."""
        # Dictionary: connector_id -> list of ChargingProfile
        self.profiles: Dict[int, List[ChargingProfile]] = {}
        logger.info("ChargingProfileManager initialized")
    
    def add_profile(self, connector_id: int, profile: ChargingProfile) -> Tuple[bool, str]:
        """
        Add a charging profile to a connector.
        
        Validates the profile structure, checks for stackLevel conflicts
        with existing profiles of the same purpose, and stores if valid.
        
        Args:
            connector_id: Connector to add profile to (0 = charge point level)
            profile: ChargingProfile to add
            
        Returns:
            Tuple of (success: bool, message: str)
            
        Note:
            Profiles with identical purpose and stackLevel on the same
            connector are rejected per OCPP specification.
        """
        # Validate profile
        is_valid, error_msg = validate_charging_profile(profile)
        if not is_valid:
            logger.warning(
                f"Profile {profile.charging_profile_id} validation failed: {error_msg}"
            )
            return False, f"Validation failed: {error_msg}"
        
        # Initialize connector profile list if needed
        if connector_id not in self.profiles:
            self.profiles[connector_id] = []
        
        # Check for stackLevel conflict with same purpose
        for existing in self.profiles[connector_id]:
            if (existing.charging_profile_purpose == profile.charging_profile_purpose and
                existing.stack_level == profile.stack_level):
                logger.warning(
                    f"Profile {profile.charging_profile_id} rejected: "
                    f"stackLevel {profile.stack_level} conflict with profile "
                    f"{existing.charging_profile_id} for purpose "
                    f"{profile.charging_profile_purpose.value}"
                )
                return False, (
                    f"StackLevel conflict: profile {existing.charging_profile_id} "
                    f"already has stackLevel {profile.stack_level} for purpose "
                    f"{profile.charging_profile_purpose.value}"
                )
        
        # Check if profile with same ID exists and remove it (update)
        self.profiles[connector_id] = [
            p for p in self.profiles[connector_id]
            if p.charging_profile_id != profile.charging_profile_id
        ]
        
        # Store profile
        self.profiles[connector_id].append(profile)
        logger.info(
            f"Profile {profile.charging_profile_id} added to connector {connector_id} "
            f"(purpose={profile.charging_profile_purpose.value}, "
            f"stackLevel={profile.stack_level})"
        )
        
        return True, "Profile accepted"
    
    def clear_profile(
        self,
        connector_id: int,
        profile_id: Optional[int] = None,
        purpose: Optional[ChargingProfilePurpose] = None,
        stack_level: Optional[int] = None
    ) -> int:
        """
        Remove profiles matching the provided criteria.
        
        Uses AND logic: all provided criteria must match for removal.
        If no filters provided, clears all profiles for the connector.
        
        Args:
            connector_id: Connector to clear profiles from
            profile_id: Optional specific profile ID to remove
            purpose: Optional purpose filter
            stack_level: Optional stackLevel filter
            
        Returns:
            Count of profiles removed
        """
        if connector_id not in self.profiles:
            logger.debug(f"No profiles exist for connector {connector_id}")
            return 0
        
        original_count = len(self.profiles[connector_id])
        
        def matches_criteria(p: ChargingProfile) -> bool:
            """Check if profile matches all provided criteria."""
            if profile_id is not None and p.charging_profile_id != profile_id:
                return False
            if purpose is not None and p.charging_profile_purpose != purpose:
                return False
            if stack_level is not None and p.stack_level != stack_level:
                return False
            return True
        
        # Keep profiles that DON'T match criteria
        self.profiles[connector_id] = [
            p for p in self.profiles[connector_id]
            if not matches_criteria(p)
        ]
        
        removed_count = original_count - len(self.profiles[connector_id])
        
        if removed_count > 0:
            logger.info(
                f"Cleared {removed_count} profiles from connector {connector_id} "
                f"(filters: profile_id={profile_id}, purpose={purpose}, "
                f"stack_level={stack_level})"
            )
        else:
            logger.debug(
                f"No profiles matched criteria on connector {connector_id}"
            )
        
        return removed_count
    
    def _get_effective_schedule_start(
        self,
        profile: ChargingProfile,
        reference_time: datetime,
        transaction_start: Optional[datetime] = None
    ) -> Optional[datetime]:
        """
        Calculate effective schedule start time based on profile kind.
        
        Args:
            profile: The charging profile
            reference_time: Current time for calculation
            transaction_start: Optional transaction start time for Relative profiles
            
        Returns:
            Effective schedule start datetime, or None if cannot be calculated
        """
        kind = profile.charging_profile_kind
        schedule = profile.charging_schedule
        
        if kind == ChargingProfileKind.ABSOLUTE:
            return schedule.start_schedule
        
        elif kind == ChargingProfileKind.RECURRING:
            if schedule.start_schedule is None:
                return None
            
            # Get time-of-day from start_schedule
            schedule_time = schedule.start_schedule.time()
            
            if profile.recurrency_kind == RecurrencyKind.DAILY:
                # Calculate today's occurrence
                today_start = reference_time.replace(
                    hour=schedule_time.hour,
                    minute=schedule_time.minute,
                    second=schedule_time.second,
                    microsecond=schedule_time.microsecond
                )
                
                # If today's start is in the future, use yesterday's
                if today_start > reference_time:
                    today_start = today_start - timedelta(days=1)
                
                return today_start
            
            elif profile.recurrency_kind == RecurrencyKind.WEEKLY:
                # Get day of week from start_schedule (0=Monday, 6=Sunday)
                target_weekday = schedule.start_schedule.weekday()
                current_weekday = reference_time.weekday()
                
                # Calculate days since last occurrence
                days_diff = (current_weekday - target_weekday) % 7
                
                # Get this week's occurrence
                this_week_start = reference_time.replace(
                    hour=schedule_time.hour,
                    minute=schedule_time.minute,
                    second=schedule_time.second,
                    microsecond=schedule_time.microsecond
                ) - timedelta(days=days_diff)
                
                # If this week's start is in the future, use last week's
                if this_week_start > reference_time:
                    this_week_start = this_week_start - timedelta(weeks=1)
                
                return this_week_start
        
        elif kind == ChargingProfileKind.RELATIVE:
            # Relative profiles require transaction start time
            if transaction_start is None:
                return None
            return transaction_start
        
        return None
    
    def _get_limit_at_time(
        self,
        profile: ChargingProfile,
        target_time: datetime,
        transaction_start: Optional[datetime] = None
    ) -> Optional[float]:
        """
        Get the power/current limit from a profile at a specific time.
        
        Args:
            profile: The charging profile
            target_time: Time to get limit for
            transaction_start: Optional transaction start for Relative profiles
            
        Returns:
            Limit value in W or A, or None if profile doesn't apply
        """
        schedule = profile.charging_schedule
        
        # Get effective schedule start
        schedule_start = self._get_effective_schedule_start(
            profile, target_time, transaction_start
        )
        
        if schedule_start is None:
            return None
        
        # Calculate seconds from schedule start
        elapsed = (target_time - schedule_start).total_seconds()
        
        if elapsed < 0:
            return None  # Target time is before schedule starts
        
        # Check duration
        if schedule.duration is not None and elapsed > schedule.duration:
            return None  # Past schedule duration
        
        # Find applicable period
        periods = schedule.charging_schedule_period
        applicable_period = None
        
        for period in periods:
            if period.start_period <= elapsed:
                applicable_period = period
            else:
                break
        
        if applicable_period is None:
            return None
        
        return applicable_period.limit
    
    def _is_profile_valid_at_time(
        self,
        profile: ChargingProfile,
        check_time: datetime
    ) -> bool:
        """
        Check if a profile is valid at a given time based on validFrom/validTo.
        
        Args:
            profile: The charging profile
            check_time: Time to check validity for
            
        Returns:
            True if profile is valid at the given time
        """
        if profile.valid_from is not None and check_time < profile.valid_from:
            return False
        if profile.valid_to is not None and check_time > profile.valid_to:
            return False
        return True
    
    def get_composite_schedule(
        self,
        connector_id: int,
        duration: int,
        charging_rate_unit: ChargingRateUnit,
        start_time: Optional[datetime] = None
    ) -> Optional[ChargingSchedule]:
        """
        Calculate composite charging schedule from all applicable profiles.
        
        Merges all valid profiles for the connector, taking the minimum
        limit at each time point. Compresses adjacent periods with
        identical limits.
        
        Args:
            connector_id: Connector to get schedule for
            duration: Duration in seconds for the schedule
            charging_rate_unit: Desired rate unit for the schedule
            start_time: Optional start time (defaults to now)
            
        Returns:
            ChargingSchedule with merged limits, or None if no profiles apply
            
        Note:
            - Relative profiles are excluded (need transaction start time)
            - Profiles are filtered by validFrom/validTo
            - Purpose priority: ChargePointMax > TxProfile > TxDefault
            - Lower stackLevel = higher priority within same purpose
        """
        if start_time is None:
            start_time = datetime.now(timezone.utc)
        
        # Collect all applicable profiles from connector 0 (charge point level) and requested connector
        all_profiles: List[ChargingProfile] = []
        
        # Connector 0 profiles (ChargePointMaxProfile)
        if 0 in self.profiles:
            all_profiles.extend(self.profiles[0])
        
        # Specific connector profiles
        if connector_id != 0 and connector_id in self.profiles:
            all_profiles.extend(self.profiles[connector_id])
        
        if not all_profiles:
            logger.debug(f"No profiles found for connector {connector_id}")
            return None
        
        # Filter valid profiles (excluding Relative as we don't have transaction start)
        valid_profiles = []
        for p in all_profiles:
            if p.charging_profile_kind == ChargingProfileKind.RELATIVE:
                continue  # Skip Relative profiles without transaction context
            if not self._is_profile_valid_at_time(p, start_time):
                continue
            valid_profiles.append(p)
        
        if not valid_profiles:
            logger.debug(f"No valid profiles for connector {connector_id} at {start_time}")
            return None
        
        # Sort by purpose priority, then by stackLevel
        purpose_priority = {
            ChargingProfilePurpose.CHARGE_POINT_MAX_PROFILE: 0,
            ChargingProfilePurpose.TX_PROFILE: 1,
            ChargingProfilePurpose.TX_DEFAULT_PROFILE: 2
        }
        
        valid_profiles.sort(key=lambda p: (
            purpose_priority.get(p.charging_profile_purpose, 99),
            p.stack_level
        ))
        
        # Calculate limits for each second and find minimum
        time_limits: Dict[int, float] = {}
        
        for second in range(duration):
            current_time = start_time + timedelta(seconds=second)
            min_limit = None
            
            for profile in valid_profiles:
                limit = self._get_limit_at_time(profile, current_time)
                if limit is not None:
                    if min_limit is None or limit < min_limit:
                        min_limit = limit
            
            if min_limit is not None:
                time_limits[second] = min_limit
        
        if not time_limits:
            logger.debug(f"No applicable limits calculated for connector {connector_id}")
            return None
        
        # Compress adjacent periods with identical limits
        periods: List[ChargingSchedulePeriod] = []
        sorted_seconds = sorted(time_limits.keys())
        
        current_start = sorted_seconds[0]
        current_limit = time_limits[current_start]
        
        for second in sorted_seconds[1:]:
            limit = time_limits[second]
            if limit != current_limit or second != sorted_seconds[sorted_seconds.index(second) - 1] + 1:
                # Limit changed or gap in coverage - save current period
                periods.append(ChargingSchedulePeriod(
                    start_period=current_start,
                    limit=current_limit
                ))
                current_start = second
                current_limit = limit
        
        # Add final period
        periods.append(ChargingSchedulePeriod(
            start_period=current_start,
            limit=current_limit
        ))
        
        logger.info(
            f"Composite schedule for connector {connector_id}: "
            f"{len(periods)} periods over {duration}s"
        )
        
        return ChargingSchedule(
            charging_rate_unit=charging_rate_unit,
            charging_schedule_period=periods,
            duration=duration,
            start_schedule=start_time
        )
    
    def get_current_limit(
        self,
        connector_id: int,
        transaction_id: Optional[int] = None
    ) -> Optional[float]:
        """
        Get the current charging limit for a connector.
        
        Calculates the minimum limit from all applicable profiles
        at the current moment.
        
        Args:
            connector_id: Connector to get limit for
            transaction_id: Optional transaction ID for TxProfile matching
            
        Returns:
            Limit in watts or amps, or None if no profiles apply
        """
        now = datetime.now(timezone.utc)
        
        # Collect applicable profiles
        all_profiles: List[ChargingProfile] = []
        
        # Connector 0 profiles (charge point level)
        if 0 in self.profiles:
            all_profiles.extend(self.profiles[0])
        
        # Specific connector profiles
        if connector_id != 0 and connector_id in self.profiles:
            all_profiles.extend(self.profiles[connector_id])
        
        if not all_profiles:
            return None
        
        # Filter valid profiles
        valid_profiles = []
        for p in all_profiles:
            # Check time validity
            if not self._is_profile_valid_at_time(p, now):
                continue
            
            # For TxProfile, must match transaction_id
            if p.charging_profile_purpose == ChargingProfilePurpose.TX_PROFILE:
                if transaction_id is None or p.transaction_id != transaction_id:
                    continue
            
            valid_profiles.append(p)
        
        if not valid_profiles:
            return None
        
        # Get minimum limit from all valid profiles
        min_limit = None
        
        for profile in valid_profiles:
            limit = self._get_limit_at_time(profile, now)
            if limit is not None:
                if min_limit is None or limit < min_limit:
                    min_limit = limit
        
        logger.debug(f"Current limit for connector {connector_id}: {min_limit}")
        return min_limit
    
    def get_profiles_for_connector(self, connector_id: int) -> List[ChargingProfile]:
        """
        Get all profiles for a connector.
        
        Args:
            connector_id: Connector to get profiles for
            
        Returns:
            List of ChargingProfile objects
        """
        return self.profiles.get(connector_id, []).copy()
    
    def get_all_connector_ids(self) -> List[int]:
        """
        Get all connector IDs that have profiles.
        
        Returns:
            List of connector IDs
        """
        return list(self.profiles.keys())

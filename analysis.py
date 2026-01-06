"""
Analysis and visualization for EV Charging Station Simulator.

Generates matplotlib charts showing:
- Price impact on charging behavior
- Profile comparisons
- Peak vs off-peak patterns
- Energy distribution
- Smart charging effectiveness
"""

import os
import logging
from datetime import datetime, timezone
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from profiles import DEFAULT_PROFILES

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analysis")

# Create reports directory
REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)

# ================== VISUALIZATION 1: PROFILE COMPARISON ==================

def plot_profile_comparison():
    """Compare profiles by their smart charging parameters."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Profile Comparison - Smart Charging Parameters", fontsize=16, fontweight="bold")
    
    profiles = DEFAULT_PROFILES
    names = list(profiles.keys())
    
    # Filter out disabled profiles
    names = [n for n in names if profiles[n].enable_transactions or n == "no-transactions"]
    
    # Data for each metric
    price_thresholds = [profiles[n].charge_if_price_below for n in names]
    max_energy = [profiles[n].max_energy_kwh for n in names]
    allow_peak = [1 if profiles[n].allow_peak else 0 for n in names]
    idle_min = [profiles[n].idle_min for n in names]
    
    colors = ["#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#a855f7"]
    
    # Chart 1: Price Threshold
    ax = axes[0, 0]
    bars1 = ax.bar(names, price_thresholds, color=colors, alpha=0.8, edgecolor="white", linewidth=1.5)
    ax.set_ylabel("₹/kWh", fontweight="bold")
    ax.set_title("Price Threshold (charge_if_price_below)", fontweight="bold")
    ax.set_ylim(0, 35)
    ax.grid(axis="y", alpha=0.3)
    for bar, val in zip(bars1, price_thresholds):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'₹{val:.0f}', ha='center', va='bottom', fontsize=9, fontweight="bold")
    
    # Chart 2: Max Energy
    ax = axes[0, 1]
    bars2 = ax.bar(names, max_energy, color=colors, alpha=0.8, edgecolor="white", linewidth=1.5)
    ax.set_ylabel("kWh", fontweight="bold")
    ax.set_title("Max Energy Cap (max_energy_kwh)", fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    for bar, val in zip(bars2, max_energy):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{val:.0f}', ha='center', va='bottom', fontsize=9, fontweight="bold")
    
    # Chart 3: Peak Hour Permission
    ax = axes[1, 0]
    peak_labels = ["✓ Peak\nAllowed" if p else "✗ No\nPeak" for p in allow_peak]
    peak_colors = ["#22c55e" if p else "#ef4444" for p in allow_peak]
    bars3 = ax.bar(names, [1]*len(names), color=peak_colors, alpha=0.8, edgecolor="white", linewidth=1.5)
    ax.set_ylim(0, 1.3)
    ax.set_yticks([])
    ax.set_title("Peak Hour Charging Permission (allow_peak)", fontweight="bold")
    for bar, label in zip(bars3, peak_labels):
        ax.text(bar.get_x() + bar.get_width()/2., 0.5,
                label, ha='center', va='center', fontsize=9, fontweight="bold", color="white")
    
    # Chart 4: Idle Time Range
    ax = axes[1, 1]
    idle_max = [profiles[n].idle_max for n in names]
    x_pos = np.arange(len(names))
    ax.bar(x_pos - 0.2, idle_min, 0.4, label="Min Idle", color="#3b82f6", alpha=0.8, edgecolor="white")
    ax.bar(x_pos + 0.2, idle_max, 0.4, label="Max Idle", color="#f59e0b", alpha=0.8, edgecolor="white")
    ax.set_xticks(x_pos)
    ax.set_xticklabels(names)
    ax.set_ylabel("Seconds", fontweight="bold")
    ax.set_title("Idle Time Between Sessions", fontweight="bold")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    
    plt.tight_layout()
    filepath = os.path.join(REPORTS_DIR, "1_profile_comparison.png")
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#050816", edgecolor="none")
    logger.info(f"✓ Saved: {filepath}")
    plt.close()

# ================== VISUALIZATION 2: PRICE IMPACT ==================

def plot_price_impact():
    """Show how different profiles respond to price changes."""
    fig, ax = plt.subplots(figsize=(12, 7))
    
    profiles = DEFAULT_PROFILES
    active_profiles = {k: v for k, v in profiles.items() if v.enable_transactions}
    
    price_range = np.arange(10, 35, 1)
    
    for profile_name, profile in active_profiles.items():
        threshold = profile.charge_if_price_below
        will_charge = [1 if p <= threshold else 0 for p in price_range]
        ax.plot(price_range, will_charge, marker='o', linewidth=2.5, markersize=6, label=profile_name)
    
    ax.axvline(x=20, color="white", linestyle="--", alpha=0.5, linewidth=1.5, label="Default Price (₹20)")
    ax.set_xlabel("Price (₹/kWh)", fontweight="bold", fontsize=11)
    ax.set_ylabel("Will Charge (1=Yes, 0=No)", fontweight="bold", fontsize=11)
    ax.set_title("Price Impact on Smart Charging Decisions", fontweight="bold", fontsize=14)
    ax.set_ylim(-0.1, 1.2)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["No", "Yes"])
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", fontsize=10)
    
    plt.tight_layout()
    filepath = os.path.join(REPORTS_DIR, "2_price_impact.png")
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#050816", edgecolor="none")
    logger.info(f"✓ Saved: {filepath}")
    plt.close()

# ================== VISUALIZATION 3: PEAK VS OFF-PEAK ==================

def plot_peak_vs_offpeak():
    """Show peak and off-peak charging patterns."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle("Peak vs Off-Peak Charging Behavior", fontsize=14, fontweight="bold")
    
    profiles = DEFAULT_PROFILES
    active_profiles = {k: v for k, v in profiles.items() if v.enable_transactions}
    
    names = list(active_profiles.keys())
    allow_peak = [1 if active_profiles[n].allow_peak else 0 for n in names]
    disallow_peak = [1 - p for p in allow_peak]
    
    colors = ["#22c55e", "#3b82f6", "#f59e0b", "#a855f7"]
    
    # Chart 1: Peak Permission
    ax = axes[0]
    x_pos = np.arange(len(names))
    bars_yes = ax.bar(x_pos - 0.2, allow_peak, 0.4, label="Allowed", color="#22c55e", alpha=0.8, edgecolor="white")
    bars_no = ax.bar(x_pos + 0.2, disallow_peak, 0.4, label="Blocked", color="#ef4444", alpha=0.8, edgecolor="white")
    ax.set_xticks(x_pos)
    ax.set_xticklabels(names)
    ax.set_ylabel("Charging Permission", fontweight="bold")
    ax.set_title("Peak Hours (8:00 - 18:00)", fontweight="bold")
    ax.set_ylim(0, 1.2)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["No", "Yes"])
    ax.legend()
    
    # Chart 2: Charging Rate Impact
    ax = axes[1]
    rates = []
    rate_labels = []
    for name in names:
        profile = active_profiles[name]
        if profile.allow_peak:
            # During peak: 50% slower
            peak_rate = 50  # percent of normal
            rates.append(peak_rate)
            rate_labels.append(f"{name}\n(50% during peak)")
        else:
            # Peak blocked entirely
            rates.append(0)
            rate_labels.append(f"{name}\n(0% during peak)")
    
    bars = ax.bar(range(len(rate_labels)), rates, color=["#22c55e" if r > 0 else "#ef4444" for r in rates],
                  alpha=0.8, edgecolor="white", linewidth=1.5)
    ax.set_xticks(range(len(rate_labels)))
    ax.set_xticklabels(rate_labels, fontsize=9)
    ax.set_ylabel("Charging Rate During Peak (%)", fontweight="bold")
    ax.set_title("Charging Rate Impact", fontweight="bold")
    ax.set_ylim(0, 110)
    ax.grid(axis="y", alpha=0.3)
    for bar, val in zip(bars, rates):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width()/2., val + 2,
                    f'{val}%', ha='center', va='bottom', fontsize=9, fontweight="bold")
    
    plt.tight_layout()
    filepath = os.path.join(REPORTS_DIR, "3_peak_vs_offpeak.png")
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#050816", edgecolor="none")
    logger.info(f"✓ Saved: {filepath}")
    plt.close()

# ================== VISUALIZATION 4: ENERGY DISTRIBUTION ==================

def plot_energy_distribution():
    """Show energy caps and potential revenue distribution."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle("Energy Delivery & Revenue Potential", fontsize=14, fontweight="bold")
    
    profiles = DEFAULT_PROFILES
    active_profiles = {k: v for k, v in profiles.items() if v.enable_transactions}
    
    names = list(active_profiles.keys())
    energy_caps = [active_profiles[n].max_energy_kwh for n in names]
    price_thresholds = [active_profiles[n].charge_if_price_below for n in names]
    
    colors = ["#22c55e", "#3b82f6", "#f59e0b", "#a855f7"]
    
    # Chart 1: Energy Caps
    ax = axes[0]
    bars = ax.bar(names, energy_caps, color=colors, alpha=0.8, edgecolor="white", linewidth=1.5)
    ax.set_ylabel("Energy (kWh)", fontweight="bold")
    ax.set_title("Max Energy Per Session", fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    for bar, val in zip(bars, energy_caps):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{val:.0f}kWh', ha='center', va='bottom', fontsize=10, fontweight="bold")
    
    # Chart 2: Revenue Potential (at threshold price)
    revenue_potential = [ec * pt for ec, pt in zip(energy_caps, price_thresholds)]
    ax = axes[1]
    bars = ax.bar(names, revenue_potential, color=colors, alpha=0.8, edgecolor="white", linewidth=1.5)
    ax.set_ylabel("₹ (at max price)", fontweight="bold")
    ax.set_title("Revenue Potential Per Session", fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    for bar, val in zip(bars, revenue_potential):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'₹{val:.0f}', ha='center', va='bottom', fontsize=10, fontweight="bold")
    
    plt.tight_layout()
    filepath = os.path.join(REPORTS_DIR, "4_energy_distribution.png")
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#050816", edgecolor="none")
    logger.info(f"✓ Saved: {filepath}")
    plt.close()

# ================== VISUALIZATION 5: SESSION FREQUENCY ==================

def plot_session_frequency():
    """Show expected session frequency for each profile."""
    fig, ax = plt.subplots(figsize=(12, 7))
    
    profiles = DEFAULT_PROFILES
    active_profiles = {k: v for k, v in profiles.items() if v.enable_transactions}
    
    names = list(active_profiles.keys())
    idle_mins = [active_profiles[n].idle_min for n in names]
    idle_maxs = [active_profiles[n].idle_max for n in names]
    
    # Average idle time
    avg_idle = [(idle_mins[i] + idle_maxs[i]) / 2 for i in range(len(names))]
    sessions_per_hour = [3600 / idle for idle in avg_idle]  # Simplified estimate
    
    colors = ["#22c55e", "#3b82f6", "#f59e0b", "#a855f7"]
    
    bars = ax.bar(names, sessions_per_hour, color=colors, alpha=0.8, edgecolor="white", linewidth=1.5)
    ax.set_ylabel("Sessions Per Hour (approx)", fontweight="bold", fontsize=11)
    ax.set_title("Session Frequency by Profile", fontweight="bold", fontsize=14)
    ax.grid(axis="y", alpha=0.3)
    
    for bar, val in zip(bars, sessions_per_hour):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{val:.1f}', ha='center', va='bottom', fontsize=10, fontweight="bold")
    
    plt.tight_layout()
    filepath = os.path.join(REPORTS_DIR, "5_session_frequency.png")
    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor="#050816", edgecolor="none")
    logger.info(f"✓ Saved: {filepath}")
    plt.close()

# ================== MAIN ==================

def main():
    """Generate all analysis visualizations."""
    logger.info("=" * 60)
    logger.info("EV Charging Station Simulator - Analysis Report")
    logger.info("=" * 60)
    logger.info(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(f"Output Directory: {REPORTS_DIR}/\n")
    
    try:
        logger.info("Generating visualizations...")
        plot_profile_comparison()
        plot_price_impact()
        plot_peak_vs_offpeak()
        plot_energy_distribution()
        plot_session_frequency()
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ All visualizations generated successfully!")
        logger.info("=" * 60)
        logger.info("\nReports saved to:")
        for fname in sorted(os.listdir(REPORTS_DIR)):
            logger.info(f"  • {REPORTS_DIR}/{fname}")
    except Exception as e:
        logger.error(f"✗ Error generating visualizations: {e}", exc_info=True)

if __name__ == "__main__":
    main()

"""
Peak-hour staffing (Phase 6b).

Learns each floor zone's busiest hours from this month's visitor history,
kept separate for normal weekdays and busy days (weekends and the sale day),
because they trade differently. Recommends where to put floor cover
tomorrow, always naming the past days the advice is based on.
"""

from config import (
    DELIVERY_WEEKDAY,
    DEPARTMENTS,
    PEAK_SHARE_OF_BUSIEST_HOUR,
    SALE_DAY,
    STORE_HOURS,
    TODAY_DAY,
    is_busy_day,
    month_calendar,
)
from kpi import load_footfall_data

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Below this many visitors in a zone's busiest hour, its peaks are mostly noise.
LOW_TRAFFIC_VISITORS_PER_HOUR = 3


def _hour_windows(hours):
    """Groups consecutive store hours into windows, e.g. [17, 18, 19] -> '17:00-20:00'."""
    windows, start, prev = [], None, None
    for h in sorted(hours):
        if start is None:
            start = prev = h
        elif h == prev + 1:
            prev = h
        else:
            windows.append((start, prev))
            start = prev = h
    if start is not None:
        windows.append((start, prev))
    return [f"{s}:00-{e + 1}:00" for s, e in windows]


def get_peak_hours(zone, busy_day, before_day=TODAY_DAY + 1, footfall_df=None):
    """
    Average visitors per hour for a zone on past days of one kind (busy days
    = weekends + sale day, or normal weekdays), and the peak windows: hours
    with at least PEAK_SHARE_OF_BUSIEST_HOUR of the busiest hour's visitors.
    """
    if zone not in DEPARTMENTS:
        raise ValueError(f"zone must be one of {DEPARTMENTS}")
    if footfall_df is None:
        footfall_df = load_footfall_data()

    calendar = {d["day"]: d for d in month_calendar()}
    days_with_data = set(footfall_df["day"])
    based_on = [
        d for d in range(1, before_day)
        if d in days_with_data and is_busy_day(calendar[d]) == busy_day
    ]
    history = footfall_df[(footfall_df["zone"] == zone) & footfall_df["day"].isin(based_on)]
    avg_by_hour = (
        history.groupby("hour")["visitors"].sum().reindex(STORE_HOURS, fill_value=0) / len(based_on)
    )
    busiest = float(avg_by_hour.max())
    peak_hours = [h for h, v in avg_by_hour.items() if v >= PEAK_SHARE_OF_BUSIEST_HOUR * busiest]

    return {
        # With only a few visitors an hour, which hour is "busiest" is mostly
        # chance, so the peaks are only a rough guide.
        "reliability": "rough guide (low traffic)" if busiest < LOW_TRAFFIC_VISITORS_PER_HOUR else "good",
        "zone": zone,
        "day_type": "weekend / sale day" if busy_day else "weekday",
        "based_on_days": [
            f"day {d} ({WEEKDAY_NAMES[calendar[d]['weekday']]}"
            f"{', sale day' if d == SALE_DAY else ''})"
            for d in based_on
        ],
        "avg_visitors_by_hour": {int(h): round(float(v), 1) for h, v in avg_by_hour.items()},
        "busiest_hour": int(avg_by_hour.idxmax()),
        "peak_windows": _hour_windows(peak_hours),
        "avg_visitors_per_day": round(float(avg_by_hour.sum()), 1),
    }


def get_staffing_recommendation(for_day=TODAY_DAY + 1, footfall_df=None):
    """
    Where floor cover matters most on `for_day` (default: tomorrow), per
    zone, from the history of days of the same kind. Also suggests how to
    split the floor team across zones at the busiest time, in proportion to
    each zone's visitors then.
    """
    if footfall_df is None:
        footfall_df = load_footfall_data()
    calendar = month_calendar()
    if not 1 <= for_day <= len(calendar):
        raise ValueError(f"for_day must be within the month (1-{len(calendar)})")
    target = calendar[for_day - 1]
    busy = is_busy_day(target)

    zones = [get_peak_hours(z, busy, before_day=for_day, footfall_df=footfall_df) for z in DEPARTMENTS]

    store_by_hour = {
        h: sum(z["avg_visitors_by_hour"][h] for z in zones) for h in STORE_HOURS
    }
    store_peak_hour = max(store_by_hour, key=store_by_hour.get)
    split = {
        z["zone"]: round(z["avg_visitors_by_hour"][store_peak_hour] / store_by_hour[store_peak_hour] * 100)
        for z in zones
    }

    notes = []
    if target["weekday"] == DELIVERY_WEEKDAY:
        notes.append(
            "Delivery day: receiving and putting away stock takes staff time, so schedule it "
            "before the first peak rather than during it."
        )

    return {
        "for_day": for_day,
        "date": target["date"],
        "weekday": WEEKDAY_NAMES[target["weekday"]],
        "day_type": "weekend / sale day" if busy else "weekday",
        "zones": [
            {
                "zone": z["zone"],
                "peak_windows": z["peak_windows"],
                "busiest_hour": z["busiest_hour"],
                "avg_visitors_per_day": z["avg_visitors_per_day"],
                "reliability": z["reliability"],
            }
            for z in zones
        ],
        "store_busiest_hour": store_peak_hour,
        "suggested_floor_split_at_busiest_hour_pct": split,
        "based_on_days": zones[0]["based_on_days"],
        "notes": notes,
    }


def _print_recommendation():
    rec = get_staffing_recommendation()
    print(f"\nStaffing for tomorrow: day {rec['for_day']}, {rec['weekday']} ({rec['day_type']})")
    print(f"Based on {len(rec['based_on_days'])} past {rec['day_type']}s: "
          + ", ".join(rec["based_on_days"]))
    for z in rec["zones"]:
        print(f"  {z['zone']:<11} peaks {', '.join(z['peak_windows']):<26} "
              f"busiest {z['busiest_hour']}:00, ~{z['avg_visitors_per_day']:.0f} visitors/day"
              f"  [{z['reliability']}]")
    split = ", ".join(f"{zone} {pct}%" for zone, pct in rec["suggested_floor_split_at_busiest_hour_pct"].items())
    print(f"  Store's busiest hour {rec['store_busiest_hour']}:00 -> split floor team: {split}")
    for note in rec["notes"]:
        print(f"  Note: {note}")


def _print_weekend_contrast():
    print("\nFor comparison, weekend / sale-day peaks:")
    for zone in DEPARTMENTS:
        p = get_peak_hours(zone, busy_day=True)
        print(f"  {zone:<11} peaks {', '.join(p['peak_windows']):<26} "
              f"~{p['avg_visitors_per_day']:.0f} visitors/day")


if __name__ == "__main__":
    _print_recommendation()
    _print_weekend_contrast()

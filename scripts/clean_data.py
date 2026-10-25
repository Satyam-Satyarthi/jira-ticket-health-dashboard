from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")
INPUT_PATTERN = "raw_*.csv"

SNAPSHOT_DATE = pd.Timestamp("2026-10-15")
INSTANT_SECONDS = 60
BULK_DAY_THRESHOLD = 40
BULK_MAX_DELIVERED_SHARE = 0.5

DELIVERED = {"Fixed", "Implemented", "Done", "Delivered", "Resolved"}
NOT_DELIVERED = {
    "Won't Fix", "Won't Do", "Duplicate", "Invalid", "Not A Problem", "Not A Bug",
    "Cannot Reproduce", "Abandoned", "Information Provided", "Incomplete",
    "Later", "Auto Closed", "Pending Closed",
    "Workaround", "Works for Me", "Feedback Received",
}

PRIORITY_LEVEL = {
    "Blocker": 1, "Urgent": 1,
    "Critical": 2, "High": 2,
    "Major": 3, "Normal": 3,
    "Minor": 4, "Low": 4,
    "Trivial": 5,
}
PRIORITY_GROUP = {1: "Highest", 2: "High", 3: "Medium", 4: "Low", 5: "Lowest"}


def load_raw():
    files = sorted(DATA_DIR.glob(INPUT_PATTERN))
    if not files:
        raise SystemExit(f"No files matching data/{INPUT_PATTERN}. Run from the project folder.")
    frames = [pd.read_csv(path, keep_default_na=False) for path in files]
    df = pd.concat(frames, ignore_index=True)
    df["project"] = df["key"].str.split("-").str[0]

    duplicates = int(df["key"].duplicated().sum())
    if duplicates:
        print(f"Removed {duplicates} duplicate issue keys")
        df = df.drop_duplicates(subset="key", keep="first").reset_index(drop=True)
    return df


def outcome(resolution):
    if resolution == "(unresolved)":
        return "Unresolved"
    if resolution in DELIVERED:
        return "Delivered"
    if resolution in NOT_DELIVERED:
        return "Not delivered"
    return "Other"


def clean(df):
    for col in ["created", "resolved"]:
        parsed = pd.to_datetime(df[col].replace("", pd.NA), utc=True, errors="coerce")
        df[col] = parsed.dt.tz_localize(None)

    df["assignee_id"] = df["assignee_id"].replace("", "Unassigned")
    df["components"] = df["components"].replace("", "(none)")
    df["resolution"] = df["resolution"].replace("", "(unresolved)")
    df["priority"] = df["priority"].replace("", "(none)")

    df["outcome_group"] = df["resolution"].map(outcome)

    df["priority_order"] = df["priority"].map(PRIORITY_LEVEL).fillna(99).astype(int)
    df["priority_group"] = df["priority_order"].map(PRIORITY_GROUP).fillna("Unknown")

    df["is_resolved"] = df["resolved"].notna()
    df["resolution_days"] = (df["resolved"] - df["created"]).dt.total_seconds() / 86400
    df["bad_dates"] = df["resolution_days"] < 0
    df["instant_resolution"] = df["resolution_days"].between(0, INSTANT_SECONDS / 86400)

    df["resolved_day"] = df["resolved"].dt.date
    day_group = df.groupby(["project", "resolved_day"])
    per_day = day_group["key"].transform("size")
    df["_delivered"] = df["outcome_group"] == "Delivered"
    delivered_share = df.groupby(["project", "resolved_day"])["_delivered"].transform("mean")
    df["bulk_closed"] = (
        df["is_resolved"]
        & (per_day >= BULK_DAY_THRESHOLD)
        & (delivered_share < BULK_MAX_DELIVERED_SHARE)
    )

    df["valid_for_duration"] = (
        df["is_resolved"] & ~df["bad_dates"] & ~df["instant_resolution"] & ~df["bulk_closed"]
    )

    open_age = (SNAPSHOT_DATE - df["created"]).dt.total_seconds() / 86400
    df["open_age_days"] = open_age.where(~df["is_resolved"])

    df["component_count"] = df["components"].apply(lambda s: 0 if s == "(none)" else s.count(";") + 1)
    bridge = df[["key", "components"]].copy()
    bridge["component"] = bridge["components"].str.split(";")
    bridge = bridge.explode("component")[["key", "component"]]
    return df, bridge


def report(df):
    other = sorted(df.loc[df["outcome_group"] == "Other", "resolution"].unique())
    print("Unclassified resolutions:", other or "none")
    unknown = sorted(df.loc[df["priority_order"] == 99, "priority"].unique())
    print("Priorities not in the mapping:", unknown or "none")
    print("Issues with more than one component:", int((df["component_count"] > 1).sum()))

    print("\nOutcome share per project (%)")
    print((pd.crosstab(df["project"], df["outcome_group"], normalize="index") * 100).round(1).to_string())

    print("\nPriority counts per project")
    print(pd.crosstab(df["priority"], df["project"]).join(
        df.drop_duplicates("priority").set_index("priority")["priority_group"]).to_string())

    summary = df.groupby("project").agg(
        issues=("key", "size"),
        resolved=("is_resolved", "sum"),
        instant=("instant_resolution", "sum"),
        bulk_closed=("bulk_closed", "sum"),
        bad_dates=("bad_dates", "sum"),
        valid=("valid_for_duration", "sum"),
    )
    summary["median_days_all"] = df[df["is_resolved"]].groupby("project")["resolution_days"].median().round(1)
    summary["median_days_valid"] = df[df["valid_for_duration"]].groupby("project")["resolution_days"].median().round(1)
    print("\nFlags per project")
    print(summary.to_string())

    swept = df[df["bulk_closed"]].groupby("project")["resolved_day"].nunique()
    print("\nSweep days flagged:", swept.to_dict() or "none")

    busy = (
        df[df["is_resolved"]].groupby(["project", "resolved_day"]).agg(
            issues_resolved=("key", "size"),
            median_age_days=("resolution_days", "median"),
            pct_delivered=("outcome_group", lambda s: (s == "Delivered").mean() * 100),
            flagged_as_sweep=("bulk_closed", "max"),
        ).reset_index()
        .sort_values("issues_resolved", ascending=False).groupby("project").head(5)
        .sort_values(["project", "issues_resolved"], ascending=[True, False])
    )
    print("\nBusiest resolution days")
    print(busy.to_string(index=False))


def main():
    df = load_raw()
    df, bridge = clean(df)
    report(df)

    columns = [
        "key", "project", "issue_type", "priority", "priority_order", "priority_group", "status",
        "resolution", "outcome_group", "created", "resolved", "resolution_days",
        "open_age_days", "assignee_id", "component_count", "is_resolved",
        "bad_dates", "instant_resolution", "bulk_closed", "valid_for_duration",
    ]
    df[columns].to_csv(DATA_DIR / "issues_clean.csv", index=False)
    bridge.to_csv(DATA_DIR / "issue_components.csv", index=False)
    print(f"\nSaved {len(df)} rows to data/issues_clean.csv and {len(bridge)} rows to data/issue_components.csv")


if __name__ == "__main__":
    main()
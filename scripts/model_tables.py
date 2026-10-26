from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")
MODEL_DIR = DATA_DIR / "model"

PROJECT_NAMES = {
    "KAFKA": "Apache Kafka",
    "CASSANDRA": "Apache Cassandra",
    "HADOOP": "Apache Hadoop",
}

FACT_COLUMNS = [
    "key", "project", "issue_type", "priority", "priority_group", "status",
    "resolution", "outcome_group", "created_date", "resolved_date",
    "resolution_days", "open_age_days", "assignee_id", "component_count",
    "is_resolved", "instant_resolution", "bulk_closed", "valid_for_duration",
]

CATEGORY_COLUMNS = ["issue_type", "priority", "status", "resolution", "outcome_group"]


def read_text(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def to_date_text(series):
    parsed = pd.to_datetime(series.replace("", pd.NA), errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").fillna("")


def case_only_variants(values):
    groups = {}
    for value in pd.Series(values).dropna().unique():
        groups.setdefault(value.casefold(), set()).add(value)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


def run_checks(fact, bridge, dim_component, dims):
    problems = []
    if fact["key"].duplicated().any():
        problems.append("duplicate issue keys in fact_issues")
    orphans = set(bridge["key"]) - set(fact["key"])
    if orphans:
        problems.append(f"{len(orphans)} bridge keys do not exist in fact_issues")
    lonely = set(fact["key"]) - set(bridge["key"])
    if lonely:
        problems.append(f"{len(lonely)} issues have no row in the bridge table")
    if bridge.duplicated().any():
        problems.append("duplicate (key, component_key) rows in the bridge table")
    clash = case_only_variants(dim_component["component_key"])
    if clash:
        problems.append(f"component keys that differ only in capitalisation: {list(clash.values())}")
    for column, dim in dims.items():
        missing = set(fact[column]) - set(dim[column])
        if missing:
            problems.append(f"fact_issues.{column} has values missing from its dimension: {sorted(missing)}")
    return problems


def main():
    issues = read_text(DATA_DIR / "issues_clean.csv")
    raw_bridge = read_text(DATA_DIR / "issue_components.csv")

    fact = issues.copy()
    fact["created_date"] = to_date_text(fact["created"])
    fact["resolved_date"] = to_date_text(fact["resolved"])
    fact = fact[FACT_COLUMNS]

    project_of = fact.drop_duplicates("key").set_index("key")["project"]
    bridge = raw_bridge.copy()
    bridge["project"] = bridge["key"].map(project_of).fillna("UNKNOWN")
    bridge["component_key"] = bridge["project"] + ": " + bridge["component"]

    dim_component = bridge[["component_key", "project", "component"]].drop_duplicates()
    dim_component = dim_component.sort_values(["project", "component"], key=lambda s: s.str.casefold())
    bridge_out = bridge[["key", "component_key"]]

    dim_project = pd.DataFrame({"project": sorted(fact["project"].unique())})
    dim_project["project_name"] = dim_project["project"].map(PROJECT_NAMES).fillna(dim_project["project"])

    dim_priority = issues[["priority_group", "priority_order"]].drop_duplicates()
    dim_priority = dim_priority.sort_values("priority_order", key=lambda s: s.astype(int))

    problems = run_checks(
        fact, bridge_out, dim_component,
        {"project": dim_project, "priority_group": dim_priority},
    )
    if problems:
        raise SystemExit("Nothing written:\n- " + "\n- ".join(problems))

    MODEL_DIR.mkdir(exist_ok=True)
    tables = {
        "fact_issues": fact,
        "dim_project": dim_project,
        "dim_priority": dim_priority,
        "dim_component": dim_component,
        "bridge_issue_component": bridge_out,
    }
    for name, table in tables.items():
        table.to_csv(MODEL_DIR / f"{name}.csv", index=False)
        print(f"{name}: {len(table)} rows")

    for col in CATEGORY_COLUMNS:
        found = case_only_variants(fact[col])
        if found:
            print(f"Warning: {col} has values that differ only in capitalisation: {list(found.values())}")


if __name__ == "__main__":
    main()
import os
import argparse
import csv
import hashlib
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://issues.apache.org/jira/rest/api/2/search"

FIELDS = [
    "summary", "issuetype", "priority", "status", "resolution",
    "created", "updated", "resolutiondate", "components", "assignee",
]

PAGE_SIZE = 100   
PAUSE_SECONDS = 1.0
OUT_DIR = Path("data")

HEADERS = {"User-Agent": "portfolio-project (contact: [email])"}

SALT = os.environ["JIRA_SALT"]  # random string to make pseudonyms


def pseudonymize(username):
    if not username:
        return ""
    digest = hashlib.sha256((SALT + username).encode("utf-8")).hexdigest()
    return "user_" + digest[:10]


def fetch_page(project, start_at):
    params = {
        "jql": f"project = {project} ORDER BY created ASC",
        "startAt": start_at,
        "maxResults": PAGE_SIZE,
        "fields": ",".join(FIELDS),
    }
    for attempt in range(5):
        response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=60)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429 or response.status_code >= 500:
            try:
                wait = int(response.headers.get("Retry-After", ""))
            except ValueError:
                wait = 10 * (attempt + 1)
            print(f"  Server said {response.status_code}; waiting {wait}s then retrying...")
            time.sleep(wait)
            continue
        response.raise_for_status()
    raise RuntimeError(f"Gave up on {project} at startAt={start_at}")


def name_of(value):
    return value["name"] if value else ""


def flatten(issue):
    fields = issue["fields"]
    assignee = fields.get("assignee")
    components = fields.get("components") or []
    return {
        "key": issue["key"],
        "summary": fields.get("summary") or "",
        "issue_type": name_of(fields.get("issuetype")),
        "priority": name_of(fields.get("priority")),
        "status": name_of(fields.get("status")),
        "resolution": name_of(fields.get("resolution")),
        "created": fields.get("created") or "",
        "updated": fields.get("updated") or "",
        "resolved": fields.get("resolutiondate") or "",
        "components": ";".join(c["name"] for c in components),
        "assignee_id": pseudonymize(assignee.get("name") if assignee else ""),
    }


def pull_project(project, limit=None):
    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"raw_{project}.csv"
    start_at, written, total = 0, 0, None

    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = None
        while True:
            page = fetch_page(project, start_at)
            if total is None:
                total = page["total"]
                print(f"{project}: {total} issues in Jira" + (f" (limit {limit})" if limit else ""))
            issues = page["issues"]
            if not issues:
                break
            for issue in issues:
                row = flatten(issue)
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
                    writer.writeheader()
                writer.writerow(row)
                written += 1
                if limit and written >= limit:
                    break
            start_at += len(issues)
            print(f"  {written} / {total}")
            if (limit and written >= limit) or start_at >= total:
                break
            time.sleep(PAUSE_SECONDS)

    print(f"Saved {written} rows to {out_path}\n")
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pull Apache Jira issues to CSV.")
    parser.add_argument("projects", nargs="+", help="Jira project keys, e.g. KAFKA")
    parser.add_argument("--limit", type=int, default=None, help="max issues per project")
    args = parser.parse_args(argv)
    for project in args.projects:
        pull_project(project.upper(), args.limit)


if __name__ == "__main__":
    sys.exit(main())
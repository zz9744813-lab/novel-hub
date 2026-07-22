from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.db import get_conn

def list_consistency_reports(project: str) -> list[dict[str, Any]]:
    reports = []
    with get_conn() as conn:
        cursor = conn.execute(
            """
            SELECT
                r.chapter_path,
                r.created_at,
                r.issues,
                fi.title,
                fi.chapter,
                fi.chapter_int,
                fi.status,
                fi.word_count
            FROM consistency_reports r
            LEFT JOIN file_index fi ON fi.path = r.chapter_path
            WHERE fi.project = ? OR r.chapter_path LIKE ?
            ORDER BY r.created_at DESC
            """,
            (project, f"%/{project}/%"),
        )
        for row in cursor:
            report = dict(row)
            chapter_path = report["chapter_path"]
            chapter_filename = Path(chapter_path).name
            report["chapter_filename"] = chapter_filename

            issues_raw = report.pop("issues")
            try:
                issues = json.loads(issues_raw)
            except Exception:
                issues = [{"type": "raw", "message": issues_raw}]

            if isinstance(issues, str):
                issues = [{"type": "raw", "message": issues}]
            elif isinstance(issues, dict):
                issues = [issues]
            elif not isinstance(issues, list):
                issues = [{"type": "raw", "message": str(issues)}]

            report["issues"] = issues
            issue_count = len(issues)
            report["issue_count"] = issue_count

            severity = "ok"
            if issue_count > 0:
                severity = "warning"
                for issue in issues:
                    if isinstance(issue, dict):
                        sev = issue.get("severity", "").lower()
                        typ = issue.get("type", "").lower()
                        if "error" in sev or "error" in typ or "严重" in sev or "严重" in typ:
                            severity = "error"
                            break

            report["severity"] = severity
            reports.append(report)

    return reports

def get_consistency_summary(project: str) -> dict[str, Any]:
    reports = list_consistency_reports(project)

    total_reports = len(reports)
    total_issues = 0
    ok_count = 0
    warning_count = 0
    error_count = 0
    latest_created_at = None

    for report in reports:
        total_issues += report["issue_count"]
        if report["severity"] == "ok":
            ok_count += 1
        elif report["severity"] == "warning":
            warning_count += 1
        elif report["severity"] == "error":
            error_count += 1

        if not latest_created_at or report["created_at"] > latest_created_at:
            latest_created_at = report["created_at"]

    return {
        "total_reports": total_reports,
        "total_issues": total_issues,
        "ok_count": ok_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "latest_created_at": latest_created_at,
    }

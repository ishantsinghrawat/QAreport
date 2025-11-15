# scripts/daily_cam_report.py
import os
import requests
import datetime
import smtplib
from collections import Counter
from email.message import EmailMessage
from urllib.parse import urlencode

# --- Jira config (from GitHub Secrets) ---
# JIRA_BASE must be: https://mcd-tools.atlassian.net  (NO trailing /jira)
JIRA_BASE   = os.environ["JIRA_BASE"]
JIRA_EMAIL  = os.environ["JIRA_EMAIL"]          # Jira user email
JIRA_TOKEN  = os.environ["JIRA_API_TOKEN"]      # Jira API token
PROJECT_KEY = os.environ.get("PROJECT_KEY", "CAM")

# --- Zephyr Scale config ---
Z_BASE  = "https://api.zephyrscale.smartbear.com/v2"
Z_TOKEN = os.environ["ZEPHYR_SCALE_TOKEN"]      # Zephyr Scale API token

# --- SMTP / Gmail config ---
# For Gmail:
#   SMTP_HOST = smtp.gmail.com
#   SMTP_PORT = 587
#   SMTP_USER = your Gmail address
#   SMTP_PASS = your Gmail App Password (NOT normal password)
SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]

MAIL_TO   = os.environ["MAIL_TO"]               # comma-separated
MAIL_FROM = os.environ.get("MAIL_FROM", SMTP_USER)

# --- Date window: today in UTC ---
today_utc = datetime.datetime.utcnow().date()
start_iso = f"{today_utc}T00:00:00Z"
end_iso   = f"{today_utc}T23:59:59Z"


# ---------- Helpers ----------

def jira_search(jql: str, fields: str):
    """Search Jira issues by JQL."""
    url = f"{JIRA_BASE}/rest/api/3/search"
    resp = requests.post(
        url,
        auth=(JIRA_EMAIL, JIRA_TOKEN),
        json={"jql": jql, "maxResults": 1000, "fields": fields},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("issues", [])


def zephyr_get_test_executions(project_key: str, start_iso: str, end_iso: str, limit: int = 1000):
    """
    Get Zephyr Scale test executions for CAM between start_iso and end_iso.
    If you go above `limit`, you can extend this to handle pagination.
    """
    qs = {
        "projectKey": project_key,
        "from": start_iso,
        "to": end_iso,
        "maxResults": limit,
    }
    url = f"{Z_BASE}/testexecutions?{urlencode(qs)}"
    headers = {
        "Authorization": f"Bearer {Z_TOKEN}",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    executions = data.get("executions", data)  # support both shapes
    return executions


def html_link(href: str, text: str) -> str:
    return f"<a href='{href}'>{text}</a>"


def jira_row(issue):
    f = issue["fields"]
    key = issue["key"]
    summary = (f.get("summary") or "").replace("<", "&lt;")
    priority = f.get("priority", {}).get("name", "")
    status = f.get("status", {}).get("name", "")
    assignee = f.get("assignee", {}).get("displayName", "—")
    return [
        html_link(f"{JIRA_BASE}/browse/{key}", key),
        summary,
        priority,
        status,
        assignee,
    ]


def to_table(rows, headers):
    th = "".join(f"<th>{h}</th>" for h in headers)
    tr = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        "<table border='1' cellspacing='0' cellpadding='6'>"
        f"<thead><tr>{th}</tr></thead>"
        f"<tbody>{tr}</tbody>"
        "</table>"
    )


# ---------- Jira: defects for CAM ----------

jql_defects_today = (
    f"project = {PROJECT_KEY} "
    "AND issuetype = Bug "
    "AND created >= startOfDay() "
    "ORDER BY priority DESC"
)

jql_open_blockers = (
    f"project = {PROJECT_KEY} "
    "AND issuetype = Bug "
    "AND priority in (Blocker, Highest) "
    "AND statusCategory != Done"
)

defects_today = jira_search(
    jql_defects_today,
    "key,summary,priority,status,assignee"
)
open_blockers = jira_search(
    jql_open_blockers,
    "key,summary,priority,status,assignee"
)

# ---------- Zephyr Scale: executions for CAM today ----------

executions = zephyr_get_test_executions(PROJECT_KEY, start_iso, end_iso)
status_counts = Counter((e.get("status") or "Unknown") for e in executions)

today_str = today_utc.isoformat()

html_body = f"""
<html>
  <body>
    <h2>Daily QA Report — {today_str} (Project: {PROJECT_KEY})</h2>

    <h3>Zephyr Scale — Test Executions Today</h3>
    <ul>
      <li>Total executed today: {len(executions)}</li>
      <li>Pass: {status_counts.get('Pass', 0)}</li>
      <li>Fail: {status_counts.get('Fail', 0)}</li>
      <li>Blocked: {status_counts.get('Blocked', 0)}</li>
      <li>Not Executed: {status_counts.get('Not Executed', 0)}</li>
      <li>Other/Unknown: {status_counts.get('Unknown', 0)}</li>
    </ul>

    <h3>Defects Created Today (Jira – CAM): {len(defects_today)}</h3>
    {to_table([jira_row(i) for i in defects_today],
              ["Key", "Summary", "Priority", "Status", "Assignee"])}

    <h3>Open Blockers (Priority Blocker/Highest): {len(open_blockers)}</h3>
    {to_table([jira_row(i) for i in open_blockers],
              ["Key", "Summary", "Priority", "Status", "Assignee"])}

    <p style="font-size: 12px; color: #666;">
      Generated automatically from Jira Cloud ({JIRA_BASE})
      and Zephyr Scale Cloud APIs.<br/>
      Window: {start_iso} to {end_iso} (UTC).<br/>
      Sent via Gmail SMTP as {SMTP_USER}.
    </p>
  </body>
</html>
"""

# ---------- Send email via Gmail SMTP ----------

msg = EmailMessage()
msg["Subject"] = f"Daily QA Report — {today_str} (CAM)"
msg["From"] = MAIL_FROM
msg["To"] = [addr.strip() for addr in MAIL_TO.split(",")]

msg.set_content("This email contains HTML content. Please use an HTML-capable client.")
msg.add_alternative(html_body, subtype="html")

with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
    server.starttls()
    server.login(SMTP_USER, SMTP_PASS)  # Gmail user + Gmail App Password
    server.send_message(msg)

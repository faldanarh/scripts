#!/usr/bin/env python3
"""Create a proactive case from an OHSS Jira ticket.

Usage:
    ./create_proactive_case.py OHSS-56716
    ./create_proactive_case.py OHSS-56716 --no-slack
    ./create_proactive_case.py OHSS-56716 OHSS-56717 OHSS-56718

Environment variables (or .env file):
    JIRA_PAT           Jira personal access token
    JIRA_EMAIL         Email associated with the Jira PAT
    SLACK_WEBHOOK_URL  Slack incoming webhook URL (optional)
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import ssl


JIRA_BASE_URL = "https://redhat.atlassian.net"
_cluster_id_field = None


def load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key.strip(), value.strip())


def get_config():
    load_env_file()
    jira_pat = os.environ.get("JIRA_PAT", "")
    jira_email = os.environ.get("JIRA_EMAIL", "")
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL", "")

    default_ocm = os.path.expanduser("~/.local/bin/backplane/latest/ocm")
    ocm_path = os.environ.get("OCM_PATH", default_ocm)
    if not shutil.which(ocm_path) and not os.path.isfile(ocm_path):
        ocm_path = shutil.which("ocm")
        if not ocm_path:
            print("Error: ocm binary not found. Set OCM_PATH or add ocm to PATH.", file=sys.stderr)
            sys.exit(1)

    if not jira_pat or not jira_email:
        print("Error: JIRA_PAT and JIRA_EMAIL must be set.", file=sys.stderr)
        sys.exit(1)

    return jira_pat, jira_email, slack_webhook, ocm_path


def jira_request(path, jira_email, jira_pat):
    url = f"{JIRA_BASE_URL}{path}"
    req = urllib.request.Request(url)
    credentials = base64.b64encode(f"{jira_email}:{jira_pat}".encode()).decode()
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Accept", "application/json")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx) as resp:
        return json.loads(resp.read())


def find_cluster_id_field(jira_email, jira_pat):
    global _cluster_id_field
    if _cluster_id_field:
        return _cluster_id_field

    fields = jira_request("/rest/api/3/field", jira_email, jira_pat)
    for f in fields:
        if f.get("name", "").lower() == "cluster id":
            _cluster_id_field = f["id"]
            return _cluster_id_field

    print("Error: Could not find 'Cluster ID' field in Jira.", file=sys.stderr)
    sys.exit(1)


def run_ocm(ocm_path, *args):
    result = subprocess.run(
        [ocm_path, *args],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ocm {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def parse_ocm_json(ocm_path, *args):
    raw = run_ocm(ocm_path, *args)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"ocm {' '.join(args)} returned invalid JSON: {raw[:200]}")


def extract_text_from_adf(node):
    if node is None:
        return ""
    if isinstance(node, list):
        return "".join(extract_text_from_adf(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [extract_text_from_adf(child) for child in node.get("content", [])]
        block_types = {"paragraph", "heading", "bulletList", "orderedList", "listItem", "blockquote"}
        suffix = "\n" if node.get("type") in block_types else ""
        return "".join(parts) + suffix
    return str(node)


def _extract_section(text):
    pattern = r"(?:Problem\s+Statement|Issue\s+Summary|Current\s+Summary)\s*:?\s*\n(.*?)(?:\n[A-Z][A-Za-z\s]*:\s*\n|\Z)"
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if match:
        body = match.group(1).strip()
        if body:
            return body
    return None


def summarize_description(description, max_length=500):
    if not description:
        return ""
    if isinstance(description, dict):
        text = extract_text_from_adf(description)
    else:
        text = str(description)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    section = _extract_section(text)
    if section:
        text = section
    if len(text) > max_length:
        text = text[:max_length].rsplit(" ", 1)[0] + "..."
    return text


def _text_to_adf_inline(text):
    token_re = re.compile(r'(\[([^\]]+)\]\((https?://\S+?)\))|(https?://\S+)')
    nodes = []
    last = 0
    for m in token_re.finditer(text):
        if m.start() > last:
            nodes.append({"type": "text", "text": text[last:m.start()]})
        if m.group(1):
            nodes.append({
                "type": "text",
                "text": m.group(2),
                "marks": [{"type": "link", "attrs": {"href": m.group(3)}}],
            })
        else:
            url = m.group(4)
            nodes.append({
                "type": "text",
                "text": url,
                "marks": [{"type": "link", "attrs": {"href": url}}],
            })
        last = m.end()
    if last < len(text):
        nodes.append({"type": "text", "text": text[last:]})
    return nodes


def _build_adf_body(text):
    lines = text.split("\n")
    content = []
    for i, line in enumerate(lines):
        if line:
            content.extend(_text_to_adf_inline(line))
        if i < len(lines) - 1:
            content.append({"type": "hardBreak"})
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": content}],
    }


def add_jira_comment(ticket_key, comment_text, jira_email, jira_pat):
    url = f"{JIRA_BASE_URL}/rest/api/3/issue/{ticket_key}/comment"
    body = {"body": _build_adf_body(comment_text)}
    data = json.dumps(body).encode()
    credentials = base64.b64encode(f"{jira_email}:{jira_pat}".encode()).decode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Jira comment API returned {resp.status}")
        return json.loads(resp.read())


def post_to_slack(variables, webhook_url):
    data = json.dumps(variables).encode()
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Slack webhook returned {resp.status}")
        body = resp.read().decode()
        try:
            resp_data = json.loads(body)
            return resp_data.get("url", resp_data.get("permalink", ""))
        except (json.JSONDecodeError, AttributeError):
            return body if body.startswith("http") else ""


def create_proactive_case(ticket_key, jira_email, jira_pat, ocm_path, slack_webhook, notify_slack=True):
    if not re.match(r"^OHSS-\d+$", ticket_key, re.IGNORECASE):
        print(f"Error: Expected an OHSS ticket key (e.g. OHSS-12345), got: {ticket_key}", file=sys.stderr)
        return None

    field_id = find_cluster_id_field(jira_email, jira_pat)
    issue = jira_request(f"/rest/api/3/issue/{ticket_key}?fields={field_id},description,reporter", jira_email, jira_pat)
    fields = issue.get("fields", {})
    cluster_id = fields.get(field_id)
    context = summarize_description(fields.get("description"))
    reporter = fields.get("reporter", {}).get("displayName", "Unknown")

    if not cluster_id:
        print(f"Error: No Cluster ID found in {ticket_key}", file=sys.stderr)
        return None

    if isinstance(cluster_id, dict):
        cluster_id = cluster_id.get("value", cluster_id.get("id", str(cluster_id)))
    cluster_id = str(cluster_id).strip()

    cluster_data = parse_ocm_json(ocm_path, "describe", "cluster", cluster_id, "--json")
    sub_href = cluster_data.get("subscription", {}).get("href") or cluster_data.get("href")
    if not sub_href:
        print(f"Error: No subscription href found for cluster {cluster_id}", file=sys.stderr)
        return None

    sub_json = parse_ocm_json(ocm_path, "get", sub_href)
    sub_id = sub_json.get("id") or sub_json.get("subscription", {}).get("id")
    creator_id = (
        sub_json.get("creator", {}).get("id")
        or sub_json.get("subscription", {}).get("creator", {}).get("id")
    )
    if not creator_id:
        print(f"Error: No creator ID found in subscription for cluster {cluster_id}", file=sys.stderr)
        return None

    account_json = parse_ocm_json(ocm_path, "get", f"/api/accounts_mgmt/v1/accounts/{creator_id}")

    ohss_url = f"{JIRA_BASE_URL}/browse/{ticket_key.upper()}"
    username = account_json.get("username")
    email = account_json.get("email")
    ebs_account_id = account_json.get("organization", {}).get("ebs_account_id")
    ocm_console_url = f"https://console.redhat.com/openshift/details/s/{sub_id}#support"

    slack_thread_url = ""
    slack_vars = {
        "ohss_url": ohss_url,
        "ohss_reporter": reporter,
        "context": context or "N/A",
        "ocm_console_url": ocm_console_url,
    }
    if notify_slack and slack_webhook:
        slack_thread_url = post_to_slack(slack_vars, slack_webhook)
        print("[Slack Message] - Okay")
    elif notify_slack and not slack_webhook:
        print("[Slack Message] - Skipped (SLACK_WEBHOOK_URL not set)", file=sys.stderr)

    jira_comment = (
        f"Customer details for the proactive case:\n"
        f"========================================\n"
        f"Username: {username}\n"
        f"Email: {email}\n"
        f"Account ID: {ebs_account_id}\n"
        f"OCM Console: {ocm_console_url}\n"
    )
    if slack_thread_url:
        jira_comment += f"Slack thread: {slack_thread_url}\n"
    jira_comment += f"Search {ticket_key.upper()} in [#mcs-global](https://redhat.enterprise.slack.com/archives/C01676GAH51) for the Slack thread MCS notification."
    add_jira_comment(ticket_key, jira_comment, jira_email, jira_pat)

    print("[Jira Comment] - Okay")

    result = {
        "ohss_url": ohss_url,
        "context": context,
        "reporter": reporter,
        "username": username,
        "email": email,
        "ebs_account_id": ebs_account_id,
        "ocm_console_url": ocm_console_url,
        "slack_thread_url": slack_thread_url,
    }

    return result


def main():
    parser = argparse.ArgumentParser(description="Create proactive case(s) from OHSS Jira ticket(s)")
    parser.add_argument("tickets", nargs="+", metavar="OHSS-NNNNN", help="OHSS ticket key(s)")
    parser.add_argument("--no-slack", action="store_true", help="Skip Slack notification")
    args = parser.parse_args()

    jira_pat, jira_email, slack_webhook, ocm_path = get_config()

    results = []
    for ticket_key in args.tickets:
        print(f"\n{'='*60}")
        print(f"Processing {ticket_key}")
        print(f"{'='*60}")
        try:
            result = create_proactive_case(
                ticket_key, jira_email, jira_pat, ocm_path, slack_webhook,
                notify_slack=not args.no_slack,
            )
            if result:
                results.append(result)
            else:
                print(f"Failed to create proactive case for {ticket_key}", file=sys.stderr)
        except Exception as e:
            print(f"Error processing {ticket_key}: {e}", file=sys.stderr)

    if not results:
        sys.exit(1)


if __name__ == "__main__":
    main()

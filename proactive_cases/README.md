# Proactive Case Creator

`create_proactive_case.py` builds a proactive support case from an OHSS Jira
ticket. Given one or more `OHSS-NNNNN` keys, it:

1. Reads the ticket's **Cluster ID**, description, and reporter from Jira.
2. Summarizes the ticket description (extracting the *Problem Statement* /
   *Issue Summary* / *Current Summary* section when present).
3. Looks up the cluster in OCM to resolve the subscription, creator account,
   customer username/email, and EBS account ID.
4. Posts a notification to a Slack incoming webhook (can be optional).
5. Adds a comment back to the Jira ticket with the collected customer details,
   the OCM console link, and the Slack thread reference.

## Requirements

### Runtime

- **Python 3** (3.6+). Only the standard library is used — there are no `pip` 
  packages to install.
- **`ocm` CLI** must be installed and authenticated (`ocm login`). By default
  the script looks for it at `~/.local/bin/backplane/latest/ocm`, then falls
  back to `ocm` on your `PATH`. Override with the `OCM_PATH` environment
  variable.

### Credentials & configuration
Set these as environment variables in your shell:

| Variable | Required | Description |
|----------|----------|-------------|
| `JIRA_PAT` | Yes | Jira personal access token |
| `JIRA_EMAIL` | Yes | Email associated with the Jira PAT |
| `SLACK_WEBHOOK_URL` | Yes | Slack incoming webhook URL. If unset, Slack notification is skipped. |
| `OCM_PATH` | No | Path to the `ocm` binary (defaults as described above). |

Export them in your shell (add to `~/.bashrc`, `~/.zshrc`, etc. to persist):

```bash
export JIRA_PAT=<your_personal_access_token>
export JIRA_EMAIL=<you@redhat.com>
export SLACK_WEBHOOK_URL="https://hooks.slack.com/triggers/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"
```

> A `.env` file placed next to the script is also supported as a fallback, but
> variables already set in your environment always take precedence.

### Access
- The Jira account must be able to read OHSS issues (including the *Cluster ID*
  custom field) and add comments.
- The `ocm` session must be authorized to describe clusters and read
  subscriptions and accounts.

## Usage

```bash
# Single ticket
./create_proactive_case.py OHSS-56716

# Skip the Slack notification
./create_proactive_case.py OHSS-56716 --no-slack

# Multiple tickets in one run
./create_proactive_case.py OHSS-56716 OHSS-56717 OHSS-56718
```

Each ticket key must match the form `OHSS-NNNNN`.

## Output

For every processed ticket the script prints progress and, on success,
`[Slack Message] - Okay` and `[Jira Comment] - Okay`. Errors for an
individual ticket are written to stderr and processing continues with the
next ticket. The script exits with status `1` if no cases were created.

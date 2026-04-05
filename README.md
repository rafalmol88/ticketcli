# ticketcli

A multi-backend ticket CLI with pluggable handlers for Jira Cloud, Jira Server, GitHub Issues, ClickUp, Azure DevOps Boards, and a local mock backend.

## Install

From the project root:

```bash
pip install -e .
```

This installs the `ticketcli` command.

## Config files

Stored under `~/.ticketcli/`:

| File | Purpose |
|------|---------|
| `config.json` | Editor preference and default target behaviour |
| `targets.json` | Target definitions (one entry per board / project) |
| `user_mapping.conf` | Global `human_alias=system_username` pairs |
| `user_mapping_<target>.conf` | Per-target overrides (loaded on top of the global file) |

### `config.json` example

```json
{
  "editor": "nano",
  "default_target": "myproject",
  "require_explicit_target": false
}
```

## Targets

Each entry in `targets.json` is a named target.  The `ticket_system` field selects the backend.

### Jira Cloud

```json
"myproject": {
  "ticket_system": "jira_cloud",
  "project_base": "PROJ",
  "base_url": "https://your-org.atlassian.net",
  "project_id": "10001",
  "issue_type_name": "Task",
  "list_issues_max_results": 100,
  "auth": {
    "email_env": "JIRA_EMAIL",
    "token_env": "JIRA_TOKEN"
  }
}
```

### Jira Server / Data Center

```json
"legacyjira": {
  "ticket_system": "jira_server",
  "project_base": "OPS",
  "base_url": "https://jira.example.internal",
  "project_key": "OPS",
  "issue_type_name": "Task",
  "auth": {
    "username_env": "JIRA_SERVER_USER",
    "password_env": "JIRA_SERVER_PASSWORD"
  }
}
```

### Azure DevOps

```json
"myboard": {
  "ticket_system": "azuredevops",
  "organization": "my-org",
  "project": "MyProject",
  "project_base": "MYBOARD",
  "work_item_type": "Task",
  "list_issues_max_results": 200,
  "auth": {
    "pat_env": "AZURE_DEVOPS_PAT"
  }
}
```

> **Auth:** create a Personal Access Token in Azure DevOps (User Settings → Personal Access Tokens) with at least *Work Items (Read & Write)* scope and export it:
> ```bash
> export AZURE_DEVOPS_PAT="<your-pat>"
> ```
>
> `base_url` defaults to `https://dev.azure.com/{organization}` and can be overridden for on-premises Azure DevOps Server instances.

### ClickUp

```json
"mylist": {
  "ticket_system": "clickup",
  "project_base": "CU",
  "list_id": "123456789012",
  "team_id": "12345678901",
  "auth": {
    "token_env": "CLICKUP_TOKEN"
  }
}
```

### GitHub Issues

```json
"myrepo": {
  "ticket_system": "github",
  "project_base": "GH",
  "owner": "my-org",
  "repo": "my-repo",
  "list_issues_max_results": 100,
  "auth": {
    "token_env": "GITHUB_TOKEN"
  }
}
```

> **Auth:** create a [fine-grained personal access token](https://github.com/settings/tokens?type=beta) with at least *Issues (Read & Write)* permission on the target repository and export it:
> ```bash
> export GITHUB_TOKEN="ghp_..."
> ```
>
> `base_url` defaults to `https://api.github.com` and can be overridden for GitHub Enterprise Server instances.

### Local mock (for testing)

```json
"sandbox": {
  "ticket_system": "localmock",
  "project_base": "SBX",
  "me": "alice"
}
```

## Commands

```bash
# Create an issue (open editor for description)
ticketcli add -s "Broken login" --edit
ticketcli add -s "UI glitch" -l bug frontend --components Portal

# Edit summary or description of an issue
ticketcli edit -i 145 --edit
ticketcli edit -i 145 -s "New title"
ticketcli edit -i 145 -l bug critical --components Backend API
ticketcli edit -i 145 --pin     # interactively pin/unpin comments

# Assign / reassign / unassign
ticketcli assign -i 145 -a alice
ticketcli assign -i 145          # interactive selection
ticketcli assign -i 145 --unassign

# Add a comment
ticketcli comment -i 145 -m "fixed in v2.3"
ticketcli comment -i 145         # opens editor

# Show full issue details (includes labels, components, worklogs, links, comments, attachments)
# Comments: always pinned (marked [pinned]), plus the last 5 non-pinned; dates are human-readable
ticketcli show -i 145
ticketcli show -i 145 -f json    # output as JSON

# Download attachments interactively
ticketcli attachments -i 145 -o ./downloads

# Upload an attachment
ticketcli upload -i 145 ./screenshot.png

# Delete an attachment (use the ID shown by 'ticketcli show')
ticketcli delete-attachment -i 145 abc-123-uuid

# Change issue status
ticketcli status -i 145 "In Progress"

# Close an issue (shortcut for status Done)
ticketcli close -i 145
ticketcli close -i 145 -s Closed   # use a different close status

# List issues
ticketcli list                   # unresolved, assigned to me
ticketcli list -c                # created by me
ticketcli list -a                # all unresolved in the project/board
ticketcli list -f json           # output as JSON

# Migrate an issue between targets (e.g. localmock → Jira)
ticketcli migrate --source sandbox --dest myproject -i 1 --comments --attachments
ticketcli migrate --source sandbox --dest myboard -i 1 --comments --close-source
ticketcli migrate --source sandbox --dest myproject --all --comments   # batch migrate
ticketcli migrate --source sandbox --dest myproject -i 1 --dry-run     # preview only

# Manage the default target
ticketcli target --default myproject --allow-default
ticketcli target --clear-default --require-target

# Show all configured targets
ticketcli targets

# Generate a cycle report
ticketcli show-report                   # current cycle
ticketcli show-report -p -1             # previous cycle
ticketcli show-report -p -2 -r          # two cycles ago, with roster tables

# Clear completion cache
ticketcli cache clear
```

## Migrating issues

The `migrate` command copies an issue from one target to another:

```bash
ticketcli migrate --source sandbox --dest myproject -i SBX-1 --comments --attachments
ticketcli migrate --source sandbox --dest myproject --all --comments  # batch
ticketcli migrate --source sandbox --dest myproject -i SBX-1 --dry-run
```

| Flag | Purpose |
|------|---------|
| `--source` | Source target name |
| `--dest` | Destination target name |
| `-i` | Issue key or number in the source target |
| `-a / --all` | Migrate all unresolved issues from the source |
| `--comments` | Also copy comments (prefixed with original author and date) |
| `--attachments` | Also download and re-upload attachments to the destination |
| `--no-assignee` | Don't carry over the assignee |
| `--close-source` | Attempt to mark the source issue as Done after migration |
| `--dry-run` | Preview what would be created without making any API calls |

Provide either `-i` or `--all` (not both).  Assignees are matched through human-readable user mapping aliases — the source system ID is reverse-looked up to find the human name, which is then mapped to the destination system ID.  This is especially useful for moving draft issues from `localmock` to a real backend once they're ready.

## Labels and components

Jira Cloud and Jira Server targets support labels and components on create and edit:

```bash
ticketcli add -t myproject -s "Fix auth" -l security urgent --components Auth API
ticketcli edit -t myproject -i PROJ-42 -l security --components Auth
```

Labels and components are shown in `ticketcli show` output and are carried over by `ticketcli migrate`.

## Attachments

All real backends (Jira Cloud, Jira Server, Azure DevOps, ClickUp) and the local mock support attachment upload, download, and deletion:

```bash
# Upload a file
ticketcli upload -i PROJ-42 ./report.pdf

# Show issue — attachment IDs and copy-paste download commands are included
ticketcli show -i PROJ-42

# Download interactively (pick which attachments)
ticketcli attachments -i PROJ-42 -o ./downloads

# Delete by attachment ID
ticketcli delete-attachment -i PROJ-42 abc-123-uuid
```

When migrating with `--attachments`, files are downloaded from the source and re-uploaded to the destination automatically.

## Status transitions

Change an issue's status (workflow transition) on any backend:

```bash
ticketcli status -i PROJ-42 "In Progress"
ticketcli status -i PROJ-42 "Code Review"
```

For Jira, the CLI looks up available transitions and matches by name.  For Azure DevOps, it sets `System.State` directly.

### Quick close

```bash
ticketcli close -i PROJ-42               # transitions to "Done"
ticketcli close -i PROJ-42 -s Closed     # use a different target status
```

## Pinned comments

Jira Cloud/Server supports pinning comments (via comment properties).  ClickUp uses the 📌 pushpin emoji reaction.  GitHub uses native issue-comment pinning (via GraphQL API).  The local mock backend persists pin state in its JSON file.

Pinned comments are always shown in `ticketcli show` output (regardless of the 5-comment limit) and tagged with **`[pinned]`**.

### Pin / unpin interactively

```bash
ticketcli edit -i PROJ-42 --pin
```

This shows the last 5 comments plus any currently pinned comments, lets you toggle pin/unpin by number, and repeats until you press Enter to finish:

```
Comments for PROJ-42:
  1.    [Mon, Mar 10 at 14:22] alice: Investigated root cause, it's the auth m...
  2. 📌 [Wed, Mar 12 at 09:05] bob: Fix merged to main — verified in staging
  3.    [Fri, Mar 14 at 16:30] alice: Closing after 48h with no regressions

Toggle pin by number (comma-separated), or Enter to finish:
```

## Cycle reports

The `show-report` command generates a team-facing report for a configurable time window ("cycle").  It queries the backend directly — no cache is used.

```bash
ticketcli show-report                   # current cycle
ticketcli show-report -p -1             # previous cycle (-1, -2, …)
ticketcli show-report -r                # include roster tables
ticketcli show-report -t myproject -p -1 -r
```

### Cycle configuration

On first run the CLI walks you through a one-time setup saved under the target in `~/.ticketcli/targets.json`:

```
Cycle duration in Jira notation (e.g. 1w, 4d, 8h) [1w]: 1w
Cycle start day (e.g. Monday) [Monday]: Monday
Cycle start hour (0-23) [9]: 9
```

Duration uses **Jira notation**: `1w` = 5 days × 8 h, `2d` = 16 h, `8h` = 8 h. Combinations like `1w2d4h` are supported.

The `targets.json` entry for the target gains a `cycle` block:

```json
"myproject": {
  "ticket_system": "jira_cloud",
  ...
  "cycle": {
    "duration": "1w",
    "start_day": "Monday",
    "start_hour": 9
  }
}
```

### Report sections

Each section shows `{key} | {assignee} | {summary}`:

| Section | What it shows |
|---------|---------------|
| **Closed Tickets** | Issues whose status moved to Done/Closed/Resolved during the cycle |
| **Reassigned Tickets** | Issues whose assignee changed, with old → new |
| **Links Added** | New issue links created during the cycle |
| **Pinned Comments (Progress)** | Issues with pinned comments — the latest pinned comment is shown as a progress note |
| **Activity** | Worklogs logged and status changes made during the cycle |

### Roster tables (`--roster` / `-r`)

Appended after the main sections:

```
──────────────────────────────────────────────────────────
  Roster: Worklogs by Ticket
──────────────────────────────────────────────────────────

  PROJ-42 — Fix broken login page
    alice                             3d 4h
    bob                               1d
    TOTAL                             4d 4h

──────────────────────────────────────────────────────────
  Roster: Total Hours by Person
──────────────────────────────────────────────────────────
  alice                               5d 2h
  bob                                 2d 4h
  GRAND TOTAL                         7d 6h
```

## JSON output

`list` and `show` accept `-f json` for machine-readable output:

```bash
ticketcli list -f json
ticketcli show -i PROJ-42 -f json
```

Pipe to `jq` for further processing:

```bash
ticketcli list -a -f json | jq '.[].key'
```

## Cache management

Autocomplete data (issues, users, labels, components, targets) is cached under `~/.cache/ticketcli/` with configurable TTLs (24–48 h).  The completion cache always stores **all unresolved** issues for a target so every issue key is available for tab-completion regardless of the command flags.

```bash
ticketcli cache clear   # remove all cached completion data
```

## Notes

- If a target has `project_base` set, a short reference like `-i 145` expands to `PROJECT_BASE-145`.
- `ticketcli show` displays issue **links**, limits comments to the **last 5** (plus all pinned), renders dates as human-readable strings (e.g. `Mon, Apr 7 at 09:30`), and prefixes pinned comments with **`[pinned]`**.
- If an assignee is missing or unknown the CLI shows available mapped users and supports fuzzy interactive selection.
- `ticketcli list` shows unresolved issues assigned to the authenticated user; `-c` switches to issues created by you; `-a` shows all unresolved issues in the project (use with care on large boards).
- Jira `--all` queries are scoped to the configured `project_key` / `project_base` so they don't flood results from other projects.
- Azure DevOps descriptions are stored as HTML; the CLI auto-converts between plain text and HTML on read/write.
- Azure DevOps work item type defaults to `Task`; set `"work_item_type"` in the target config to use `Bug`, `User Story`, etc.
- Tab-completion for issue keys shows summaries as hints while typing (zsh/fish); the hint is stripped once the typed text matches a full key. Flags are hidden while completing values so only real candidates appear.
- Migration uses reverse user-mapping: source system ID → human alias → destination system ID.
- GitHub Issues only have two states (`open` / `closed`).  Common close-status names (`Done`, `Resolved`, `Complete`, etc.) are automatically mapped to `closed`.  Worklogs are marked as **not available** since GitHub has no built-in time-tracking.
- GitHub Issues do not support first-class file attachments; `upload`, `download`, and `delete-attachment` are not available.  Embed files via markdown links in the issue body instead.
- When you add a comment, upload, or download an attachment on an issue whose status is idle (Open, To Do, Planned, Backlog, etc.), the CLI suggests transitioning it to "In Progress".  Press **Enter** to accept or **n** to skip.  Your preferred in-progress status is cached per target (30-day TTL) so you are only asked to pick once.

## Shell completion

Tab-completion is powered by [`argcomplete`](https://github.com/kislyuk/argcomplete).  The following arguments support completion:

| Argument | What completes |
|----------|---------------|
| `-t / --target` | Configured target names |
| `-i / --issue` | Open issue keys (with summary hint in zsh/fish) |
| `-a / --assignee` | Mapped users / assignable users |
| `-l / --labels` | Labels seen on recent issues |
| `--components` | Components seen on recent issues |
| `--source / --dest` | Configured target names |

Completion data is cached under `~/.cache/ticketcli/` and refreshed automatically after 24 h, or on demand with `ticketcli cache clear`.

---

### bash

#### One-time setup (recommended)

Add the following line to your `~/.bashrc`:

```bash
eval "$(register-python-argcomplete ticketcli)"
```

Then reload the shell:

```bash
source ~/.bashrc
```

#### Alternative: global activation

```bash
activate-global-python-argcomplete
```

This registers all `argcomplete`-aware scripts at once.  May require `sudo` depending on your Python installation.

#### bash limitations

bash's completion mechanism (`COMPREPLY`) supports only **plain strings** — it cannot display descriptions next to candidates.  As a result:

- **Issue summaries are not visible** while completing `-i / --issue`.  You see only the issue key (e.g. `PROJ-42`).  Use `ticketcli list` to find the right key, then tab-complete it.
- Flags (`--target`, `--format`, …) are **not** shown while completing a value argument.  Only valid candidates (issue keys, user names, etc.) appear.

---

### zsh

zsh supports rich completions including per-candidate descriptions, so the experience is noticeably better than bash.

#### ⚠️ Critical: placement in `~/.zshrc`

The generated completion script calls `compdef`, which only works **after** `compinit` has been called.  If you place the `eval` line before `compinit` (or if `compinit` is never called), zsh silently falls back to its default file completion — you will see files being listed instead of ticket options.

**Correct order in `~/.zshrc`:**

```zsh
# 1. compinit must come first (it is already present in most setups / Oh My Zsh)
autoload -Uz compinit && compinit

# 2. Register ticketcli completion AFTER compinit
eval "$(register-python-argcomplete ticketcli)"
```

If you use **Oh My Zsh**, `compinit` is called internally when Oh My Zsh is sourced, so put the `eval` line **after** the `source $ZSH/oh-my-zsh.sh` line:

```zsh
source $ZSH/oh-my-zsh.sh   # compinit is called inside here

# After Oh My Zsh:
eval "$(register-python-argcomplete ticketcli)"
```

#### Alternative: dedicated completion file (most reliable)

Instead of `eval`, write the script to a file that zsh loads automatically:

```zsh
# Run once in your terminal:
mkdir -p ~/.zsh/completions
register-python-argcomplete --shell zsh ticketcli > ~/.zsh/completions/_ticketcli
```

Then add the completions directory to `$fpath` **before** `compinit` in `~/.zshrc`:

```zsh
fpath=(~/.zsh/completions $fpath)
autoload -Uz compinit && compinit
```

Re-run this command any time you update ticketcli or argcomplete.

#### zsh behaviour

- **Issue keys show summaries** next to each candidate:
  ```
  PROJ-42  Fix broken login page
  PROJ-43  Migrate database schema
  ```
- **Assignees show display names** next to each username.
- Once you have typed enough characters to match a single key exactly, the description is stripped so the completed value is the bare key (not `PROJ-42 Fix broken login page`).

---

### fish

fish shell completion works similarly to zsh — descriptions are shown for issue keys and assignees.

```fish
# Add to ~/.config/fish/completions/ticketcli.fish
register-python-argcomplete --shell fish ticketcli | source
```

---

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| **zsh completes files instead of ticket options** | The `eval` line is before `compinit`. Move it after `compinit` / `source $ZSH/oh-my-zsh.sh`, then `source ~/.zshrc` |
| Nothing completes | Confirm `argcomplete` is installed: `pip show argcomplete` |
| Completions are stale | Run `ticketcli cache clear` |
| Only `--help` / flags appear | Ensure `eval "$(register-python-argcomplete ticketcli)"` is in your rc file and you have reloaded the shell |
| Descriptions not shown (bash) | This is a bash limitation — switch to zsh or fish for richer completions |
| `activate-global-python-argcomplete` not found | Install argcomplete: `pip install argcomplete` |


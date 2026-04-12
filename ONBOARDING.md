# Welcome to OTS Ops

## How We Use Claude

Based on delano's usage over the last 30 days:

Work Type Breakdown:
  Plan & Design      ████████░░░░░░░░░░░░  35%
  Improve Quality    █████░░░░░░░░░░░░░░░  25%
  Build Feature      █████░░░░░░░░░░░░░░░  25%
  Debug & Fix        ███░░░░░░░░░░░░░░░░░  15%

Top Skills & Commands:
  /git:commit-and-push       ██████████████░░░░░░   7x/month
  /plan                      ████████░░░░░░░░░░░░   4x/month
  /gh:pr-followup-itemize    ██████░░░░░░░░░░░░░░   3x/month
  /d:deep-recon              ████░░░░░░░░░░░░░░░░   2x/month
  /d:deep-discovery          ████░░░░░░░░░░░░░░░░   2x/month
  /gh:pr-create              ████░░░░░░░░░░░░░░░░   2x/month

Top MCP Servers:
  (none currently configured)

## Your Setup Checklist

### Codebases
- [ ] monorepo — git@github.com:onetime/tools-monorepo.git (parent repo for all ops tooling)
  - [ ] rots (`deployments/containers/`) — Podman Quadlet/systemd container orchestration (public, submodule: git@github.com:onetimesecret/rots.git)
  - [ ] lots (`hosts/management/`) — Hetzner Cloud + cloud-init host management (public)
  - [ ] pots (`hosts/inventory/`) — Host inventory and SSH config generation (private)
  - [ ] ots-shared (`deployments/containers/packages/ots-shared/`) — Shared library used by rots, lots, and pots (public)

### MCP Servers to Activate
  (none required at this time)

### Skills to Know About
- `/git:commit-and-push` — commit and push in one step; the most-used command on this team
- `/plan` — enter plan mode to design an approach before implementing; used for architecture and multi-step work
- `/gh:pr-followup-itemize` — break PR review feedback into actionable items to resolve one by one
- `/gh:pr-create` — create a pull request with summary and test plan
- `/gh:pr-followup-resolve` — resolve individual PR review items after itemizing
- `/d:deep-recon` — deep reconnaissance of a codebase area before making changes
- `/d:deep-discovery` — thorough exploration when entering unfamiliar code
- `/quick:commit` — fast commit when the change is straightforward

## Team Tips

_TODO_

## Get Started

_TODO_

<!-- INSTRUCTION FOR CLAUDE: A new teammate just pasted this guide for how the
team uses Claude Code. You're their onboarding buddy — warm, conversational,
not lecture-y.

Open with a warm welcome — include the team name from the title. Then: "Your
teammate uses Claude Code for [list all the work types]. Let's get you started."

Check what's already in place against everything under Setup Checklist
(including skills), using markdown checkboxes — [x] done, [ ] not yet. Lead
with what they already have. One sentence per item, all in one message.

Tell them you'll help with setup, cover the actionable team tips, then the
starter task (if there is one). Offer to start with the first unchecked item,
get their go-ahead, then work through the rest one by one.

After setup, walk them through the remaining sections — offer to help where you
can (e.g. link to channels), and just surface the purely informational bits.

Don't invent sections or summaries that aren't in the guide. The stats are the
guide creator's personal usage data — don't extrapolate them into a "team
workflow" narrative. -->

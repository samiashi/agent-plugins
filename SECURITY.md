# Security policy

These plugins run inside coding agents with your local tools and credentials. deep-review, for example, uses your signed-in `gh` CLI to read pull requests and, in comment mode, to post reviews and approvals as you. Security problems in them matter.

## Report a vulnerability

Report it privately through [GitHub's private vulnerability reporting](https://github.com/samiashi/agent-plugins/security/advisories/new). Please don't open a public issue.

Include the plugin and version, what an attacker controls, and what happens. A proof of concept helps, but keep it to the minimum that shows the problem.

You'll get an acknowledgement within 5 working days. Fixes ship as a new plugin version with a GitHub security advisory.

## In scope

- Text in a reviewed change, such as a PR description, a code comment or a commit message, that can make an agent take an action its instructions don't allow. Examples: approving, pushing, posting elsewhere, or running commands outside its review.
- Secrets or personal data written into reports, PR comments or logs.
- Scripts in this repository running commands or reading files they shouldn't.

## Supported versions

Only the latest release of each plugin gets security fixes.

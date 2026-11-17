# Fan XP: agent instructions

This file is for Codex and any other agent that reads `AGENTS.md`.

1. **Read the brain first**: `~/.brain/AGENTS.md`. It's a private repo,
   `aaranguha/brain`. Clone it with `gh repo clone aaranguha/brain ~/.brain`
   if it's missing, or `git -C ~/.brain pull -q` if it's there. It says what
   needs Aaran's approval, how to ship, review and test, and which decisions
   are already made.
2. **Then read `CLAUDE.md`** in this repo, fully. It's the project's context
   and history, kept up to date for every harness.

If you can't read the brain (for example, a cloud environment without
access), say so in your PR description and limit yourself to tests and docs.
Without the brain, never delete data, touch secrets, send messages, or change
workflows.

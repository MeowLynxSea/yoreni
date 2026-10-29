---
name: clawhub
description: Search and install agent skills from ClawHub, the public skill registry.
homepage: https://clawhub.ai
metadata: {"yoreni":{"emoji":"🦞"}}
---

# ClawHub

Public skill registry for AI agents. Search by natural language (vector search).

## When to use

Use this skill when the user asks any of:
- "find a skill for …"
- "search for skills"
- "install a skill"
- "what skills are available?"
- "update my skills"

## Search

```bash
npx --yes clawhub@latest search "web scraping" --limit 5
```

## Install

```bash
npx --yes clawhub@latest install <slug> --workdir ~/.yoreni/workspace
```

Replace `<slug>` with the skill name from search results. This places the skill into `~/.yoreni/workspace/skills/`, where yoreni loads workspace skills from. Always include `--workdir`.

## Update

```bash
npx --yes clawhub@latest update --all --workdir ~/.yoreni/workspace
```

## List installed

```bash
npx --yes clawhub@latest list --workdir ~/.yoreni/workspace
```

## Notes

- Requires Node.js (`npx` comes with it).
- No API key needed for search and install.
- Login (`npx --yes clawhub@latest login`) is only required for publishing.
- `--workdir ~/.yoreni/workspace` is critical — without it, skills install to the current directory instead of the yoreni workspace.
- After install, remind the user to start a new session to load the skill.

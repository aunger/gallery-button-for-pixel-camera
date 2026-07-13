---
paths:
  - "**/*.{yaml,yml}"
---

# GitHub Actions version reminders

When editing YAML files,
keep these priorities in mind:

1. Be consistent with Action versions across this project.
   Match the versions used by other workflows and *particularly* this same workflow.
2. If introducing an Action to this project,
   start with the current version (not just the most recent version you're aware of).
3. Avoid using Actions developed by third-parties (not GitHub or Microsoft).
   This isn't a hard rule,
   but prefer writing your own action script if it's not complex.

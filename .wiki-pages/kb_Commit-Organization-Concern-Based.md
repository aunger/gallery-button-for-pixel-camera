A "concern-based commit" approach means each commit represents a logically cohesive unit of work — a single concern, feature, fix, or refactoring — rather than arbitrary groupings by file or time.

## Example: Seven-Phase E2E Test Implementation

When implementing a large feature like E2E visual testing, work spans multiple phases. Each phase should be **one commit per PR** if possible, but if grouped into fewer commits, they should be organized by **concern**, not by file count or "whatever happened in a 2-hour window."

### Concern Examples (from this project)

| Concern | Commits | Rationale |
|---------|---------|-----------|
| Test infrastructure setup | 1 commit | Testgallery module, test harness, JUnit XML parsing all work together |
| Mock camera | 1 commit | Mock green-feed view + MediaStore insertion + ready signal |
| Visual assertion library | 1 commit | Shape templates (square, circle, squircle), matcher logic, IoU classifier |
| E2E fixture helpers | 1 commit | All fixture methods (capturePhoto, tap, lock, launch, pause) are interdependent |
| Test class implementation | 1 commit | Individual test methods are one concern: "does the test suite run?" |
| CI wiring | 1 commit | All CI glue (JUnit runner config, environment variables, Gradle plugin) |
| Commit organization docs | 1 commit | Documenting the above structure for future reference |

### Deviations & Acceptable Variations

**Grouping skeleton-level additions:** If multiple new classes/modules are **purely structural** (no logic, just constructor scaffolding), it's acceptable to group them in one commit:
- Example: `ShapeTemplate`, `ShapeMatcher`, `ImageUtils` all empty shells for 1st commit
- Then populate with logic in follow-up commits
- Rationale: These don't work independently; showing them together clarifies intent

**Test fixture grouping:** All fixture helper methods can go in one commit because they're defined in the same class and serve the same purpose (supporting tests).

## Code Review Benefit

Concern-based commits make code review **much easier**:
- Reviewer can understand each commit's purpose at a glance
- No need to read 10 files to understand why each file changed
- Easy to spot scope creep ("this commit is supposed to add shape matching, but it's also refactoring logging?")
- Commit history is readable without full PR descriptions

## Enforcement

Reference the discipline in `agents/code_edit.md` (or equivalent coding standards) when making commits. A consistent policy helps all contributors understand expectations.

---

**Source:** [PR #174 — E2E Commit Organization Audit](https://github.com/aunger/gallery-button-for-pixel-camera/pull/174)

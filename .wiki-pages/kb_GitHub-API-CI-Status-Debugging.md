GitHub's CI status APIs can be confusing because there are two different APIs from different eras, and they represent pending states differently.

## The Two Status APIs

### Legacy Status API (`/repos/{owner}/{repo}/statuses/{sha}`)
Returns the old-style "Statuses" — typically from Travis CI, Codecov, custom systems. Modern GitHub Actions **does not use this API**.

**Key quirk:** When no legacy statuses exist, `get_status` returns:
```json
{
  "state": "pending",
  "total_count": 0,
  "description": null
}
```

This `pending` state is a **default artifact**, not an indication that checks are actually running. It means "no status information available" rather than "checks are in progress."

### Checks API (`/repos/{owner}/{repo}/commits/{sha}/check-runs`)
Used by modern GitHub Actions. Returns individual check-run objects with detailed status and conclusion fields.

**Why this is correct:**
- Each check run has explicit `status`: `queued`, `in_progress`, or `completed`
- When `completed`, the `conclusion` field indicates the result: `success`, `failure`, `neutral`, `cancelled`, etc.
- No ambiguous default states

## Distinguishing Pending States

When polling CI status, you must distinguish:

1. **Pending-active** (checks are running): `check-runs` exist with `status != "completed"`
2. **Pending-inactive** (no checks at all): `check-runs` is empty array
3. **Pending-legacy** (legacy statuses only): Use legacy API, but this is rare in modern repos

Modern GitHub workflows should **always use the Checks API** (`get_check_runs`). If you're seeing only the legacy API, you're looking at the wrong endpoint.

## Implementation Pattern

```bash
# Correct: Get modern GitHub Actions check runs
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/OWNER/REPO/commits/SHA/check-runs" \
  | jq '.check_runs[] | {name, status, conclusion}'

# Avoid: Legacy status API (rarely useful for GitHub Actions)
# curl -s "https://api.github.com/repos/OWNER/REPO/statuses/SHA"
```

---

**Source:** [PR #146 comment — CI Status API Debugging](https://github.com/aunger/gallery-button-for-pixel-camera/pull/146#issuecomment-4508569279)

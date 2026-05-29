# CI Architecture

## Non-Failing CI Design

The CI pipeline in this repository is intentionally configured to **not fail the overall build** when tests fail. This is a deliberate temporary decision while test fixes are landing incrementally.

### Why Non-Failing?

During periods of active test stabilization:
- Test failures should not block check-ins or code merges
- All contributors should be able to land fixes incrementally
- Failed tests are automatically filed as GitHub issues for tracking and prioritization
- The pipeline continues to produce artifacts (APKs, screenshots, logcat) to support debugging

This allows the development velocity to remain high while test suites are being stabilized.

### How It Works

The build pipeline uses `continue-on-error: true` on test steps that can fail:

1. **Unit tests and compile steps** run normally and fail the build if they encounter errors (fast feedback loop)
2. **E2E tests** (PixelCameraOverlayE2ETest, GalleryButtonVisualE2ETest) use `continue-on-error: true`:
   - Tests run to completion even if they fail
   - Failures are captured in artifacts (screenshots, logcat, test reports)
   - The build does not fail or block the PR
3. **Issue filing** (`.github/workflows/build.yml` `file-issues` job):
   - Automatically files issues for each failed test
   - Tracks recurring failures by re-opening closed issues
   - Provides visibility into test health

### When Returning to Failing CI

This non-failing architecture should be **removed only when**:
- The test suite is stable (all critical tests passing consistently)
- The team has decided fixes have landed and are ready to enforce CI gates
- A deliberate decision is made to restore strict CI checks

**Do not "fix" this back to failing CI without explicit team consensus.**

To restore failing CI:
1. Remove `continue-on-error: true` from the E2E test steps in `.github/workflows/build.yml`
2. Update this document to reflect the change and the date it took effect
3. Ensure all tests are passing before merging (the new failures will become blockers)

### Related Files

- `.github/workflows/build.yml` — Contains the actual CI configuration
- `scripts/file_test_failure_issues.py` — Auto-files issues for failed tests
- `.github/workflows/archive-stale-test-failures.yml` — Archives test failure issues when tests pass

### For Future Contributors and Agents

If you are reviewing CI behavior or making changes:
- This non-failing design is intentional and temporary
- Do not re-introduce failing gates without consulting the team
- If you believe tests are stable enough to fail CI, raise the question in an issue first
- Understand that test failures should result in issues, not build failures, during this phase

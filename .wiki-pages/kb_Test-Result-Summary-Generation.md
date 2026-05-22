Generating human-readable test result summaries for CI workflows requires parsing JUnit XML output and publishing it to a location where developers can easily see it.

## Overview: `summarize_test_results.py`

This utility:
1. **Parses** JUnit XML test reports
2. **Renders** a Markdown table summarizing pass/fail/error counts
3. **Publishes** to `$GITHUB_STEP_SUMMARY` (GitHub Actions feature)

Result: A collapsible test summary appears in the job's UI automatically.

## Key Components

### JUnit XML Parsing
- Reads `build/test-results/*.xml` (standard Gradle location)
- Extracts: test class, method name, result (pass/fail/error), error message
- Handles nested test suites and parameterized tests

### Markdown Rendering
Output format:
```markdown
| Test Class | Method | Status | Details |
|---|---|---|---|
| CameraTest | testCapture | ✅ PASS | - |
| CameraTest | testANR | ❌ FAIL | ANR timeout |
| ...
```

### GitHub Integration
Writes output to `$GITHUB_STEP_SUMMARY`:
```bash
echo "## Test Results" >> $GITHUB_STEP_SUMMARY
echo "| ... |" >> $GITHUB_STEP_SUMMARY
```

GitHub Actions automatically displays this in the job summary.

## Conditional Execution

**Critical:** Only run summarization when it's meaningful:

```yaml
- name: Generate test summary
  if: always() && steps.diff_check.outputs.needs_full_build == 'true'
  run: python3 summarize_test_results.py
```

**`if: always()`:** Run even if previous steps failed (so you see summary of failures)
**`needs_full_build == 'true'`:** Only if full test suite actually ran (not skipped)

Running summarization on skipped test suites produces empty/misleading tables.

## Test Coverage

The script includes 28 unit tests covering:
- XML parsing (happy path and error cases)
- Markdown table rendering
- CLI argument validation
- Edge cases (no tests, malformed XML, etc.)

---

**Source:** [PR #196 — Test Result Summary Generation](https://github.com/aunger/gallery-button-for-pixel-camera/pull/196)

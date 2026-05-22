Linking screenshots to specific test cases in E2E visual testing requires a design choice between two approaches with different tradeoffs.

## Option A: Filesystem Diff Per Test

**Approach:** Save screenshots to test-specific directories (e.g., `results/TestName_MethodName/`). Each test owns a subdirectory, screenshots are compared within that directory.

**Pros:**
- Clear ownership: each test has its own directory
- Easy to audit: all artifacts for one test in one place
- Simple to implement: just mkdir per test

**Cons:**
- Directory explosion: N tests = N directories
- Requires careful cleanup between test runs
- Hard to share baseline/reference images across similar tests

## Option B: Test Fixture Wrapper

**Approach:** Create a test fixture/helper class that wraps screenshot operations. When a test calls `takeScreenshot()`, the fixture:
1. Uses reflection or thread-local context to identify the calling test
2. Saves to a unified location with metadata (test class, method, timestamp)
3. Returns a reference the test can use for assertions

**Pros:**
- Single output directory
- Fixture handles all bookkeeping
- Easy to add cross-cutting concerns (logging, archival)
- Can implement smart comparison (fuzzy matching, ROI-only, etc.)

**Cons:**
- Reflection/context overhead
- More complex fixture implementation
- Less obvious where screenshots go without reading fixture code

## Recommended Approach

**Use a combined strategy:**
1. **Default:** Option B fixture for normal cases (single screenshot per test)
2. **Override:** Allow tests to specify custom directories when doing multi-step screenshot sequences (e.g., testing animation frames)
3. **Metadata file:** Have the fixture write a JSON manifest listing all screenshots in a run, mapping each to its test, for post-analysis

This gives the simplicity of Option B with the flexibility of Option A when needed.

---

**Source:** [Issue #201 — Screenshot-Test Association Design](https://github.com/aunger/gallery-button-for-pixel-camera/issues/201)

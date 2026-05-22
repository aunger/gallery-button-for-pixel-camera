A comprehensive E2E visual test plan for camera gallery apps requires phased implementation: test infrastructure, mocks, assertions, fixtures, tests, CI integration, and documentation.

## Seven Implementation Phases

### Phase 1: Test Infrastructure
- Create `testgallery` module (test app)
- Wire JUnit test runner
- Configure Gradle to build and install test APK
- Set up environment variables for CI

### Phase 2: Mock Camera
- Implement in-app green-feed renderer
- Synthetic JPEG generation
- MediaStore insertion
- Ready signal via window focus

### Phase 3: Visual Assertion Library
- Shape templates (square, circle, squircle)
- Template matching with IoU classifier
- Position and scale sweep handling
- Edge-pixel dropout tolerance

### Phase 4: E2E Fixture
- Fixture class with helper methods
- Photo capture, screen lock, overlay tapping
- Cross-module action string handling

### Phase 5: Test Class
- Implement 8 test methods
- 2 deliberate red-light tests for regression checking (Tests 4a and 5a)
- Shape classification assertions

### Phase 6: CI Wiring
- Gradle plugin configuration
- JUnit XML parsing
- Test result summary publication
- Keyguard setup
- ANR watcher integration

### Phase 7: Commit Organization
- Document the phases as commits
- Concern-based organization
- Reference coding standards

## Test Suite Scope

**8 tests total:**
- Test 1: Gallery loads
- Test 2: Mock camera produces green
- Test 3: Photo captured appears in gallery
- Test 4a: Overlay shape is square (deliberate red-light for regression)
- Test 4b: Overlay shape is circle
- Test 5a: Overlay shape is squircle (deliberate red-light)
- Test 5b: Overlay shape is superellipse variant
- Test 6: Secure camera overlay with keyguard

**Deliberate red-lights:** Tests 4a and 5a are intentionally written to fail until the corresponding overlay feature is implemented. This validates the test framework itself and ensures we catch regressions.

## Risk & Confidence

- **GPU compatibility:** Tested and validated; `-gpu swiftshader_indirect` works with green rendering
- **Emulator stability:** ANR handling implemented (see ANR watcher KB)
- **Shape confidence:** 95%+ correct classification on real screenshots

---

**Source:** [PR #117 — E2E Visual Test Plan Document](https://github.com/aunger/gallery-button-for-pixel-camera/pull/117)

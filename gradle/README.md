# Gradle build integrity

This directory carries two "verify what you download" controls for the Gradle build, matching the posture already applied to ktlint (SHA-256 verified in `scripts/lint/install-ktlint.sh`) and the Python lint tools (pip `--require-hashes`, issue #723).

## `verification-metadata.xml` -- dependency verification (issue #714)

[Gradle dependency verification](https://docs.gradle.org/9.5.1/userguide/dependency_verification.html) pins every plugin, dependency, and metadata artifact the build resolves to a SHA-256 checksum.
Once this file is present, *every* Gradle invocation verifies each resolved artifact against it and fails fast if any artifact is missing from the file or its checksum does not match.

Scope decisions:

- **`sha256` checksums only; no PGP signatures** (`verify-signatures=false`).
  Many `google()` artifacts are unsigned, and signature verification would add a keyring to maintain plus a dependency on public keyservers.
  Checksum-only matches the ktlint and pip precedent, both of which are hash-based.
- **Metadata files are verified too** (`verify-metadata=true`, the generator default), so `.pom` and `.module` files are pinned alongside the binaries.
- **Strict mode.** No `--dependency-verification lenient` is used anywhere in CI.
  A contributor on a non-Linux machine may need it locally (see below), but never in CI.

### Regenerating the file

Run `scripts/regenerate-gradle-verification.sh`.
It regenerates the file with the exact command and options used to create it.
Set `GRADLE_BIN` to run the generation on a Gradle other than `./gradlew`, which is what a toolchain bump needs: the committed wrapper is still the old Gradle and cannot run the new AGP.

There are two regeneration modes, and the difference is deliberate:

- **The script merges.** New pins are added to whatever `verification-metadata.xml` already holds.
  That is the safe local behaviour: a run that fails or is interrupted partway never leaves your working tree with verification quietly switched off.
- **The generator workflow (`.github/workflows/regenerate-gradle-toolchain.yml`) deletes first.**
  It removes the file before regenerating, so the result is built from scratch and carries no pins left over from a previous dependency graph.
  That `rm` lives in the workflow rather than in the script for exactly the reason above, and `scripts/test_verification_metadata.sh` check (e) allowlists that one workflow filename for the pattern while failing any other workflow that deletes or moves the file.

Any change that moves the dependency graph, an AGP, Kotlin, Compose, AndroidX, or test-library version bump, a new dependency, or a new plugin, requires regenerating this file **in the same commit**, or the build will fail verification.
There is no automated dependency bumper in this repo, so the graph only moves on manual, reviewed version changes.

Requirements, and why they exist:

- **Regenerate on Linux.** The merge-gating CI (`.github/workflows/build.yml`) runs on `ubuntu-latest`, and some artifacts are OS-classified.
  Chief among them is AGP's `aapt2`, recorded here as `aapt2-<version>-linux.jar`.
  A file generated on macOS or Windows records that platform's classifier instead and fails verification on the Linux CI.
- **Full Android toolchain.** Generation needs JDK 17 and the Android SDK (platform `android-35`, `build-tools;36.0.0`, `platform-tools`), matching the generator workflow.
  A JDK-only environment cannot resolve the Android dependency graph, which is why this was split out of the pip-hashing work in issue #699.
- **Review the diff** (`git diff gradle/verification-metadata.xml`) before committing, then let the complete `build.yml` run (including the instrumented and E2E steps) validate it end to end.
  That full run is the only way to catch a configuration whose dependencies were missed during generation; on a verification failure Gradle names the offending artifact, so re-run the script (it merges into the existing file) and repeat until CI is green.

Contributors on macOS or Windows who only need a local build can pass `--dependency-verification lenient` to downgrade a verification failure to a warning.
Never commit that flag into CI, and do not commit a file regenerated on a non-Linux machine.

## `wrapper/gradle-wrapper.properties` -- distribution pin

`distributionSha256Sum` pins the Gradle 9.5.1 distribution (`gradle-9.5.1-bin.zip`) to its published SHA-256.
The Gradle wrapper verifies the downloaded distribution against this checksum before running it, so a substituted or corrupted distribution fails loudly.

The wrapper JAR (`wrapper/gradle-wrapper.jar`) is committed and reviewed directly; it is read before `verification-metadata.xml` is consulted, so it cannot be covered by that file.
Instead, `scripts/test_verification_metadata.sh` check (g) verifies the JAR's SHA-256 against a pinned copy of the checksum Gradle officially publishes for it (issue #744), so a substituted or corrupted wrapper JAR fails the `shell-tests` CI job rather than being trusted silently.

That JAR pin is version-blind on its own.
Gradle republishes byte-identical wrapper JARs across releases (9.5.0, 9.5.1, and 9.6.1 all ship this one), so a matching checksum proves the bytes are genuinely Gradle's but says nothing about which distribution the wrapper will download.
The same guard script therefore also pins the expected version and distribution checksum and asserts both against `gradle-wrapper.properties`, so the JAR, the version, and the distribution have to describe one release.

`gradlew` and `gradlew.bat` are regenerated as a matched set with the JAR and properties, by `gradle wrapper` on the target version, never hand-edited.
The regenerated `gradlew` launches with the standard `-jar gradle/wrapper/gradle-wrapper.jar`, and the vestigial `CLASSPATH="\"\""` assignment and `-classpath "$CLASSPATH"` argument that older Gradle wrapper scripts carried are simply gone.

When bumping the Gradle version, update both `distributionUrl` and `distributionSha256Sum` (the published checksum is at `https://services.gradle.org/distributions/gradle-<version>-bin.zip.sha256`), then regenerate `verification-metadata.xml`.
Also update the same two pins in `.claude/setup-environment.sh`, which seeds that distribution into the Claude web environment's Gradle cache, and re-derive the `EXPECTED_DIST_HASH` in `scripts/test_setup_environment.sh` (that guard script fails the build if either is forgotten; `.claude/environment.md` explains why the pasted copy also has to be refreshed by hand).
If the wrapper JAR itself changes, also update its pinned SHA-256 in `scripts/test_verification_metadata.sh` (the published checksum is at `https://services.gradle.org/distributions/gradle-<version>-wrapper.jar.sha256`), along with the version and distribution constants next to it.

## CodeQL Kotlin ceiling

CodeQL extracts Kotlin with its own bundled compiler frontend, which supports a bounded range of Kotlin versions.
Any Kotlin, KGP, or Kotlin-compiler bump must check that ceiling **before** it is made, or `analyze-kotlin` in `.github/workflows/codeql.yml` fails on a version CodeQL cannot extract.

To check it:

1. Read `cliVersion` from `src/defaults.json` in `github/codeql-action`, at the ref `codeql.yml` actually uses (currently `v4`).
2. Read the Kotlin row of `docs/codeql/reusables/supported-versions-compilers.rst` in `github/codeql`, at tag `codeql-cli/v<cliVersion>`.

The upper bound uses a trailing-`x` wildcard for the patch digit, so `2.4.0x` means "any 2.4.0 patch", that is, everything below 2.4.10.
Read `2.4.1x` the same way: up to but not including 2.4.20.

Note that `codeql-action@v4` is a force-moved tag, not an immutable one.
A previously red `analyze-kotlin` can turn green on a plain re-run once the tag moves to a bundle whose CLI supports the Kotlin version in use, with no change to this repository.

This section deliberately records the procedure for recomputing the ceiling rather than a cached answer.
A specific ceiling read off the tag on a given day decays silently as the tag moves; the procedure stays correct.

## KGP compatibility row

Separately from the CodeQL ceiling, the Kotlin Gradle plugin documents which Gradle and AGP versions each KGP row supports, in the compatibility table on [Configure a Gradle project](https://kotlinlang.org/docs/gradle-configure-project.html).
The row for the KGP version in use must cover both the intended Gradle version and the intended AGP version.

Read that table at bump time.
Like the CodeQL ceiling, it moves without notice, so no value from it is recorded here.

The two constraints are independent, and neither subsumes the other: this row bounds Gradle and AGP for a given KGP, while the CodeQL ceiling bounds the Kotlin version itself.
A Gradle-only or AGP-only bump can be blocked by this row alone, and a Kotlin-only bump by the CodeQL ceiling alone.
Check both, every time.

Exceeding a row is a decision to record, not an error to avoid at all costs.
#774 accepted Gradle 9.5.1, one patch above the fully-supported maximum of the KGP row it was on, because Gradle had already superseded that maximum with 9.5.1 (fixes ship only in the newest patch of the newest minor, so the older patch would receive none) and because 9.5.1 exists to fix an OOM regression.
Weigh any future overage the same way, and write down the reasoning: a patch above the row is a far smaller step than a minor above it.

## Performing a toolchain bump

1. Check the KGP compatibility row and the CodeQL Kotlin ceiling, both above.
   Do this **before** editing any version, not after a red CI run.

2. Edit the versions.
   The root `build.gradle.kts` holds the KGP buildscript classpath pin, the AGP plugin version, and the Compose plugin version, which moves in lockstep with KGP.
   Other pin sets travel with a bump depending on what moved:

   - **A Gradle bump** touches every pin listed under the distribution-pin section above, *and* the `GRADLE_VERSION`, `DIST_SHA256`, and `WRAPPER_JAR_SHA256` values in `.github/workflows/regenerate-gradle-toolchain.yml`.
     Miss those three and step 3 downloads the old Gradle and rewrites `gradle-wrapper.properties` back to it, so the run fails its own guard step and uploads no artifact, only the `DO-NOT-COMMIT` diagnostic.
   - **An AGP bump** may move the required Android build-tools version, pinned in `.claude/hooks/session-start.sh`, `.claude/setup-environment.sh`, `.github/workflows/regenerate-gradle-toolchain.yml`, and the "Full Android toolchain" requirement above.

   Know which of these are enforced.
   `scripts/test_verification_metadata.sh` and `scripts/test_setup_environment.sh` fail the build on drift among the pins they compare, but nothing checks the generator workflow's three values against them, and nothing can check that `.claude/setup-environment.sh` has been re-pasted into the web form (see `.claude/environment.md`).
   Those last two are human steps.

3. Commit the step 2 edits and push the branch, then dispatch `.github/workflows/regenerate-gradle-toolchain.yml` against it.
   The workflow checks out the pushed tip, not your working tree, so an uncommitted edit is silently regenerated against the old graph.
   It rebuilds the wrapper matched set and `verification-metadata.xml` from scratch and uploads them as an artifact rather than committing them.

4. Review the artifact, then amend it into the step 2 commit and force-push.
   #774's Step 5 records the review recipe: verify the wrapper JAR against its published checksum; regenerate the wrapper from the verified distribution and compare with `cmp` for content **and** `stat` for modes, since neither `cmp` nor `diff` sees the exec bit that the tarball packaging exists to preserve; and diff the artifact's `metadata-components.txt` against the same listing derived from the committed file (the generator's "Emit the pinned-component listing" step holds the script that produces it) rather than reading the checksum hashes.
   Unpack the tarball outside the repository: `metadata-components.txt` is review-only and is not gitignored.

5. Open the PR and let the complete `build.yml` run validate it, per the regeneration requirements above.
   That full run is the only thing that catches a configuration whose dependencies were missed during generation; on a verification failure, re-run the merge-mode script and repeat until green.
   #774 also requires a check CI cannot perform: after merge, install the `dev-build` APK and exercise the overlay once, because the release variant has no runtime coverage anywhere in CI.

The bump lands as one commit, so the version edits and the regenerated pins are never separated.

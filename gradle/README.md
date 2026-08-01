# Gradle build integrity

This directory carries two "verify what you download" controls for the Gradle build, matching the posture already applied to ktlint (SHA-256 verified in `scripts/install-ktlint.sh`) and the Python lint tools (pip `--require-hashes`, issue #723).

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

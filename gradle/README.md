# Gradle build integrity

This directory carries two "verify what you download" controls for the Gradle build, matching the posture already applied to ktlint (SHA-256 verified in `scripts/lint/install-ktlint.sh`) and the Python lint tools (pip `--require-hashes`, issue #723).
It also carries the procedure for moving the toolchain those controls pin, which reaches beyond this directory; see "Performing a toolchain bump" at the end.

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
The scheduled watcher described under "Noticing when a bump becomes available" below reports that a bump may have become possible; it never performs one.

Requirements, and why they exist:

- **Regenerate on Linux.** The merge-gating CI (`.github/workflows/build.yml`) runs on `ubuntu-latest`, and some artifacts are OS-classified.
  Chief among them is AGP's `aapt2`, recorded here as `aapt2-<version>-linux.jar`.
  A file generated on macOS or Windows records that platform's classifier instead and fails verification on the Linux CI.
- **Full Android toolchain.** Generation needs JDK 17 and the Android SDK (platform `android-35`, `build-tools;36.0.0`, `platform-tools`), matching the generator workflow.
  A JDK-only environment cannot resolve the Android dependency graph, which is why this was split out of the pip-hashing work in issue #699.
- **Review the diff** (`git diff gradle/verification-metadata.xml`) before committing, then let the complete `build.yml` run (including the instrumented and E2E steps) validate it end to end.
  That full run is the only way to catch a configuration whose dependencies were missed during generation; on a verification failure Gradle names the offending artifact, so re-run the script (it merges into the existing file) and repeat until CI is green.
  That merge-mode remedy is for iterating on an unchanged toolchain.
  During a toolchain bump, re-dispatch the generator workflow instead: a local merge records the missing pin from whatever machine you are on, losing the Linux classifier guarantee and the CI provenance that make the generated file reviewable (see "Performing a toolchain bump" below).

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
On every Gradle bump, re-read all three constants in `scripts/test_verification_metadata.sh` from their published URLs in one change, as that script's own header instructs (the wrapper-JAR checksum is at `https://services.gradle.org/distributions/gradle-<version>-wrapper.jar.sha256`).
The JAR value often turns out unchanged, since Gradle republishes it across releases, but you only learn that by fetching it.

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

## KGP compatibility row

Separately from the CodeQL ceiling, the Kotlin Gradle plugin documents which Gradle and AGP versions each KGP row supports, in the compatibility table on [Configure a Gradle project](https://kotlinlang.org/docs/gradle-configure-project.html).
The row for the KGP version in use must cover both the intended Gradle version and the intended AGP version.

Read that table at bump time, and recompute the CodeQL ceiling by its own procedure above.
Treat no version printed anywhere in this file as a current bound: the wildcard forms above illustrate the notation, not today's ceiling, and both limits move without notice.

The row constrains in both directions, which is what makes it easy to underestimate.
For a given KGP it bounds Gradle and AGP, so a Gradle-only or AGP-only bump can be blocked by it.
For a given Gradle and AGP pair it also bounds which KGP rows are available at all: #774 found KGP 2.4.x forced rather than chosen, because it was the only row covering Gradle 9.5.x together with AGP 9.1.0.
So a KGP-only bump is governed by this row too, not by the CodeQL ceiling alone.

Exceeding a row is a decision to record, not an error to avoid at all costs.
#774 accepted Gradle 9.5.1, one patch above whatever the row's fully-supported maximum was at the time.
That maximum carries an OOM regression, and the fix for it shipped only in 9.5.1, outside the row.
Gradle patches only its newest minor, so no later release inside the row would ever carry that fix, and staying in-row meant shipping a known bug indefinitely.
#774 also rejected the obvious workaround of raising the daemon heap instead.
Weigh any future overage the same way, and write down the reasoning: a patch above the row is a far smaller step than a minor above it.

## Noticing when a bump becomes available

The two sections above describe checks to make at bump time.
`scripts/ci/prs-and-issues/watch_toolchain_bump.py` executes **one** of them on a schedule, the CodeQL Kotlin ceiling, so that noticing a bump became possible does not depend on anyone remembering to look.
It does not check the KGP compatibility row, which stays a manual step for the reasons below; what it watches instead is the KGP release that could move that row.
`.github/workflows/watch-toolchain-bump.yml` runs it weekly, and it comments on one long-lived tracking issue only when something it watches moves.

It is a watcher, not a bumper.
A comment on that issue does not move the dependency graph, and the issue is not dispatchable work.
It carries no `orchestrate` label for exactly that reason: an open issue standing in for a "has a bump become available yet" reminder is the pattern #803 replaced, because its candidate list decays (the one in #789 named Gradle 9.6.x and was wrong within two days).

What it tracks:

- The highest stable `org.jetbrains.kotlin:kotlin-gradle-plugin` version published to Maven Central.
  The `<latest>` and `<release>` elements of `maven-metadata.xml` are not used, because Kotlin sets both to the newest upload, which is routinely a Beta or RC.
- The CodeQL Kotlin upper bound, by executing the two-step procedure under "CodeQL Kotlin ceiling" above, including reading the `codeql-action` ref out of `codeql.yml` rather than assuming it is still `v4`.
- Whether OSV reports an advisory against `org.gradle:gradle-core`, `com.android.tools.build:gradle`, or `org.jetbrains.kotlin:kotlin-gradle-plugin` at the versions this tree pins.

Every version it compares against is read out of the tree (`gradle-wrapper.properties`, `build.gradle.kts`, `codeql.yml`), so a bump does not also require editing the watcher, and the watcher cannot quietly compare against a stale pin.

What it deliberately does not do:

- **It does not read the KGP compatibility table.**
  That table is the one input with no machine-readable form, and it is also the input that takes judgment to apply, per the overage reasoning recorded above.
  A new stable KGP release is the event that can move the row, so watching releases gets the same trigger without depending on the table's HTML.
  When the watcher comments, re-read the table by hand.
- **It does not scan the whole dependency graph for CVEs.**
  `verification-metadata.xml` pins the build graph, not what ships in the APK, so a scan over it says nothing about the app; every advisory it finds today arrives transitively through Gradle and AGP build tooling, and none through a declared dependency.
  A weekly report of the same few dozen standing build-tool advisories would train everyone to ignore the job, which is the same failure mode as an open issue that is not really actionable.
  The three toolchain coordinates are different: a hit there is directly actionable, and is a reason to bump that overrides the standing lack of urgency.
  App-runtime CVE coverage would be Dependabot over `app/build.gradle.kts`, which is separate work; there is no `.github/dependabot.yml` here today.
  This is also not the repo's general advisory scan: `.github/workflows/dependency-audit.yml` runs `pip-audit` weekly over every hash-pinned Python lock under `scripts/` (issue #804).
  The two do not overlap, and neither subsumes the other; that one watches what CI's own helper scripts import, this one watches three Maven coordinates because a hit against them is an argument for a toolchain bump.

An input that is fetched but cannot be parsed, or whose URL 404s because upstream moved it, is reported as an upstream format change, never folded into a silent "nothing moved".
A network-level failure reports nothing at all, since it is evidence of nothing.
The same rule governs the tracking issue itself: the watcher creates one only on a *confirmed* absence, checked against the issue list rather than the eventually-consistent search index, because a duplicated tracking issue would split the watcher's only state store and needs a human to clean up.

Run `python3 scripts/ci/prs-and-issues/watch_toolchain_bump.py --dry-run` to print the current observation without touching the tracking issue.
That is also the quickest way to confirm the parsers still work after an upstream reformat.

## Performing a toolchain bump

This covers a Gradle, AGP, KGP, or Compose-plugin bump.
A JDK change has a wider blast radius and is not covered here.

> [!WARNING]
> This procedure was reconstructed by reading the repository and #774, not by performing a bump.
> No step below has been executed end to end.
> Treat it as a checklist to verify against the current tree, and correct it from what you observe on the first real bump.

1. Check the KGP compatibility row above, which binds on every bump this section covers.
   Add the CodeQL Kotlin ceiling check when the bump moves Kotlin, KGP, or the Kotlin compiler, per that section's own scope.
   Do this **before** editing any version, not after a red CI run.
   The watcher's latest comment already carries the ceiling and the newest published KGP, but it does not read the compatibility row, so that part is still yours to do by hand.

2. Edit the versions.
   The root `build.gradle.kts` holds the KGP buildscript classpath pin, the AGP plugin version, and the Compose plugin version, which moves in lockstep with KGP.
   For a Gradle bump the surrounding pins are the ones named in the distribution-pin section above.
   Beyond those, a handful of version literals sit in prose and script headers that no guard reads: the docs URL and the toolchain requirement near the top of this file, the header of `scripts/regenerate-gradle-verification.sh`, and the AGP comment in `.claude/hooks/session-start.sh`.

   Two sites in `.github/workflows/regenerate-gradle-toolchain.yml` are easy to miss because no section above names them.
   Its `env:` block duplicates the Gradle version and both checksums; the workflow runs the build-integrity guard in the same job, so a block that disagrees with `scripts/test_verification_metadata.sh` fails the run loudly, but a bump that leaves **both** of them stale passes instead, and the artifact then reverts the version change.
   Its `sdkmanager --install` line pins the build-tools and SDK platform separately from the `.claude/` provisioning scripts, and nothing cross-checks it against them.

   Do not grep-and-replace a version across the tree.
   `gradle/verification-metadata.xml` carries dozens of incidental matches per version and is regenerated wholesale by step 3, so exclude it from any search.

   > [!IMPORTANT]
   > Every version of this inventory has turned out incomplete when checked against the tree.
   > Treat it as a starting point, search deliberately for the rest, and add what you find.

3. Commit the step 2 edits and push the branch, then dispatch `.github/workflows/regenerate-gradle-toolchain.yml` against it.
   Do not open the PR yet: `build.yml` runs on pull requests, so opening one now spends a full build on a commit that is still half-migrated by design.
   The workflow checks out the pushed tip, not your working tree, so an uncommitted edit is silently regenerated against the old graph.
   It rebuilds `verification-metadata.xml` from scratch and then the wrapper matched set, an order chosen so the wrapper step runs under enforcement of the fresh pins, and uploads them as `gradle-toolchain-regenerated`.
   A **failed** run instead uploads `gradle-toolchain-partial-DO-NOT-COMMIT`, whose metadata Gradle may have written only partly; never amend that one in.

4. Review the downloaded artifact, then bring the files into the tree, amend them into the step 2 commit, and force-push.
   #774's Step 5 holds the review recipe, but it is written with 9.5.1 literals throughout, so substitute your own version and checksum rather than running its commands as printed.
   Unpack the tarball somewhere outside the working tree: it carries a review-only `metadata-components.txt` whose path inside the archive is the repository root, and which is not gitignored.
   Keep `gradlew` executable, and confirm `git diff` reports no mode change on it; the tarball format exists because `upload-artifact` strips permissions.

5. Open the PR and let `build.yml` validate it end to end.
   `codeql.yml`'s `analyze-kotlin` job is what actually tests the ceiling computed in step 1, but on a pull request it runs only when the diff touches `.kt`, `.kts`, or `.java`, so a Gradle-only bump does not exercise it until the post-merge push to `main`.
   #774 Step 6 also asks for one enforcing run against live registries before merge; note that `build.yml`'s cache falls back through a `restore-keys` prefix, so clearing the branch's own entries is not sufficient on its own to force a cold resolve.
   On a verification failure at this stage, re-dispatch the generator and re-amend rather than reaching for the local merge-mode remedy, for the reason given under "Review the diff" above.

   > [!CAUTION]
   > Review could not settle which remedy is correct here.
   > A re-dispatch is deterministic, so it will reproduce the same pin set unless the task list in `scripts/regenerate-gradle-verification.sh` is what needs extending.
   > Diagnose which of the two you are facing before spending a 20-45 minute run on it.

6. Finish what CI cannot check.
   Re-paste `.claude/setup-environment.sh` into the web environment if it changed (see `.claude/environment.md`), and after merge install the `dev-build` APK and exercise the overlay once, because the release variant has no runtime coverage anywhere in CI.

The bump lands as one commit, so the version edits and the regenerated pins are never separated.

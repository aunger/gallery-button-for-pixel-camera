# workflow_yaml.sh: shape helpers for tests that assert things about
# .github/workflows/*.yml. Source it; it defines functions and runs nothing.
#
#   . "$REPO_ROOT/scripts/lib/workflow_yaml.sh"
#
# These read indentation rather than parsing YAML. That is enough for the
# questions the workflow guard tests ask ("what does this job's permissions
# block grant?", "does this step download from another run?") and keeps those
# tests dependency-free, which matters because they run in the shell-tests job
# before any Python or JVM tooling is set up.
#
# What they assume, none of which YAML itself requires:
#
#   - Block style, indented with spaces, for the `jobs:` mapping and for job
#     bodies. A flow mapping spanning a job body would not be understood.
#     (permissions_grant_actions_read does handle a flow mapping, since
#     `permissions: { actions: read }` is a plausible thing to write.)
#   - At most one top-level `permissions:` key, at column 0. Its position
#     relative to `jobs:` does not matter.
#   - Job names that are plain scalars, matched literally, not as patterns.
#
# A file that breaks an assumption fails loudly (a block comes back empty, and
# the caller reports its own check as failed) rather than passing vacuously.
#
# Every helper reads its input to the end, and callers capture the whole
# output. Nothing here may be rewritten into `producer | grep -q` or
# `producer | head -1`: those exit at the first match and close the pipe, the
# producer dies of SIGPIPE (141), and under `set -o pipefail` a pattern that IS
# present gets reported missing. The window opens only once the producer's
# output outgrows a single buffered write, which is why that bug survived in
# test_dependabot_verification_metadata_workflows.sh until one job grew past
# that size and CI run 31978297339 reported "statuses: write" missing from a
# job that declares it.

# awk helper functions shared by the readers below, prepended to their program
# text rather than copied into each of them:
#
#     awk "$_WORKFLOW_YAML_AWK"'
#         { ... }
#     '
#
# The variable is expanded in double quotes and the program body stays in its
# own single-quoted string, so the shell substitutes the helpers and leaves the
# program's own `$0` alone.
_WORKFLOW_YAML_AWK='
    function indent_of(s,   n) { n = match(s, /[^ ]/); return n == 0 ? -1 : n - 1 }
'

# Print the block nested under the first `<key>:` line on stdin, plus that
# line's own inline value if it has one, so both
#
#   permissions:            and    permissions: write-all
#     actions: read
#
# come back as the key's content. The block ends at the first non-blank,
# non-comment line indented no deeper than the key itself.
yaml_sub_block() {
    awk -v key="$1" "$_WORKFLOW_YAML_AWK"'
        !found && $0 ~ "^ *" key ":" {
            found = 1
            key_indent = indent_of($0)
            rest = $0
            sub("^ *" key ":", "", rest)
            gsub(/^[ \t]+|[ \t]+$/, "", rest)
            if (rest != "" && substr(rest, 1, 1) != "#") print rest
            next
        }
        found {
            if ($0 ~ /^[ \t]*$/ || $0 ~ /^[ \t]*#/) next
            if (indent_of($0) <= key_indent) { found = 0; next }
            print
        }
    '
}

# Print the `jobs:` mapping of a workflow file.
workflow_jobs_section() { yaml_sub_block jobs < "$1"; }

# Print the names of a workflow file's top-level jobs, one per line.
workflow_job_names() {
    workflow_jobs_section "$1" | awk "$_WORKFLOW_YAML_AWK"'
        /^[ \t]*$/ || /^[ \t]*#/ { next }
        !base_set { base = indent_of($0); base_set = 1 }
        indent_of($0) == base && /:[ \t]*$/ {
            name = $0
            sub(/^ +/, "", name)
            sub(/:[ \t]*$/, "", name)
            print name
        }
    '
}

# Print the body of one named job (its keys, not its `<name>:` header line).
# The name is compared literally, so a job called `push` cannot be confused
# with some deeper key that happens to be spelled the same way.
workflow_job_block() {
    workflow_jobs_section "$1" | awk -v job="$2" "$_WORKFLOW_YAML_AWK"'
        /^[ \t]*$/ || /^[ \t]*#/ { if (in_job) print; next }
        !base_set { base = indent_of($0); base_set = 1 }
        indent_of($0) == base {
            name = $0
            sub(/^ +/, "", name)
            sub(/:[ \t]*$/, "", name)
            in_job = (name == job)
            next
        }
        in_job
    '
}

# Print a workflow file's top-level `permissions:` block: the one at column 0,
# wherever in the file it sits. Anchoring on the column rather than on
# "everything before `jobs:`" means a workflow that declares its permissions
# after its jobs (valid YAML; mapping key order is free) is read correctly
# instead of looking like it has no block at all.
workflow_permissions() {
    awk '
        /^permissions:/ {
            found = 1
            rest = $0
            sub(/^permissions:/, "", rest)
            gsub(/^[ \t]+|[ \t]+$/, "", rest)
            if (rest != "" && substr(rest, 1, 1) != "#") print rest
            next
        }
        found {
            if ($0 ~ /^[ \t]*$/ || $0 ~ /^[ \t]*#/) next
            if ($0 !~ /^[ \t]/) { found = 0; next }
            print
        }
    ' "$1"
}

# Print one job's own `permissions:` block, empty if it declares none. A
# job-level block fully replaces the workflow-level one for that job rather
# than merging with it, so "empty" and "inherits everything" are the same
# answer and the caller falls back to workflow_permissions.
workflow_job_permissions() { workflow_job_block "$1" "$2" | yaml_sub_block permissions; }

# Whether a permissions block ($1) grants actions: read or better. `read-all`
# and `write-all` both include it. The block is normalized first so a flow
# mapping, `permissions: { actions: read, issues: write }`, is read the same
# way as the equivalent block mapping.
permissions_grant_actions_read() {
    local normalized
    normalized="$(printf '%s\n' "$1" | tr '{},' '\n\n\n')"
    grep -Eq '^[ \t]*(actions:[ \t]*(read|write)|read-all|write-all)[ \t]*$' <<<"$normalized"
}

# Whether $1 (a captured block) has a line matching the ERE $2. Plain grep on a
# here-string, so there is no pipe to close early; see the SIGPIPE note above.
block_has() { grep -E "$2" <<<"$1" > /dev/null; }

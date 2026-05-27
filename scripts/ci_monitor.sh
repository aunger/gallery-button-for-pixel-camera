#!/usr/bin/env bash
# ci_monitor.sh — Poll a PR's CI and stream a terminal outcome plus per-test signals.
#
# Invoked by the Orchestrator's Monitor tool call (see agents/dev_orchestration.md).
# Each stdout line is consumed as a task-notification event, so output is the
# interface: terminal outcome lines end the loop, while informational lines
# (in_progress heartbeat, per-step deltas, per-test FAILs) keep it alive.
#
# Usage:
#   bash scripts/ci_monitor.sh <PR_NUMBER>
#
# Arguments:
#   <PR_NUMBER>   The pull request number to monitor (required).
#
# Environment:
#   GITHUB_TOKEN  GitHub token used for the REST calls (required).
#
# Outcome vocabulary (one terminal line ends the loop):
#   PR#N: Clear ...      All checks passed and mergeable_state is clean/unstable.
#   PR#N: Blocked ...    A check failed, or mergeable_state is behind/dirty.
#   PR#N: Infra ...      A CI infrastructure problem, or mergeable_state=blocked.
#   PR#N: in_progress    CI still running; emitted only after >120 s of silence.
#   PR#N: step "..." -> ...    A build-and-test step reached a conclusion (informational).
#   PR#N: FAIL [suite] name: ...   A per-test failure from a testresults artifact (informational).
#
# NOTE on error handling: this script deliberately does NOT use `set -e`. The
# poll loop must survive transient REST/parse failures — the many
# `curl ... 2>/dev/null` and `python3` invocations may exit non-zero on a
# hiccup, and `set -e` would kill the resilient loop on the first such blip.
# `set -u`/`set -o pipefail` are likewise avoided so a missing field or a
# python exit inside a pipeline cannot abort the loop. The 30-minute escalation
# threshold is enforced by `timeout_ms` on the Monitor call, not here.

OWNER="aunger"
REPO="gallery-button-for-pixel-camera"
PR="$1"
HEADERS=(-H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json")
last_output_ts=$(date +%s)

# Dedup state for the streamed test-result signals (removed when the loop exits).
seen_steps=$(mktemp); seen_arts=$(mktemp); seen_fails=$(mktemp)
trap 'rm -f "$seen_steps" "$seen_arts" "$seen_fails"' EXIT

# Print each line of $1 prefixed with the PR tag, and reset the 120s silence
# timer (any streamed output counts as liveness).
emit_block() {
  [ -z "$1" ] && return 0
  printf '%s\n' "$1" | sed "s/^/PR#${PR}: /"
  last_output_ts=$(date +%s)
}

while true; do
  sha=$(curl -s "${HEADERS[@]}" "https://api.github.com/repos/$OWNER/$REPO/pulls/$PR" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('head',{}).get('sha',''))" 2>/dev/null)

  [ -z "$sha" ] && { echo "PR#${PR}: could not fetch SHA"; last_output_ts=$(date +%s); sleep 30; continue; }

  check_data=$(curl -s "${HEADERS[@]}" \
    "https://api.github.com/repos/$OWNER/$REPO/commits/$sha/check-runs" 2>/dev/null)

  result=$(echo "$check_data" | python3 -c "
import sys,json
d=json.load(sys.stdin)
runs=d.get('check_runs',[])
total=d.get('total_count',0)
if total==0:
    print('Clear'); exit()
statuses=[r['status'] for r in runs]
conclusions=[r.get('conclusion','') for r in runs if r['status']=='completed']
if any(s in ('in_progress','queued') for s in statuses):
    print('in_progress')
elif all(s=='completed' for s in statuses):
    if any(c in ('cancelled','timed_out','stale','startup_failure') for c in conclusions): print('Infra')
    elif any(c in ('failure','action_required') for c in conclusions): print('Blocked')
    else: print('all_passed')
else:
    print('in_progress')
" 2>/dev/null)

  # --- Streamed test-result signals -----------------------------------------
  # Emitted independent of the overall check conclusion, so E2E failures surface
  # even while the check stays green via continue-on-error. Both signals are
  # purely informational: they reset the silence timer but never end the loop.
  run_id=$(curl -s "${HEADERS[@]}" \
    "https://api.github.com/repos/$OWNER/$REPO/actions/runs?head_sha=$sha&event=pull_request&per_page=5" | \
    python3 -c "
import sys,json
for r in json.load(sys.stdin).get('workflow_runs',[]):
    if r.get('status')!='cancelled': print(r['id']); break
" 2>/dev/null)

  if [ -n "$run_id" ]; then
    # Signal 1 — per-step conclusion deltas for the build-and-test job. Emit when
    # a step reaches 'completed' if it is one of the three named test steps (on any
    # conclusion) or genuinely failed; successful setup steps and skipped
    # conditional (upload-on-failure) steps are suppressed as noise. Deduped by step
    # number. Gives the live "which group finished/failed, and when" signal.
    steps_out=$(curl -s "${HEADERS[@]}" \
      "https://api.github.com/repos/$OWNER/$REPO/actions/runs/$run_id/jobs?per_page=30" | \
      SEEN="$seen_steps" python3 -c "
import sys,json,os
seen_f=os.environ['SEEN']
seen=set(open(seen_f).read().split()) if os.path.getsize(seen_f) else set()
new=[]; out=[]
for j in json.load(sys.stdin).get('jobs',[]):
    if j.get('name')!='build-and-test': continue
    for s in j.get('steps',[]):
        if s.get('status')!='completed': continue
        num=str(s.get('number'))
        if num in seen: continue
        new.append(num)
        name=s.get('name','?'); concl=s.get('conclusion') or '?'
        if name=='Build and run unit tests' or 'E2ETest' in name or concl in ('failure','cancelled','timed_out','action_required'):
            out.append('step \"%s\" -> %s' % (name, concl))
if new: open(seen_f,'a').write('\n'.join(new)+'\n')
print('\n'.join(out))
" 2>/dev/null)
    emit_block "$steps_out"

    # Signal 2 — per-test FAIL detail from the testresults-<group> artifacts the
    # workflow uploads after each test step. Download each new artifact once, parse
    # its ##GB4PC_TEST## ndjson markers, and emit new FAIL entries (message +
    # truncated trace), deduped across polls by suite#name.
    arts=$(curl -s "${HEADERS[@]}" \
      "https://api.github.com/repos/$OWNER/$REPO/actions/runs/$run_id/artifacts?per_page=100" | \
      SEEN="$seen_arts" python3 -c "
import sys,json,os
seen_f=os.environ['SEEN']
seen=set(open(seen_f).read().split()) if os.path.getsize(seen_f) else set()
for a in json.load(sys.stdin).get('artifacts',[]):
    n=a.get('name','')
    if n.startswith('testresults-') and not a.get('expired') and str(a['id']) not in seen:
        print('%s\t%s' % (a['id'], n))
" 2>/dev/null)
    while IFS=$'\t' read -r aid _; do
      [ -z "$aid" ] && continue
      tmp=$(mktemp -d)
      if curl -sL "${HEADERS[@]}" \
           "https://api.github.com/repos/$OWNER/$REPO/actions/artifacts/$aid/zip" -o "$tmp/a.zip" \
         && unzip -qo "$tmp/a.zip" -d "$tmp" 2>/dev/null; then
        fails_out=$(find "$tmp" -name '*.ndjson' -exec cat {} + 2>/dev/null | SEEN_FAILS="$seen_fails" python3 -c "
import sys,json,os
seen_f=os.environ['SEEN_FAILS']
seen=set(open(seen_f).read().splitlines()) if os.path.getsize(seen_f) else set()
new=[]
for raw in sys.stdin:
    i=raw.find('##GB4PC_TEST##')
    if i==-1: continue
    try: m=json.loads(raw[i+len('##GB4PC_TEST##'):].strip())
    except Exception: continue
    if m.get('outcome')!='FAIL': continue
    key=m.get('suite','')+'#'+m.get('name','')
    if key in seen: continue
    seen.add(key); new.append(key)
    msg=(m.get('msg') or '').strip(); tr=(m.get('trace') or '').strip()
    if len(tr)>800: tr=tr[:800]+' ...(truncated)'
    line='FAIL [%s] %s: %s' % (m.get('suite','?'), m.get('name','?'), msg)
    if tr: line+='\n  '+tr.replace('\n','\n  ')
    print(line)
if new: open(seen_f,'a').write('\n'.join(new)+'\n')
")
        emit_block "$fails_out"
        echo "$aid" >> "$seen_arts"
      fi
      rm -rf "$tmp"
    done <<< "$arts"
  fi
  # --------------------------------------------------------------------------

  if [ "$result" = "in_progress" ]; then
    now=$(date +%s)
    if [ $((now - last_output_ts)) -gt 120 ]; then
      echo "PR#${PR}: in_progress"
      last_output_ts=$now
    fi
  elif [ "$result" = "all_passed" ]; then
    mergeable=$(curl -s "${HEADERS[@]}" "https://api.github.com/repos/$OWNER/$REPO/pulls/$PR" | \
      python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mergeable_state','unknown'))" 2>/dev/null)
    if [ "$mergeable" = "clean" ] || [ "$mergeable" = "unstable" ]; then
      echo "PR#${PR}: Clear (mergeable_state=$mergeable)"; break
    elif [ "$mergeable" = "behind" ] || [ "$mergeable" = "dirty" ]; then
      echo "PR#${PR}: Blocked (mergeable_state=$mergeable)"; break
    elif [ "$mergeable" = "blocked" ]; then
      echo "PR#${PR}: Infra (mergeable_state=blocked)"; break
    else
      echo "PR#${PR}: all_passed mergeable_state=$mergeable (still computing)"
      last_output_ts=$(date +%s)
    fi
  elif [ "$result" = "Blocked" ] || [ "$result" = "Infra" ]; then
    echo "PR#${PR}: $result"; break
  else
    echo "PR#${PR}: $result"
    last_output_ts=$(date +%s)
  fi

  sleep 30
done

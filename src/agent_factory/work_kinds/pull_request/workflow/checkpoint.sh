#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("phase", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("branch_name", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("change_name", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("artifact_dir", ""))')"
fi
phase=$1
branch=$2
change_name=${3:-}
artifact_dir=${4:-}
if [ "$phase" = implemented ]; then
  python3 - "$change_name" <<'PYTASK'
import re
import sys
from pathlib import Path
name = sys.argv[1]
if not name or Path(name).name != name:
    raise SystemExit('implemented checkpoint requires one change name')
path = Path('openspec/changes') / name / 'tasks.md'
text = path.read_text()
tasks = re.findall(r'^\s*-\s*\[[ xX]\]', text, flags=re.MULTILINE)
if len(tasks) != 1:
    raise SystemExit('implemented checkpoint requires exactly one planned task')
updated = re.sub(r'^(\s*-\s*)\[ \]', r'\1[x]', text, count=1, flags=re.MULTILINE)
path.write_text(updated)
PYTASK
  git add -A -- . ':(exclude).agent-runner' ':(exclude)openspec/changes/*'
  git add -A -- "openspec/changes/$change_name"
else
  git add -A -- . ':(exclude).agent-runner'
fi
if git diff --cached --quiet; then
  git commit --allow-empty -m "[factory-feature] chore: mark $phase checkpoint" -m "Factory-Checkpoint: $phase"
else
  git commit -m "[factory-feature] chore: complete $phase phase" -m "Factory-Checkpoint: $phase"
fi
if [ "$phase" != implemented ]; then
  git push origin "HEAD:refs/heads/$branch"
  exit 0
fi
push_stderr=$(mktemp)
trap 'rm -f "$push_stderr"' EXIT
if git push origin "HEAD:refs/heads/$branch" 2>"$push_stderr"; then
  cat "$push_stderr" >&2
  exit 0
else
  push_status=$?
fi
cat "$push_stderr" >&2
if [ -z "$artifact_dir" ] || ! grep -Eq 'refusing to allow .* to create or update workflow .* without .*workflow.* scope' "$push_stderr"; then
  exit "$push_status"
fi
python3 - "$artifact_dir" "$branch" <<'PYWORKFLOW'
import json
import subprocess
import sys
from pathlib import Path

artifact_dir = Path(sys.argv[1])
branch = sys.argv[2]

def git(*args):
    return subprocess.check_output(['git', *args])

# Implementors commit their work before this script adds the checkpoint.
# Fetch the pushed branch so all unpushed implementation commits are covered.
subprocess.run(['git', 'fetch', 'origin', 'refs/heads/' + branch], check=True)
base = git('rev-parse', 'FETCH_HEAD').decode().strip()
subprocess.run(['git', 'merge-base', '--is-ancestor', base, 'HEAD'], check=True)
paths = [
    path.decode() for path in git(
        'diff', '--name-only', '-z', '--no-renames', base, 'HEAD',
        '--', '.github/workflows'
    ).split(b'\0') if path
]
if not paths:
    raise SystemExit('workflow scope rejection without workflow changes since the last pushed checkpoint')
patch = git('diff', '--binary', base, 'HEAD', '--', '.github/workflows')
artifact_dir.mkdir(parents=True, exist_ok=True)
patch_path = artifact_dir / 'workflow-changes.patch'
patch_path.write_bytes(patch)
original_head = git('rev-parse', 'HEAD').decode().strip()
message = git('log', '-1', '--format=%B')
# A revert on top still pushes workflow-touching commits. Replace only the
# unpushed history with one checkpoint containing the non-workflow changes.
subprocess.run(['git', 'reset', '--soft', base], check=True)
for path in paths:
    exists = subprocess.run(
        ['git', 'cat-file', '-e', base + ':' + path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if exists:
        git('checkout', base, '--', path)
    else:
        git('rm', '-f', '--', path)
subprocess.run(['git', 'commit', '--allow-empty', '-F', '-'], input=message, check=True)
push = subprocess.run(['git', 'push', 'origin', 'HEAD:refs/heads/' + branch])
if push.returncode:
    # Keep the rejected implementation intact if the handoff push also fails.
    subprocess.run(['git', 'reset', '--hard', original_head], check=True)
    raise SystemExit(push.returncode)
patch_text = patch.decode(errors='replace')
if len(patch_text) > 12000:
    patch_text = patch_text[:12000] + '\n[truncated; full patch saved at ' + str(patch_path) + ']'
explanation = (
    'The push credential lacks workflow scope for ' + ', '.join(paths) + '. '
    'A writer must apply the CI change or grant workflow scope. '
    'The saved patch is at ' + str(patch_path) + ':\n\n' + patch_text
)
(artifact_dir / 'feature-outcome.json').write_text(json.dumps({
    'contract': 'factory-feature/1',
    'outcome': 'needs-input',
    'stopped_step': 'archive',
    'branch': branch,
    'reasons': [explanation],
    'questions': [explanation],
    'direction_summary': 'Implementation is complete and pushed without its .github/workflows changes. Apply the patch on the target branch (or commit it to this branch with a credential that has workflow scope), then comment on the issue: the next attempt merges the target branch and resumes at archive.',
}) + '\n')
PYWORKFLOW

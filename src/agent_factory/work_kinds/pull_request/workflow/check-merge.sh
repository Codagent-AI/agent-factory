#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  artifact_dir=$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["artifact_dir"])')
else
  artifact_dir=$1
fi
python3 - "$artifact_dir" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

artifacts = Path(sys.argv[1])
conflict = json.loads((artifacts / 'merge-conflict.json').read_text())
before, base = conflict['pre_merge_head'], conflict['base_head']

def git(*args, check=True):
    result = subprocess.run(['git', *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result

def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)

try:
    require(not git('rev-parse', '-q', '--verify', 'MERGE_HEAD', check=False).stdout.strip(), 'merge is unfinished')
    require(not git('diff', '--name-only', '--diff-filter=U').stdout.strip(), 'unmerged paths remain')
    require(not git('status', '--porcelain').stdout.strip(), 'working tree is dirty')
    conflicted = set(conflict['conflicted'])
    require(bool(conflicted), 'conflict list is empty')
    markers = git('grep', '-nE', r'^(<<<<<<<|=======|>>>>>>>)( |$)', '--', *sorted(conflicted), check=False)
    require(markers.returncode == 1, 'conflict markers remain')
    for sha in (before, base):
        require(git('merge-base', '--is-ancestor', sha, 'HEAD', check=False).returncode == 0, f'{sha} is not an ancestor of HEAD')
    chain = git('rev-list', '--first-parent', 'HEAD').stdout.splitlines()
    require(before in chain, 'pre-merge head is not on the first-parent chain')
    index = chain.index(before)
    require(index > 0, 'no resolution commit follows the pre-merge head')
    resolution = chain[index - 1]
    parents = git('show', '-s', '--format=%P', resolution).stdout.strip().split()
    require(parents == [before, base], 'resolution merge parents differ from the expected heads')
    automatic = git('merge-tree', '--write-tree', before, base, check=False)
    tree = automatic.stdout.splitlines()[0] if automatic.stdout else ''
    require(len(tree) == 40 and all(c in '0123456789abcdef' for c in tree), 'cannot compute automatic merge tree')
    changed = set(git('diff', '--name-only', tree, resolution).stdout.splitlines())
    require(changed <= conflicted, f'resolution changed non-conflicted paths: {sorted(changed - conflicted)}')
    record = json.loads((artifacts / 'base-merge.json').read_text())
    record.update(status='resolved', merge_commit=resolution, follow_up_commits=list(reversed(chain[:index - 1])))
    (artifacts / 'base-merge.json').write_text(json.dumps(record) + '\n')
except (RuntimeError, KeyError, OSError, ValueError) as error:
    print(f'check-merge: {error}', file=sys.stderr)
    sys.exit(1)
PY

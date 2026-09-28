#!/bin/sh
# Source this file, then call merge_base SOURCE ADMISSION BRANCH ARTIFACTS RESUME PRIOR.
merge_base() {
  merge_source=$1
  merge_admission=$2
  merge_branch=$3
  merge_artifacts=$4
  merge_resume=${5:-}
  merge_prior=${6:-}
  merge_before=$(git rev-parse HEAD)
  merge_status=current
  merge_commit=''
  if ! git merge-base --is-ancestor "$merge_source" HEAD; then
    merge_status=merged
    if git merge --no-ff --no-edit -m "[factory-feature] chore: merge $(printf %.7s "$merge_source") from the target branch into $merge_branch" "$merge_source" >&2; then
      merge_commit=$(git rev-parse HEAD)
    else
      git rev-parse -q --verify MERGE_HEAD >/dev/null || return 1
      merge_status=conflict
      git diff --name-only --diff-filter=U > "$merge_artifacts/conflicted-files.txt"
      python3 - "$merge_artifacts/merge-conflict.json" "$merge_source" "$merge_before" "$merge_resume" "$merge_prior" "$merge_artifacts/conflicted-files.txt" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'base_head': sys.argv[2], 'pre_merge_head': sys.argv[3], 'resume_from': sys.argv[4], 'prior_branch': sys.argv[5], 'conflicted': Path(sys.argv[6]).read_text().splitlines()}) + '\n')
PY
      rm "$merge_artifacts/conflicted-files.txt"
    fi
  fi
  python3 - "$merge_artifacts/base-merge.json" "$merge_admission" "$merge_source" "$merge_before" "$merge_status" "$merge_commit" <<'PY'
import json, sys
from pathlib import Path
value = {'target_at_admission': sys.argv[2], 'base_head': sys.argv[3], 'pre_merge_head': sys.argv[4], 'status': sys.argv[5]}
if sys.argv[6]:
    value['merge_commit'] = sys.argv[6]
Path(sys.argv[1]).write_text(json.dumps(value) + '\n')
PY
}

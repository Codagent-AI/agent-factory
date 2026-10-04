#!/bin/sh
set -eu

# Prints one top-level field of review.json, or "true"/"false" when `equals` is given.
# A script step receives its inputs as JSON on stdin, so no review data is interpolated
# into shell text, and it resolves beside the workflow in both the sandbox and host catalogs.

payload=$(cat)
PAYLOAD="$payload" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["PAYLOAD"])
with open(data["review_file"]) as handle:
    value = json.load(handle).get(data["field"])
# review.json always carries kind and head_sha; a record without them must stop the round
# rather than quietly read as a non-task round.
if not isinstance(value, str) or not value:
    raise SystemExit(f"review-field: review.json has no {data['field']} string")
expected = data.get("equals")
print(str(value == expected).lower() if expected is not None else value, end="")
PY

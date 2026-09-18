#!/bin/sh
set -eu
python3 -c 'import json,sys; p=json.load(sys.stdin); d=json.loads(p["decision"]); print(str(any(i.get("decision") == "change" for i in d.get("items", []))).lower())'

#!/bin/sh
set -eu
python3 -c 'import json,os,sys; p=json.load(sys.stdin); d=json.loads(p["decision"]); o={"contract":"factory-review/1","outcome":"needs-input" if d.get("needs_input") else "pull-request","reasons":d.get("needs_input",[]),"answered":[i.get("id") for i in d.get("items",[])],"changed":[i.get("id") for i in d.get("items",[]) if i.get("decision")=="change"]}; os.makedirs(os.path.dirname(p["outcome_path"]),exist_ok=True); json.dump(o,open(p["outcome_path"],"w"));'

import json, yaml
from collections import deque

with open('ncof_json/1_NncofEventsSubscription_from_PCF_to_NCOF_v0.1.json', 'r', encoding='utf-8') as f:
    j1 = json.load(f)
with open('ncof_json/2_NncofEventsSubscription_from_RICF_to_NCOF_v0.1.json', 'r', encoding='utf-8') as f:
    j2 = json.load(f)

def collect_json_keys(obj, keys):
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            collect_json_keys(v, keys)
    elif isinstance(obj, list):
        for it in obj:
            collect_json_keys(it, keys)

json_keys = set()
collect_json_keys(j1, json_keys)
collect_json_keys(j2, json_keys)

with open('Nncof_EventsSubscription_PoC_ETRI_DoDo1.yaml', 'r', encoding='utf-8') as f:
    y = yaml.safe_load(f)

schema_props = set()
visited = set()
schemas = y.get('components', {}).get('schemas', {})

for _, schema in schemas.items():
    dq = deque([schema])
    while dq:
        s = dq.popleft()
        sid = id(s)
        if sid in visited:
            continue
        visited.add(sid)
        if isinstance(s, dict):
            props = s.get('properties')
            if isinstance(props, dict):
                for p in props.keys():
                    schema_props.add(p)
            if 'items' in s:
                dq.append(s['items'])
            for comp in ('allOf','anyOf','oneOf'):
                if comp in s and isinstance(s[comp], list):
                    dq.extend(s[comp])
            for v in s.values():
                if isinstance(v, (dict, list)):
                    dq.append(v)
        elif isinstance(s, list):
            dq.extend(s)

used = sorted(k for k in json_keys if k in schema_props)
unused = sorted(k for k in json_keys if k not in schema_props)

print(f"Total JSON unique keys: {len(json_keys)}")
print(f"Schema-defined property names: {len(schema_props)}")
print(f"Used (present in YAML schemas): {len(used)}")
print(f"Unused (not present in YAML schemas): {len(unused)}")
if used or unused:
    used_pct = (len(used) / (len(used)+len(unused))) * 100
    print(f"Used ratio: {used_pct:.1f}% | Unused ratio: {100-used_pct:.1f}%")

for name, jobj in [("PCF", j1),("RICF", j2)]:
    keys=set(); collect_json_keys(jobj, keys)
    u = sum(1 for k in keys if k in schema_props)
    nu = sum(1 for k in keys if k not in schema_props)
    pct = (u/(u+nu))*100 if (u+nu)>0 else 0
    print(f"{name}: total={len(keys)}, used={u}, unused={nu}, used%={pct:.1f}%")

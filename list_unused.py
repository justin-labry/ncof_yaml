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

json_keys = set(); collect_json_keys(j1, json_keys); collect_json_keys(j2, json_keys)

with open('Nncof_EventsSubscription_PoC_ETRI_DoDo1.yaml', 'r', encoding='utf-8') as f:
    y = yaml.safe_load(f)

schema_props = set(); visited=set()
schemas = y.get('components', {}).get('schemas', {})

q = deque(schemas.values())
while q:
    s = q.popleft()
    if id(s) in visited: continue
    visited.add(id(s))
    if isinstance(s, dict):
        props = s.get('properties')
        if isinstance(props, dict):
            schema_props.update(props.keys())
        for v in s.values():
            if isinstance(v, (dict,list)): q.append(v)
    elif isinstance(s, list):
        q.extend(s)

unused = sorted(k for k in json_keys if k not in schema_props)
print('\n'.join(unused[:10]))

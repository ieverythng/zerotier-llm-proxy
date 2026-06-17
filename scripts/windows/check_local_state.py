import json
import os

path = os.path.join(os.environ['LOCALAPPDATA'], 'Google', 'Chrome', 'User Data', 'Local State')
with open(path, 'r') as f:
    d = json.load(f)

print("Keys:", list(d.keys()))
for k, v in d.items():
    if isinstance(v, str):
        print(f"  {k}: {v[:100]}...")
    else:
        print(f"  {k}: {v}")

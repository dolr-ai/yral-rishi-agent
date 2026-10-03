"""Copy Redis keys matching a pattern between two Redis servers, via stdout/stdin.

  export: python redis_keys.py export 'caddy*'   -> one JSON line per key
  import: python redis_keys.py import            <- those lines, RESTORE ... REPLACE

Password comes from env REDIS_PASSWORD; host is redis-primary (same alias on
both swarms). DUMP/RESTORE keeps each value's exact type and encoding, and the
remaining TTL is carried over. Prints counts only, never values.
"""

import base64
import json
import os
import sys

import redis

r = redis.Redis(host="redis-primary", port=6379, password=os.environ["REDIS_PASSWORD"])

if sys.argv[1] == "export":
    n = 0
    for key in r.scan_iter(match=sys.argv[2], count=1000):
        ttl = r.pttl(key)
        print(
            json.dumps(
                {
                    "k": base64.b64encode(key).decode(),
                    "ttl": max(ttl, 0),
                    "v": base64.b64encode(r.dump(key)).decode(),
                }
            )
        )
        n += 1
    print(f"exported {n} keys", file=sys.stderr)
else:
    n = 0
    for line in sys.stdin:
        d = json.loads(line)
        r.restore(
            base64.b64decode(d["k"]), d["ttl"], base64.b64decode(d["v"]), replace=True
        )
        n += 1
    print(f"imported {n} keys", file=sys.stderr)

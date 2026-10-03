#!/usr/bin/env python3
"""Runs ON the new swarm's manager. Recreates one live production service.

stdin, one JSON document per line:
  1. `docker service inspect <svc> --format '{{json .}}'` from a prod manager
  2. {"<secret file name>": "<base64>"} read inside a running prod task
  3. {"<config name>": "<base64>"} from `docker config inspect` on a prod manager
  4. {"<prod network id>": "<network name>"}

What changes on the way over:
  - Postgres addresses (patroni-rishi-N, pgbouncer) -> postgres-primary, in env
    AND secrets: postgres-primary only ever points at the Patroni leader.
  - Placement pins are dropped; more than one copy -> at most one per server.
Nothing secret is printed. Secrets/configs are created only if missing.
"""

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

spec = json.loads(sys.stdin.readline())["Spec"]
secret_values = json.loads(sys.stdin.readline())
config_values = json.loads(sys.stdin.readline())
net_names = json.loads(sys.stdin.readline())
cs = spec["TaskTemplate"]["ContainerSpec"]
name = spec["Name"]

PG = re.compile(
    rb"(?:(?:yral-v2-patroni_)?patroni-rishi-[456]|(?:yral-v2-patroni_)?pgbouncer)(?::\d+)?"
    rb"(?:,(?:(?:yral-v2-patroni_)?patroni-rishi-[456]|(?:yral-v2-patroni_)?pgbouncer)(?::\d+)?)*"
)
rewrites = 0


def to_primary(value: bytes) -> bytes:
    global rewrites
    new, n = PG.subn(b"postgres-primary:5432", value)
    rewrites += n
    return new


def run(*args, stdin=None):
    return subprocess.run(list(args), input=stdin, capture_output=True, check=True)


def exists(kind, obj):
    return (
        subprocess.run(["docker", kind, "inspect", obj], capture_output=True).returncode
        == 0
    )


args = ["docker", "service", "create", "--detach", "--quiet", "--name", name]

for s in cs.get("Secrets", []):
    target = s["File"]["Name"]
    if not exists("secret", s["SecretName"]):
        run(
            "docker",
            "secret",
            "create",
            s["SecretName"],
            "-",
            stdin=to_primary(base64.b64decode(secret_values[target])),
        )
    args += ["--secret", f"source={s['SecretName']},target={target}"]

for c in cs.get("Configs", []):
    if not exists("config", c["ConfigName"]):
        run(
            "docker",
            "config",
            "create",
            c["ConfigName"],
            "-",
            stdin=base64.b64decode(config_values[c["ConfigName"]]),
        )
    args += ["--config", f"source={c['ConfigName']},target={c['File']['Name']}"]

for m in cs.get("Mounts", []):
    args += [
        "--mount",
        f"type={m['Type']},source={m['Source']},target={m['Target']}"
        + (",readonly" if m.get("ReadOnly") else ""),
    ]

for n in spec["TaskTemplate"].get("Networks", []):
    net = net_names[n["Target"]]
    args += [
        "--network",
        ",".join([f"name={net}"] + [f"alias={a}" for a in n.get("Aliases") or []]),
    ]

for p in (spec.get("EndpointSpec") or {}).get("Ports", []):
    args += [
        "--publish",
        f"published={p['PublishedPort']},target={p['TargetPort']},"
        f"protocol={p.get('Protocol', 'tcp')},mode={p.get('PublishMode', 'ingress')}",
    ]

replicas = (spec["Mode"].get("Replicated") or {}).get("Replicas", 1)
args += ["--replicas", str(replicas), "--placement-pref", "spread=node.labels.site"]
if replicas > 1:
    args += ["--replicas-max-per-node", "1"]

hc = cs.get("Healthcheck")
if hc and hc.get("Test") and hc["Test"][0] in ("CMD", "CMD-SHELL"):
    args += [
        "--health-cmd",
        hc["Test"][1] if hc["Test"][0] == "CMD-SHELL" else shlex.join(hc["Test"][1:]),
    ]
    for key, flag in (
        ("Interval", "--health-interval"),
        ("Timeout", "--health-timeout"),
        ("StartPeriod", "--health-start-period"),
    ):
        if hc.get(key):
            args += [flag, f"{hc[key] // 1_000_000_000}s"]
    if hc.get("Retries"):
        args += ["--health-retries", str(hc["Retries"])]

if cs.get("Hostname"):
    args += ["--hostname", cs["Hostname"]]
if cs.get("User"):
    args += ["--user", cs["User"]]
if cs.get("Dir"):
    args += ["--workdir", cs["Dir"]]

fd, env_file = tempfile.mkstemp()
try:
    with os.fdopen(fd, "wb") as f:
        for line in cs.get("Env", []):
            f.write(to_primary(line.encode()) + b"\n")
    args += ["--env-file", env_file]
    assert not cs.get("Command"), (
        "entrypoint overrides aren't handled; clone this one by hand"
    )
    args.append(cs["Image"].split("@")[0])
    args += cs.get("Args") or []
    run(*args)
finally:
    os.unlink(env_file)

print(
    f"{name}: created | secrets {len(cs.get('Secrets', []))} configs {len(cs.get('Configs', []))} "
    f"mounts {len(cs.get('Mounts', []))} replicas {replicas} | postgres addresses rewritten: {rewrites}"
)

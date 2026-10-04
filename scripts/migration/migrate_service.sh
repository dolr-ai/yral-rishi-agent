#!/bin/bash
# Clone one production service onto the new swarm via clone_service.py.
# Usage: migrate_service.sh <service-name>
set -euo pipefail
SVC=$1
MGR=rishi-deploy@138.201.128.108
NEW=rishi@20.219.36.211
S=$(cd "$(dirname "$0")" && pwd)
ssh_node() {  # ssh to a Hetzner node by swarm hostname
  case $1 in
    rishi-1) echo "-i $HOME/.ssh/rishi-hetzner-ci-key -o IdentitiesOnly=yes deploy@138.201.137.181" ;;
    rishi-2) echo "-i $HOME/.ssh/rishi-hetzner-ci-key -o IdentitiesOnly=yes deploy@136.243.150.84" ;;
    rishi-3) echo "-i $HOME/.ssh/rishi-hetzner-ci-key -o IdentitiesOnly=yes deploy@136.243.147.225" ;;
    rishi-4) echo "rishi-deploy@138.201.128.108" ;;
    rishi-5) echo "rishi-deploy@88.99.160.251" ;;
    rishi-6) echo "rishi-deploy@162.55.88.112" ;;
  esac
}
READ=$SVC
NODE=$(ssh -o BatchMode=yes $MGR "docker service ps $SVC --filter desired-state=running --format '{{.Node}}' | head -1")
if [ -z "$NODE" ]; then
  # The service is stopped (cutover). Mount the same secrets into a throwaway
  # container that runs nothing but sleep: no network, no app, exits by itself.
  READ=cutover-secrets-$(echo "$SVC" | tr '_' '-' | cut -c1-40)
  ssh -o BatchMode=yes $MGR "args=\$(docker service inspect $SVC --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}--secret source={{.SecretName}},target={{.File.Name}} {{end}}');
    docker service create --quiet --detach=false --name $READ --restart-condition none \$args alpine sleep 900 >/dev/null" \
    || { echo "$SVC: could not start a secret reader"; exit 1; }
  NODE=$(ssh -o BatchMode=yes $MGR "docker service ps $READ --filter desired-state=running --format '{{.Node}}' | head -1")
fi
scp -q "$S/clone_service.py" $NEW:stacks/clone_service.py
{
  ssh -o BatchMode=yes $MGR "docker service inspect $SVC --format '{{json .}}'"
  # Secret values, read where a task runs. Printed as base64 JSON into the pipe only.
  ssh -o BatchMode=yes $(ssh_node "$NODE") "c=\$(docker ps -q -f name=$READ. | head -1); docker exec \$c sh -c 'cd /run/secrets 2>/dev/null && for f in *; do [ -f \"\$f\" ] && printf \"%s\t%s\n\" \"\$f\" \"\$(base64 \"\$f\" | tr -d \"\\n\")\"; done; true'" \
    | python3 -c 'import sys,json; print(json.dumps(dict(l.rstrip("\n").split("\t",1) for l in sys.stdin if "\t" in l)))'
  ssh -o BatchMode=yes $MGR "for c in \$(docker service inspect $SVC --format '{{range .Spec.TaskTemplate.ContainerSpec.Configs}}{{.ConfigName}} {{end}}'); do printf '%s\t' \$c; docker config inspect \$c --format '{{json .Spec.Data}}'; done" \
    | python3 -c 'import sys,json; print(json.dumps({k: json.loads(v) for k,v in (l.rstrip("\n").split("\t",1) for l in sys.stdin if "\t" in l)}))'
  ssh -o BatchMode=yes $MGR "docker network ls --no-trunc --filter driver=overlay --format '{{.ID}} {{.Name}}'" \
    | python3 -c 'import sys,json; print(json.dumps(dict(l.split() for l in sys.stdin if l.strip())))'
} | ssh -o BatchMode=yes $NEW "python3 ~/stacks/clone_service.py"

#!/bin/sh
# Runs inside the proot rootfs and stays in the foreground for the lifetime of
# the app: the gateway is this process, so killing it stops the server.
set -e
export PATH=/opt/python/bin:$PATH
export PYTHONPATH=/opt

echo STEP-DEPS
if ! python3 -c "import pydantic, paramiko, tiktoken" >/dev/null 2>&1; then
  pip install --no-cache-dir --disable-pip-version-check --progress-bar off -r /tmp/requirements.txt
  echo STEP-DEPS-OK
else
  echo STEP-DEPS-CACHED
fi

# The Ubuntu base image ships no CA store, and the model client verifies TLS
# against the system one: every provider call would fail as a connection error
# while plain HTTP keeps working. certifi's bundle comes with httpx.
echo STEP-TLS
python3 - <<'PY'
import pathlib
import shutil

import certifi

bundle = pathlib.Path("/etc/ssl/cert.pem")
bundle.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(certifi.where(), bundle)
print("ca bundle:", bundle.stat().st_size, "bytes")
PY

# The sandbox on a phone is Podroid's Linux VM: there is no hypervisor for the
# desktop backend to drive, so the config always points at the host's control
# endpoint. Everything else the user set is preserved.
echo STEP-CONFIG
python3 - <<'PY'
import json
import pathlib

path = pathlib.Path("/root/.yoreni/config.json")
path.parent.mkdir(parents=True, exist_ok=True)
config = json.loads(path.read_text()) if path.is_file() else {}
sandbox = config.setdefault("tools", {}).setdefault("sandbox", {})
sandbox.update(
    backend="podroid",
    podroid_host="127.0.0.1",
    podroid_port=9922,
    podroid_control_url="http://127.0.0.1:8766/podroid",
)
path.write_text(json.dumps(config, indent=2) + "\n")
print("sandbox backend:", sandbox["backend"])
PY

# The app owns this container: every launch starts a new proot tree, so a run
# state left behind by the previous one describes a process that is gone. The
# state file compares PIDs, and Android has long since handed that number to
# some other app, whose process the rootfs cannot inspect; the gateway then
# reads the record as "already running" and refuses to start. Nothing inside
# the rootfs outlives the app, so the record is dropped before it can block.
echo STEP-SERVE
rm -f /root/.yoreni/run/gateway.json
cd /opt
cat > /opt/serve.py <<'PY'
import sys

sys.argv = ["yoreni", "gateway", "--port", "8080"]
from yoreni.cli.entry import main

main()
PY
exec python3 /opt/serve.py

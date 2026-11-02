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

echo STEP-SANDBOX
# Podroid may still be missing or its VM still off: the WebUI walks the owner
# through both, so a sandbox that is not up yet must never stop the gateway
# from serving and reporting why.
python3 - <<'PY' || true
import asyncio
import time

from yoreni.config.loader import load_config
from yoreni.sandbox.errors import SandboxUnavailableError
from yoreni.sandbox.podroid_backend import PodroidBackend


async def main() -> None:
    config = load_config().tools.sandbox
    backend = PodroidBackend(config)
    started = time.time()
    try:
        await backend.prepare()
        result = await backend.exec("uname -m; . /etc/os-release && echo $PRETTY_NAME", cwd="/")
        print("sandbox exec (%0.1fs):" % (time.time() - started),
              result.stdout.strip().replace("\n", " | "))
        info = await backend.stat("/workspace")
        print("sandbox stat /workspace:", None if info is None else info.is_dir)
    except SandboxUnavailableError as exc:
        print("sandbox not ready yet:", exc)
    finally:
        await backend.close()


asyncio.run(main())
PY

echo STEP-SERVE
cd /opt
cat > /opt/serve.py <<'PY'
import sys

sys.argv = ["yoreni", "gateway", "--port", "8080"]
from yoreni.cli.entry import main

main()
PY
exec python3 /opt/serve.py

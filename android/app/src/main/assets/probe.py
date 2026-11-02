"""Run inside the proot rootfs: prove the package graph loads on Android."""

import sys
import time

sys.path.insert(0, "/opt")

started = time.time()
import yoreni  # noqa: E402

print("yoreni", getattr(yoreni, "__version__", "unknown"), "in %.1fs" % (time.time() - started))

MODULES = [
    "yoreni.config.loader",
    "yoreni.agent",
    "yoreni.webui.gateway_endpoint",
    "yoreni.channels.websocket.runtime",
    "yoreni.sandbox.gateway",
    "yoreni.sandbox.podroid_backend",
    "yoreni.sandbox.provision",
    "yoreni.cli.entry",
]

for name in MODULES:
    mark = time.time()
    __import__(name)
    print("ok %-40s %.2fs" % (name, time.time() - mark))

print("all modules imported")

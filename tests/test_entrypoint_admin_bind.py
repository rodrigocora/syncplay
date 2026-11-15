"""The entrypoint must start the admin on 0.0.0.0 inside the container.

Docker port mapping targets the container network IP, so a 127.0.0.1 bind
inside the container makes the published 127.0.0.1:12347 mapping receive
connections that nothing answers (empty reply). Host-side exposure is
restricted by the compose "127.0.0.1:12347:12347" mapping, not by the bind.
"""
import re
from pathlib import Path


def test_admin_binds_all_interfaces_in_container():
    entrypoint = Path(__file__).resolve().parent.parent / "entrypoint.sh"
    text = entrypoint.read_text(encoding="utf-8")
    m = re.search(r"admin_cmd\s*=\s*\[(.*?)\]", text)
    assert m, "admin_cmd not found in entrypoint.sh"
    assert '"--host", "0.0.0.0"' in m.group(1)

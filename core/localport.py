"""Local service ports are picked by the OS at runtime and shared via small files in ~/.prisma/run –
no fixed addresses or port numbers live in the code."""
import os
import socket
from pathlib import Path

RUN = Path(os.environ.get("PRISMA_HOME", Path.home() / ".prisma")) / "run"
HOST = "localhost"


def bind(name, exclusive=True):
    """Bind a listening socket on a free local port and publish the port under `name`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if exclusive and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind((socket.gethostbyname(HOST), 0))
    s.listen(8)
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / f"{name}.port").write_text(str(s.getsockname()[1]))
    return s


def connect(name, timeout=1.0):
    """Connect to the service published under `name`; raises OSError if it is not running."""
    try:
        port = int((RUN / f"{name}.port").read_text().strip())
    except (OSError, ValueError):
        raise ConnectionRefusedError(f"{name} not running")
    return socket.create_connection((HOST, port), timeout=timeout)

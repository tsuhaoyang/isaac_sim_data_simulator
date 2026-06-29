"""TCP socket gateway for real machines (SPEC §2.2 / §6.1, milestone M4).

A real machine (or PLC bridge) opens a TCP connection and streams newline-delimited
JSON packets. The gateway parses + normalizes each packet to a MachineStateEvent and
hands it to the SAME ingestion path the MQTT source uses, so fake vs real is
indistinguishable downstream. Protocol: docs/integration_real_machines.md.
"""

import json
import socketserver
import threading
from typing import Callable

from isaac_common.schemas import MachineStateEvent


def parse_packet(data: dict) -> MachineStateEvent:
    """Validate/normalize one packet. Extra fields ignored; ts filled if absent. Pure -> testable."""
    return MachineStateEvent.model_validate(data)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        gw: "SocketGateway" = self.server.gateway  # type: ignore[attr-defined]
        peer = f"{self.client_address[0]}:{self.client_address[1]}"
        gw.log.info("machine socket connected: %s", peer)
        try:
            for raw in self.rfile:                  # line-delimited framing
                line = raw.decode(errors="replace").strip()
                if line:
                    gw._dispatch(line, peer)
        finally:
            gw.log.info("machine socket disconnected: %s", peer)


class SocketGateway:
    def __init__(self, host: str, port: int, on_event: Callable[[MachineStateEvent], None], log):
        self.host = host
        self.port = port
        self.on_event = on_event
        self.log = log
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    def _dispatch(self, line: str, peer: str) -> None:
        try:
            ev = parse_packet(json.loads(line))
        except Exception as exc:
            self.log.warning("bad packet from %s: %s (%s)", peer, line[:120], exc)
            return
        self.on_event(ev)

    def start(self) -> None:
        self._server = _Server((self.host, self.port), _Handler)
        self._server.gateway = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]  # resolve if port was 0
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.log.info("socket gateway listening on %s:%d", self.host, self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

import json
import logging
import socket
import threading
import time

import pytest

from isaac_common.schemas import MachineState
from socket_gateway import SocketGateway, parse_packet


def test_parse_packet_normalizes_and_fills_ts():
    ev = parse_packet({"machine_id": "M07", "state": "working", "product_id": "P1", "remaining_s": 5})
    assert ev.machine_id == "M07"
    assert ev.state == MachineState.WORKING
    assert ev.product_id == "P1"
    assert ev.ts > 0  # filled in


def test_parse_packet_rejects_bad_state():
    with pytest.raises(Exception):
        parse_packet({"machine_id": "M07", "state": "nonsense"})


def test_gateway_receives_and_dispatches_packets():
    received = []
    done = threading.Event()

    def on_event(ev):
        received.append(ev)
        done.set()

    gw = SocketGateway("127.0.0.1", 0, on_event, logging.getLogger("test"))
    gw.start()
    try:
        with socket.create_connection(("127.0.0.1", gw.port), timeout=2) as s:
            s.sendall((json.dumps({"machine_id": "M01", "state": "done", "product_id": "P9"}) + "\n").encode())
            s.sendall(b"not-json\n")  # malformed line must be skipped, connection stays up
            s.sendall((json.dumps({"machine_id": "M01", "state": "empty"}) + "\n").encode())
            assert done.wait(2.0)
            time.sleep(0.1)
    finally:
        gw.stop()

    states = [e.state.value for e in received]
    assert "done" in states and "empty" in states
    assert all(e.machine_id == "M01" for e in received)

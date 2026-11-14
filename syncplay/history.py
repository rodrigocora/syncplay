import argparse
import json
import time

# Runs as a script (python3 /app/syncplay/history.py), so the package is not on
# sys.path; import the shared backend as a sibling module.
import db
from twisted.internet import reactor
from twisted.internet.endpoints import TCP4ClientEndpoint, TCP4ServerEndpoint
from twisted.internet.protocol import Factory, Protocol


class RecordingState:
    def __init__(self, store):
        self._store = store
        self._username = None
        self._room = None
        self._file = None
        self._size = None
        self._duration = None
        self._lastKnownPosition = 0.0

    def consumeLine(self, line):
        try:
            message = json.loads(line.decode("utf-8").strip())
        except (ValueError, UnicodeDecodeError):
            return
        now = int(time.time())
        for command, payload in message.items():
            if command == "Hello":
                self._username = payload.get("username")
                if self._username:
                    self._store.upsert_user(self._username, now)
                # the room name is in the Hello; a real client usually never
                # sends a separate Set.room, so capture it here
                room = payload.get("room")
                if isinstance(room, dict) and room.get("name"):
                    self._room = room.get("name")
            elif command == "Set":
                for key, value in payload.items():
                    if key == "room" and isinstance(value, dict):
                        self._room = value.get("name")
                    elif key == "file" and isinstance(value, dict):
                        self._file = value.get("name")
                        self._size = value.get("size")
                        self._duration = value.get("duration")
            elif command == "State":
                playstate = payload.get("playstate", {})
                position = playstate.get("position")
                if position is not None:
                    self._lastKnownPosition = position
                if self._file and self._room:
                    self._store.upsert_watch(
                        self._room, self._file, self._size, position, self._duration, now
                    )

    def onDisconnect(self):
        if self._room and self._file:
            self._store.record_stopped(self._room, self._file, self._lastKnownPosition, int(time.time()))


class UpstreamProtocol(Protocol):
    def __init__(self, clientProtocol):
        self._client = clientProtocol

    def connectionMade(self):
        self._client._onUpstreamConnected(self)

    def dataReceived(self, data):
        self._client._sendToClient(data)

    def connectionLost(self, reason):
        self._client._teardown()


class UpstreamFactory(Factory):
    def __init__(self, clientProtocol):
        self._clientProtocol = clientProtocol

    def buildProtocol(self, addr):
        return UpstreamProtocol(self._clientProtocol)


class ClientProtocol(Protocol):
    def __init__(self, host, port, store):
        self._host = host
        self._port = port
        self._store = store
        self._state = RecordingState(store)
        self._upstream = None
        self._buffer = b""
        self._lineBuffer = b""

    def connectionMade(self):
        try:
            TCP4ClientEndpoint(reactor, self._host, self._port).connect(
                UpstreamFactory(self)
            )
        except OSError:
            self.transport.loseConnection()

    def _onUpstreamConnected(self, upstream):
        self._upstream = upstream
        if self._buffer:
            self._upstream.transport.write(self._buffer)
            self._buffer = b""
            self._record(b"")

    def _sendToClient(self, data):
        if self.transport and not self.transport.disconnecting:
            self.transport.write(data)

    def dataReceived(self, data):
        if self._upstream and self._upstream.transport and not self._upstream.transport.disconnecting:
            self._upstream.transport.write(data)
        else:
            self._buffer += data
        self._record(data)

    def _record(self, data):
        self._lineBuffer += data
        if b"\n" not in self._lineBuffer:
            return
        *complete, remainder = self._lineBuffer.split(b"\n")
        self._lineBuffer = remainder
        for line in complete:
            line = line.strip(b"\r")
            if line:
                self._state.consumeLine(line)

    def connectionLost(self, reason):
        self._state.onDisconnect()
        self._teardown()

    def _teardown(self):
        if self._upstream and self._upstream.transport and not self._upstream.transport.disconnecting:
            self._upstream.transport.loseConnection()


class ClientFactory(Factory):
    def __init__(self, host, port, store):
        self._host = host
        self._port = port
        self._store = store

    def buildProtocol(self, addr):
        return ClientProtocol(self._host, self._port, self._store)


def _checkpointLoop(store):
    while True:
        time.sleep(30)
        store.wal_checkpoint()


def run(proxyPort, host, port, dsn):
    store = db.open_store_with_retry(dsn)
    factory = ClientFactory(host, port, store)
    TCP4ServerEndpoint(reactor, proxyPort, interface="0.0.0.0").listen(factory)
    from twisted.internet import threads

    if store.dialect == "sqlite":
        threads.deferToThread(_checkpointLoop, store)
    print(f"history proxy listening on {proxyPort}, forwarding to {host}:{port}, dsn={db.redact_dsn(dsn)}")
    reactor.run()


def main():
    parser = argparse.ArgumentParser(description="Syncplay watch-history proxy")
    parser.add_argument("--port", type=int, default=12346, help="proxy listen port")
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=12345)
    parser.add_argument(
        "--dsn",
        default=None,
        help="database DSN. If omitted, resolves from DB_* env vars, then "
        "SYNCPLAY_DSN, then HISTORY_DSN, then the SQLite default",
    )
    args = parser.parse_args()
    dsn = db.resolve_dsn(args.dsn)
    run(args.port, args.upstream_host, args.upstream_port, dsn)


if __name__ == "__main__":
    main()

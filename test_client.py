#!/usr/bin/env python3
"""Synthetic Syncplay client for testing the watch-history proxy.

Speaks the minimal subset of the Syncplay protocol (CRLF-delimited JSON) that
the proxy records: Hello, Set.room, Set.file and State pings. Connects to the
proxy port (default 12346) and plays a short, deterministic watch session so
you can confirm a row lands in the database.

Usage (from the host, with the container running):
    python3 test_client.py
    python3 test_client.py --host 127.0.0.1 --port 12346 --username alice
"""
import argparse
import json
import socket
import time


def main():
    parser = argparse.ArgumentParser(description="Synthetic Syncplay test client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12346)
    parser.add_argument("--username", default="alice")
    parser.add_argument("--room", default="movie-room")
    parser.add_argument("--file", default="movie.mkv")
    parser.add_argument("--duration", type=float, default=3600)
    parser.add_argument("--size", type=int, default=123456)
    args = parser.parse_args()

    s = socket.create_connection((args.host, args.port), timeout=5)

    def send(msg):
        # the Syncplay protocol is CRLF-delimited JSON, not bare LF
        s.sendall((json.dumps(msg) + "\r\n").encode())

    # Hello: registers the username
    send({"Hello": {"username": args.username, "room": {"name": args.room}}})
    time.sleep(0.5)

    # Set room + file (as a client sends after receiving the server broadcast)
    send({"Set": {"room": {"name": args.room}}})
    send({"Set": {"file": {"name": args.file, "duration": args.duration, "size": args.size}}})
    time.sleep(0.5)

    # State pings: progress position
    for pos in (120.0, 180.5, 240.0):
        send({"State": {"playstate": {"position": pos, "paused": False}}})
        time.sleep(1.0)

    # keep the connection alive so the server does not time it out
    for _ in range(5):
        send({"State": {"playstate": {"position": 240.0, "paused": False}}})
        time.sleep(1.0)

    s.close()
    print("done")


if __name__ == "__main__":
    main()

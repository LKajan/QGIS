#!/usr/bin/env python3
"""TCP proxy that adds a fixed network latency, for benchmarking database clients.

Every chunk of data is forwarded half the round trip delay after it arrives, in
both directions. Ordering is kept and the proxy never waits for the other side,
so it models a long link with unlimited bandwidth (pipelined traffic is not
slowed down by more than the delay itself).

The proxy also counts *client turns*: the number of times the client sends data
after it has received data from the server. A client that waits for each reply
before sending the next request has one turn per request, so this is the number
of round trips the client waited for.

Standalone use (e.g. to try QGIS by hand against a "remote" database):

    python3 latency_proxy.py --target localhost:5432 --listen 55432 --delay 50

then connect QGIS to host=127.0.0.1 port=55432 sslmode=disable.

Controlled use (bench_addfeatures.py): with --control, the proxy prints one JSON
line {"event": "ready", "port": N} and then reads commands on stdin, one per
line, answering each with one JSON line on stdout:

    delay <ms>   set the added round trip time in milliseconds
    reset        set all counters to zero
    stats        return the counters
    quit         stop the proxy
"""

import argparse
import asyncio
import json
import socket
import sys


class Stats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.connections = 0
        self.client_turns = 0
        self.client_bytes = 0
        self.server_bytes = 0
        self.client_chunks = 0
        self.server_chunks = 0

    def as_dict(self):
        return {
            "connections": self.connections,
            "client_turns": self.client_turns,
            "client_bytes": self.client_bytes,
            "server_bytes": self.server_bytes,
            "client_chunks": self.client_chunks,
            "server_chunks": self.server_chunks,
        }


class Proxy:
    def __init__(self, target_host, target_port, rtt_ms):
        self.target_host = target_host
        self.target_port = target_port
        self.rtt_ms = rtt_ms
        self.stats = Stats()

    @property
    def one_way_delay(self):
        return self.rtt_ms / 2000.0

    async def handle_client(self, client_reader, client_writer):
        try:
            server_reader, server_writer = await asyncio.open_connection(
                self.target_host, self.target_port
            )
        except OSError as e:
            print(f"proxy: cannot connect to target: {e}", file=sys.stderr)
            client_writer.close()
            return

        for w in (client_writer, server_writer):
            sock = w.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        self.stats.connections += 1
        # True when server data was delivered to the client after its last send
        conn_state = {"client_received": True}

        await asyncio.gather(
            self.forward(client_reader, server_writer, conn_state, from_client=True),
            self.forward(server_reader, client_writer, conn_state, from_client=False),
            return_exceptions=True,
        )

    async def forward(self, reader, writer, conn_state, from_client):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()

        async def sender():
            while True:
                due, data = await queue.get()
                wait = due - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
                if not from_client:
                    conn_state["client_received"] = True
            try:
                if writer.can_write_eof():
                    writer.write_eof()
            except OSError:
                pass

        sender_task = asyncio.create_task(sender())
        try:
            while True:
                data = await reader.read(256 * 1024)
                if data:
                    if from_client:
                        self.stats.client_chunks += 1
                        self.stats.client_bytes += len(data)
                        if conn_state["client_received"]:
                            self.stats.client_turns += 1
                            conn_state["client_received"] = False
                    else:
                        self.stats.server_chunks += 1
                        self.stats.server_bytes += len(data)
                queue.put_nowait((loop.time() + self.one_way_delay, data))
                if not data:
                    break
        except OSError:
            queue.put_nowait((0, b""))
        await sender_task
        writer.close()

    async def control(self, server):
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        while True:
            line = await reader.readline()
            if not line:
                break
            cmd = line.decode().split()
            if not cmd:
                continue
            if cmd[0] == "delay" and len(cmd) == 2:
                self.rtt_ms = float(cmd[1])
                reply = {"ok": True, "rtt_ms": self.rtt_ms}
            elif cmd[0] == "reset":
                self.stats.reset()
                reply = {"ok": True}
            elif cmd[0] == "stats":
                reply = self.stats.as_dict()
            elif cmd[0] == "quit":
                print(json.dumps({"ok": True}), flush=True)
                break
            else:
                reply = {"ok": False, "error": f"unknown command: {line.decode()}"}
            print(json.dumps(reply), flush=True)
        server.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--target", default="localhost:5432", help="host:port of the real server"
    )
    parser.add_argument(
        "--listen", type=int, default=0, help="port to listen on (0 = any free port)"
    )
    parser.add_argument(
        "--delay", type=float, default=0, help="added round trip time in ms"
    )
    parser.add_argument(
        "--control", action="store_true", help="read commands from stdin"
    )
    args = parser.parse_args()

    host, _, port = args.target.rpartition(":")
    proxy = Proxy(host, int(port), args.delay)
    server = await asyncio.start_server(proxy.handle_client, "127.0.0.1", args.listen)
    listen_port = server.sockets[0].getsockname()[1]

    if args.control:
        print(json.dumps({"event": "ready", "port": listen_port}), flush=True)
        await proxy.control(server)
    else:
        print(
            f"Forwarding 127.0.0.1:{listen_port} -> {args.target} "
            f"with {args.delay} ms added round trip time",
            flush=True,
        )
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

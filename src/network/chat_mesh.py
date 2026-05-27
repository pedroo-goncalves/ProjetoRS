import asyncio
import json
import socket
from typing import Callable

from network.protocol import send_msg, recv_msg

CHAT_DISC_PORT = 50001  # UDP broadcast port — same on every machine


def _local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, callback: Callable[[dict], None]) -> None:
        self._cb = callback

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self._cb(json.loads(data.decode()))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    def error_received(self, exc) -> None:
        pass

    def connection_lost(self, exc) -> None:
        pass


class ChatMesh:
    """Full-mesh P2P chat for tournament players.

    Discovery : UDP broadcast on CHAT_DISC_PORT (no MQTT).
    Transport : direct TCP per pair, framed with send_msg/recv_msg.
    Convention: player with the smaller player_id connects OUT to the larger one,
                so each pair has exactly one TCP connection.
    """

    def __init__(self, player_id: str, tournament_id: str) -> None:
        self.player_id     = player_id
        self.tournament_id = tournament_id
        self._chat_addr:     str | None              = None
        self._server:        asyncio.Server | None   = None
        self._peers:         dict[str, asyncio.StreamWriter] = {}
        self._connecting:    set[str]                = set()
        self._tasks:         list[asyncio.Task]      = []
        self._disc_transport                         = None

        self.on_message:     Callable[[str, str], None] | None = None  # (sender_id, text)
        self.on_peer_change: Callable[[list[str]], None] | None = None # (peer_ids)

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the chat server, then broadcast our addr and listen for peers."""
        self._server = await asyncio.start_server(
            self._on_incoming, "0.0.0.0", 0
        )
        port = self._server.sockets[0].getsockname()[1]
        self._chat_addr = f"{_local_ip()}:{port}"

        await self._start_udp_listener()
        t = asyncio.create_task(self._broadcast_loop())
        self._tasks.append(t)

    async def send_all(self, text: str) -> None:
        """Broadcast a chat message to every connected peer."""
        msg  = {"command": "chat", "from": self.player_id, "text": text}
        dead = []
        for pid, writer in list(self._peers.items()):
            try:
                await send_msg(writer, msg)
            except Exception:
                dead.append(pid)
        for pid in dead:
            self._remove_peer(pid)

    async def stop(self) -> None:
        """Tear down everything: tasks, UDP, TCP connections, server."""
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

        if self._disc_transport:
            try:
                self._disc_transport.close()
            except Exception:
                pass
            self._disc_transport = None

        for writer in list(self._peers.values()):
            try:
                writer.close()
            except Exception:
                pass
        self._peers.clear()

        if self._server:
            self._server.close()
            self._server = None

    # ── UDP discovery ─────────────────────────────────────────────

    async def _start_udp_listener(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", CHAT_DISC_PORT))
        sock.setblocking(False)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DiscoveryProtocol(self._on_disc_msg),
            sock=sock,
        )
        self._disc_transport = transport

    async def _broadcast_loop(self) -> None:
        """Broadcast our chat addr every 2 s until stop() is called."""
        payload = json.dumps({
            "tid":       self.tournament_id,
            "pid":       self.player_id,
            "chat_addr": self._chat_addr,
        }).encode()
        loop = asyncio.get_running_loop()
        while True:
            await loop.run_in_executor(None, self._send_broadcast_sync, payload)
            await asyncio.sleep(2.0)

    @staticmethod
    def _send_broadcast_sync(payload: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            try:
                s.sendto(payload, ("255.255.255.255", CHAT_DISC_PORT))
            except OSError:
                pass

    def _on_disc_msg(self, msg: dict) -> None:
        """Called in the asyncio event loop when a UDP datagram arrives."""
        if msg.get("tid") != self.tournament_id:
            return
        pid  = msg.get("pid")
        addr = msg.get("chat_addr")
        if not pid or not addr or pid == self.player_id:
            return
        # Only the player with the smaller ID initiates the connection.
        if pid > self.player_id and pid not in self._peers and pid not in self._connecting:
            self._connecting.add(pid)
            t = asyncio.create_task(self._connect_to_peer(pid, addr))
            self._tasks.append(t)

    # ── TCP mesh ──────────────────────────────────────────────────

    async def _connect_to_peer(self, peer_id: str, addr: str) -> None:
        host, port_str = addr.rsplit(":", 1)
        try:
            reader, writer = await asyncio.open_connection(host, int(port_str))
        except (ConnectionRefusedError, OSError):
            self._connecting.discard(peer_id)
            return
        await send_msg(writer, {"command": "chat_hello", "player_id": self.player_id})
        self._peers[peer_id] = writer
        self._connecting.discard(peer_id)
        self._notify_peer_change()
        t = asyncio.create_task(self._read_loop(peer_id, reader))
        self._tasks.append(t)

    async def _on_incoming(self, reader, writer) -> None:
        try:
            msg = await asyncio.wait_for(recv_msg(reader), timeout=5.0)
        except Exception:
            writer.close()
            return
        if msg.get("command") != "chat_hello":
            writer.close()
            return
        peer_id = msg.get("player_id", "?")
        self._peers[peer_id] = writer
        self._notify_peer_change()
        t = asyncio.create_task(self._read_loop(peer_id, reader))
        self._tasks.append(t)

    async def _read_loop(self, peer_id: str, reader) -> None:
        try:
            while True:
                msg = await recv_msg(reader)
                if msg.get("command") == "chat" and self.on_message:
                    self.on_message(msg.get("from", peer_id), msg.get("text", ""))
        except Exception:
            self._remove_peer(peer_id)

    def _remove_peer(self, peer_id: str) -> None:
        self._peers.pop(peer_id, None)
        self._notify_peer_change()

    def _notify_peer_change(self) -> None:
        if self.on_peer_change:
            self.on_peer_change(list(self._peers.keys()))

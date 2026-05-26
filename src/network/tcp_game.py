import asyncio
from network.protocol import send_msg, recv_msg

TIMEOUT = 60  # seconds before forfeit


# ── UI bridge ─────────────────────────────────────────────────────
# Decouples the game loop from the UI. The Textual GameScreen wires
# up the callbacks; the game loop never imports anything from Textual.


class GameUI:

    def __init__(self) -> None:
        self._input_queue = asyncio.Queue()
        self.on_log = None  # (msg: str) -> None
        self.on_tracking = None  # (x, y, outcome, ship) -> None
        self.on_my_board = None  # (x, y, outcome) -> None
        self.on_turn = None  # (my_turn: bool) -> None

    async def get_attack(self) -> str:
        return await self._input_queue.get()

    def submit_attack(self, coord: str) -> None:
        self._input_queue.put_nowait(coord)

    def log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)

    def update_tracking(self, x: int, y: int, outcome: str, ship: str | None = None) -> None:
        if self.on_tracking:
            self.on_tracking(x, y, outcome, ship)

    def update_my_board(self, x: int, y: int, outcome: str) -> None:
        if self.on_my_board:
            self.on_my_board(x, y, outcome)

    def set_turn(self, my_turn: bool) -> None:
        if self.on_turn:
            self.on_turn(my_turn)


# ── Coordinate helpers ────────────────────────────────────────────
# Standard battleship: A-J = columns (x), 1-10 = rows (y), A1 = top-left


def _parse_coord(text: str) -> tuple[int, int]:
    """'A5' → (col=0, row=4). Raises ValueError on bad input."""
    text = text.strip().upper()
    col = ord(text[0]) - ord("A")  # A=0 … J=9
    row = int(text[1:]) - 1  # 1=0 … 10=9
    if not (0 <= col <= 9 and 0 <= row <= 9):
        raise ValueError
    return col, row


def _coord_str(col: int, row: int) -> str:
    """(col=0, row=4) → 'A5'."""
    return f"{chr(ord('A') + col)}{row + 1}"


# ── Entry points ──────────────────────────────────────────────────


async def start_host_server(port: int):
    """Start listening on port and return (server, conn_future).
    The future resolves to (reader, writer) when a client connects.
    Call this before placement so the socket is open early."""
    conn: asyncio.Future = asyncio.get_running_loop().create_future()

    async def on_connect(reader, writer):
        if not conn.done():
            conn.set_result((reader, writer))
        else:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(on_connect, "0.0.0.0", port)
    return server, conn


async def run_as_host(player_id: str, game_id: str, port: int, my_board, ui: GameUI, *, server_info=None) -> str | None:
    """Wait for one opponent then play. Host goes first.
    Pass server_info=(server, conn) if start_host_server was already called."""
    if server_info is None:
        server, conn = await start_host_server(port)
    else:
        server, conn = server_info

    ui.log(f"[dim]À espera de ligação TCP na porta {port}…[/]")

    try:
        reader, writer = await asyncio.wait_for(conn, timeout=TIMEOUT)
    except asyncio.TimeoutError:
        server.close()
        ui.log("[red]Tempo esgotado à espera do adversário.[/]")
        return None
    server.close()
    ui.log("[green]Adversário ligado! A iniciar partida…[/]")

    # Handshake: exchange player IDs so both sides know opponent identity
    await send_msg(writer, {"command": "hello", "player_id": player_id})
    try:
        hello = await recv_msg(reader)
        opponent_id = hello.get("player_id")
    except Exception:
        opponent_id = None

    winner = await game_session(reader, writer, player_id, opponent_id, game_id, i_go_first=True, my_board=my_board, ui=ui)
    return winner


async def run_as_guest(player_id: str, game_id: str, addr: str, my_board, ui: GameUI) -> str | None:
    """Connect to host's TCP address, then play. Guest goes second."""
    host, port_str = addr.rsplit(":", 1)
    try:
        reader, writer = await asyncio.open_connection(host, int(port_str))
    except (ConnectionRefusedError, OSError) as exc:
        ui.log(f"[red]Falha ao ligar a {addr}: {exc}[/]")
        return None
    ui.log(f"[green]Ligado a {addr}[/]")

    # Handshake: exchange player IDs so both sides know opponent identity
    await send_msg(writer, {"command": "hello", "player_id": player_id})
    try:
        hello = await recv_msg(reader)
        opponent_id = hello.get("player_id")
    except Exception:
        opponent_id = None

    winner = await game_session(reader, writer, player_id, opponent_id, game_id, i_go_first=False, my_board=my_board, ui=ui)
    return winner


# ── Game loop ─────────────────────────────────────────────────────


async def game_session(
    reader, writer, player_id, opponent_id, game_id, i_go_first, my_board, ui: GameUI
) -> str | None:
    """Run the full game turn by turn until someone wins or disconnects.
    Returns the winner's player_id (or None if unknown)."""
    my_turn = i_go_first
    winner = None
    try:
        while True:
            ui.set_turn(my_turn)
            if my_turn:
                result = await _my_attack(writer, reader, game_id, ui, player_id, opponent_id)
            else:
                result = await _their_attack(reader, writer, player_id, opponent_id, game_id, my_board, ui)

            # result is either None (no winner yet) or a winner id string
            if result is not None:
                winner = result
                break
            my_turn = not my_turn
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        ui.log("[red]Adversário desligou. Vitória por abandono.[/]")
        winner = player_id
    finally:
        writer.close()
        await writer.wait_closed()

    return winner


async def _my_attack(writer, reader, game_id: str, ui: GameUI, player_id: str, opponent_id: str | None) -> str | None:
    """My turn: ask for coordinates, send fire, wait for result.
    Returns winner id if the game ended, otherwise None."""
    while True:
        input_task   = asyncio.create_task(ui.get_attack())
        network_task = asyncio.create_task(recv_msg(reader))
        try:
            done, pending = await asyncio.wait(
                [input_task, network_task], return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            # App shutdown / CancelledError: clean up both tasks before propagating
            for t in (input_task, network_task):
                t.cancel()
                try:
                    await t
                except Exception:
                    pass
            raise
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        if network_task in done:
            # Opponent sent gameover while we were idle (forfeit)
            msg = network_task.result()
            if msg.get("command") == "gameover":
                winner = msg.get("winner")
                ui.log("[bold red]Partida terminada.[/]")
                return winner
            # Unexpected message during our turn: ignore and re-enter the loop
            # (input_task was cancelled in the pending cleanup above)
            if input_task not in done:
                continue

        raw = input_task.result()

        if raw.strip().lower() == "render":
            await send_msg(writer, {"command": "surrender", "game_id": game_id})
            ui.log("[red]Rendeu-se.[/]")
            return opponent_id

        try:
            col, row = _parse_coord(raw)
            break
        except (ValueError, IndexError):
            ui.log("[red]Formato inválido. Exemplo: A5  (letra A-J + número 1-10)[/]")

    await send_msg(writer, {"command": "fire", "game_id": game_id, "x": col, "y": row})

    try:
        msg = await asyncio.wait_for(recv_msg(reader), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        # If no response arrives in time, consider opponent forfeited -> we win
        ui.log("[red]Tempo esgotado a aguardar resultado.[/]")
        return player_id

    if msg["command"] == "gameover":
        winner = msg.get("winner")
        ui.log("[bold green]Vitória! Todos os navios afundados.[/]")
        return winner

    outcome = msg.get("outcome", "miss")
    ship = msg.get("ship")
    coord = _coord_str(col, row)

    if outcome == "miss":
        ui.log(f"{coord} → Água.")
    elif outcome == "hit":
        ui.log(f"[yellow]{coord} → Acertou![/]")
    elif outcome == "sunk":
        ui.log(f"[red]{coord} → Afundou o {ship or '?'}![/]")

    ui.update_tracking(col, row, outcome, ship)
    return None


async def _their_attack(reader, writer, player_id: str, opponent_id: str | None, game_id: str, my_board, ui: GameUI) -> str | None:
    """Their turn: wait for fire, validate against our board, reply with result.
    Returns winner id if the game ended, otherwise None."""
    try:
        msg = await asyncio.wait_for(recv_msg(reader), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        await send_msg(writer, {"command": "gameover", "game_id": game_id, "winner": player_id})
        ui.log("[green]Adversário demorou demasiado. Vitória por abandono.[/]")
        return player_id

    if msg.get("command") == "surrender":
        ui.log("[bold green]Adversário rendeu-se. Vitória![/]")
        return player_id

    if msg.get("command") != "fire":
        return None

    x, y = msg["x"], msg["y"]
    outcome, ship, game_over = my_board.register_shot(x, y)

    ui.update_my_board(x, y, outcome)
    ui.log(f"[dim]{_coord_str(x, y)} → adversário: {outcome}.[/]")

    if game_over:
        # Defender lost: winner is the attacker (opponent)
        await send_msg(writer, {"command": "gameover", "game_id": game_id, "winner": opponent_id})
        ui.log("[bold red]Derrota — todos os navios afundados.[/]")
        return opponent_id

    result = {"command": "result", "game_id": game_id, "outcome": outcome}
    if ship:
        result["ship"] = ship
    await send_msg(writer, result)

    return None

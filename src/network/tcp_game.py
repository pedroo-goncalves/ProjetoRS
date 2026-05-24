import asyncio
import aioconsole
from rich.console import Console
from network.protocol import send_msg, recv_msg

console = Console()
TIMEOUT = 60  # seconds before forfeit


# ── Coordinate helpers ────────────────────────────────────────────
# Board uses chess-style: A-J for rows, 1-10 for columns (ex: A1, J10)

def _parse_coord(text: str) -> tuple[int, int]:
    """'A5' → (row=0, col=4). Raises ValueError on bad input."""
    text = text.strip().upper()
    row = ord(text[0]) - ord("A")        # A=0, B=1, ..., J=9
    col = int(text[1:]) - 1              # 1=0, 2=1, ..., 10=9
    if not (0 <= row <= 9 and 0 <= col <= 9):
        raise ValueError
    return row, col


def _coord_str(row: int, col: int) -> str:
    """(row=0, col=4) → 'A5'."""
    return f"{chr(ord('A') + row)}{col + 1}"


# ── Entry points ──────────────────────────────────────────────────

async def run_as_host(player_id: str, game_id: str, port: int) -> None:
    """Open a TCP server, wait for one opponent, then play. Host goes first."""
    # asyncio.start_server returns immediately; we await the first connection
    conn: asyncio.Future = asyncio.get_event_loop().create_future()

    async def on_connect(reader, writer):
        if not conn.done():
            conn.set_result((reader, writer))

    server = await asyncio.start_server(on_connect, "0.0.0.0", port)
    console.print(f"[dim]À espera de ligação TCP na porta {port}…[/]")

    reader, writer = await conn  # block until someone connects
    server.close()               # stop accepting new connections

    await game_session(reader, writer, player_id, game_id, i_go_first=True)


async def run_as_guest(player_id: str, game_id: str, addr: str) -> None:
    """Connect to host's TCP address, then play. Guest goes second."""
    host, port_str = addr.rsplit(":", 1)
    reader, writer = await asyncio.open_connection(host, int(port_str))
    console.print(f"[green]Ligado a {addr}[/]")

    await game_session(reader, writer, player_id, game_id, i_go_first=False)


# ── Game loop ─────────────────────────────────────────────────────

async def game_session(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    player_id: str,
    game_id: str,
    i_go_first: bool,
) -> None:
    """Run the full game turn by turn until someone wins or disconnects."""
    my_turn = i_go_first
    try:
        while True:
            if my_turn:
                ended = await _my_attack(writer, reader, game_id)
            else:
                ended = await _their_attack(reader, writer, player_id, game_id)
            if ended:
                break
            my_turn = not my_turn  # switch turns
    except asyncio.IncompleteReadError:
        # readexactly raises this when the connection closes mid-message
        console.print("[red]Adversário desligou.[/]")
    finally:
        writer.close()
        await writer.wait_closed()


async def _my_attack(writer, reader, game_id: str) -> bool:
    """My turn: ask for coordinates, send fire, wait for result."""
    while True:
        raw = await aioconsole.ainput("Atacar (ex: A5) ou 'render': ")

        if raw.strip().lower() == "render":
            await send_msg(writer, {"command": "surrender", "game_id": game_id})
            console.print("[red]Rendeu-se.[/]")
            return True

        try:
            row, col = _parse_coord(raw)
            break  # valid input, continue
        except (ValueError, IndexError):
            console.print("[red]Formato inválido. Exemplo: A5  (letra A-J + número 1-10)[/]")

    await send_msg(writer, {"command": "fire", "game_id": game_id, "x": row, "y": col})

    # wait_for raises TimeoutError if opponent takes longer than TIMEOUT seconds
    msg = await asyncio.wait_for(recv_msg(reader), timeout=TIMEOUT)

    if msg["command"] == "gameover":
        console.print("[bold green]Vitória! Todos os navios afundados.[/]")
        return True

    # command == "result"
    outcome = msg.get("outcome", "miss")
    coord = _coord_str(row, col)
    if outcome == "miss":
        console.print(f"  {coord} → Água.")
    elif outcome == "hit":
        console.print(f"  {coord} → Acertou!")
    elif outcome == "sunk":
        console.print(f"  {coord} → Afundou o {msg.get('ship', '?')}!")
    return False


async def _their_attack(reader, writer, player_id: str, game_id: str) -> bool:
    """Their turn: wait for fire, validate against our board, reply with result."""
    console.print("[dim]Vez do adversário…[/]")

    try:
        msg = await asyncio.wait_for(recv_msg(reader), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        # Opponent went silent — we win by forfeit
        await send_msg(writer, {"command": "gameover", "game_id": game_id, "winner": player_id})
        console.print("[green]Adversário demorou demasiado. Vitória por abandono.[/]")
        return True

    if msg["command"] == "surrender":
        console.print("[bold green]Adversário rendeu-se. Vitória![/]")
        return True

    if msg["command"] != "fire":
        return False

    x, y = msg["x"], msg["y"]

    # TODO P2: substituir estas 3 linhas por board.register_shot(x, y)
    # register_shot devolve (outcome, ship_name, game_over)
    outcome, ship, game_over = "miss", None, False

    if game_over:
        await send_msg(writer, {"command": "gameover", "game_id": game_id, "winner": "oponente"})
        console.print("[bold red]Derrota — todos os navios afundados.[/]")
        return True

    result = {"command": "result", "game_id": game_id, "outcome": outcome}
    if ship:
        result["ship"] = ship
    await send_msg(writer, result)

    console.print(f"  {_coord_str(x, y)} → adversário: {outcome}.")
    return False

import json
import re
import uuid
from datetime import datetime
from typing import Optional
import socket
import asyncio
import sys

import paho.mqtt.client as mqtt

from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import (
    Static, Header, Footer, Input,
    ContentSwitcher, RichLog,
)
from textual.containers import Vertical
from textual.reactive import reactive
from textual import on, work

from network.tcp_game import run_as_host, run_as_guest, GameUI, start_host_server
from network.preparation import PlacementScreen
from network.chat_mesh import ChatMesh
from game.board import Board, GRID_SIZE
from game.types import Cell

BROKER    = sys.argv[1] if len(sys.argv) > 1 else "localhost"
PORT_MQTT = 1883

TITLE_ART = """\
[bold cyan]
██████╗  █████╗ ████████╗ █████╗ ██╗     ██╗  ██╗ █████╗
██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗██║     ██║  ██║██╔══██╗
██████╔╝███████║   ██║   ███████║██║     ███████║███████║
██╔══██╗██╔══██║   ██║   ██╔══██║██║     ██╔══██║██╔══██║
██████╔╝██║  ██║   ██║   ██║  ██║███████╗██║  ██║██║  ██║
╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝
[/]"""


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def get_local_ip() -> str:
    """Return the machine's LAN IP by probing a UDP route (no packet sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


# ── Board rendering ───────────────────────────────────────────────

_EMPTY  = "[dim]·[/]"
_MISS   = "[blue]○[/]"
_HIT    = "[yellow]×[/]"
_SUNK   = "[red]■[/]"
_SHIP   = "[green]█[/]"
_CURSOR = "[bold reverse]·[/]"


def _render_tracking(tracking: dict[Cell, str], cursor: tuple[int, int] | None = None) -> str:
    header = "     " + "   ".join("ABCDEFGHIJ")
    rows = [header]
    for y in range(GRID_SIZE):
        cells = []
        for x in range(GRID_SIZE):
            outcome = tracking.get((x, y))
            if cursor == (x, y):
                cells.append(_CURSOR)
            elif outcome == "miss": cells.append(_MISS)
            elif outcome == "hit":  cells.append(_HIT)
            elif outcome == "sunk": cells.append(_SUNK)
            else:                   cells.append(_EMPTY)
        rows.append(f"{y + 1:>2}   " + "   ".join(cells))
    return "\n\n".join(rows)


def _render_my_board(my_board: Board) -> str:
    ship_cells: set[Cell] = set()
    hit_cells:  set[Cell] = set()
    sunk_cells: set[Cell] = set()
    for ship in my_board.ships:
        ship_cells |= ship.position
        if ship.is_sunk():
            sunk_cells |= ship.position
        else:
            hit_cells |= ship.hits

    header = "     " + "   ".join("ABCDEFGHIJ")
    rows = [header]
    for y in range(GRID_SIZE):
        cells = []
        for x in range(GRID_SIZE):
            cell = Cell(x, y)
            if   cell in sunk_cells: cells.append(_SUNK)
            elif cell in hit_cells:  cells.append(_HIT)
            elif cell in ship_cells: cells.append(_SHIP)
            else:                    cells.append(_EMPTY)
        rows.append(f"{y + 1:>2}   " + "   ".join(cells))
    return "\n\n".join(rows)


# ── SetupScreen ───────────────────────────────────────────────────

_NAME_RE = re.compile(r'^[A-Za-z0-9]+$')


class SetupScreen(Screen):

    CSS = """
    SetupScreen { align: center middle; }
    #title { margin-bottom: 1; }
    .lbl   { color: cyan; margin-top: 1; margin-bottom: 0; }
    Input  { width: 36; border: tall $panel; padding: 0 1; margin-bottom: 0; }
    #name_error { color: red; height: 1; margin-top: 0; }
    """

    def compose(self) -> ComposeResult:
        yield Static(TITLE_ART, id="title")
        yield Static("Nome de jogador", classes="lbl")
        yield Input(id="name_input")
        yield Static("", id="name_error")

    @on(Input.Submitted, "#name_input")
    def on_enter(self) -> None:
        name  = self.query_one("#name_input", Input).value.strip()
        error = self.query_one("#name_error", Static)
        if not name:
            error.update("O nome não pode estar vazio.")
            return
        if not _NAME_RE.match(name):
            error.update("Só são permitidas letras (a-z) e números (0-9).")
            return
        error.update("")
        self.app.player_id = f"{name}_{datetime.now().strftime('%H%M%S%f')}"
        self.app.my_addr   = f"{get_local_ip()}:{find_free_port()}"
        self.app.setup_mqtt()
        self.app.push_screen(MainMenuScreen())


# ── MainMenuScreen ────────────────────────────────────────────────

class MainMenuScreen(Screen):

    CSS = """
    MainMenuScreen { align: center middle; }
    """

    BINDINGS = [
        ("up",    "cursor_up",   ""),
        ("down",  "cursor_down", ""),
        ("enter", "select",      "Selecionar"),
    ]

    _OPTIONS = [("1v1", True), ("Torneio", True), ("Sair", True)]
    cursor: reactive[int] = reactive(0)

    def compose(self) -> ComposeResult:
        yield Static("", id="menu_display")

    def on_mount(self) -> None:
        self._render_menu()

    def _render_menu(self) -> None:
        lines = [TITLE_ART, "\n[cyan]Batalha Naval[/]\n"]
        for i, (opt, ready) in enumerate(self._OPTIONS):
            prefix = "[yellow]►[/] " if i == self.cursor else "  "
            label  = f"[yellow]{i + 1}.[/] {opt}" if ready else f"[dim]{i + 1}. {opt}[/]"
            lines.append(f"{prefix}{label}")
        self.query_one("#menu_display", Static).update("\n".join(lines))

    def watch_cursor(self) -> None:
        self._render_menu()

    def action_cursor_up(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def action_cursor_down(self) -> None:
        self.cursor = min(len(self._OPTIONS) - 1, self.cursor + 1)

    def action_select(self) -> None:
        opt, ready = self._OPTIONS[self.cursor]
        if not ready:
            return
        if opt == "1v1":
            self.app.push_screen(Menu1v1Screen())
        elif opt == "Torneio":
            self.app.push_screen(MenuTorneioScreen())
        elif opt == "Sair":
            self.app.client.publish(
                f"naval/players/{self.app.player_id}", "offline", retain=True
            )
            self.app.exit()


# ── Menu1v1Screen ─────────────────────────────────────────────────

class Menu1v1Screen(Screen):

    CSS = """
    Menu1v1Screen { align: center middle; }
    """

    BINDINGS = [
        ("up",     "cursor_up",   ""),
        ("down",   "cursor_down", ""),
        ("enter",  "select",      "Selecionar"),
        ("escape", "back",        "Voltar"),
    ]

    _OPTIONS = ["Criar partida", "Entrar em partida", "Matchmaking"]

    cursor:          reactive[int]  = reactive(0)
    games_cursor:    reactive[int]  = reactive(0)
    mode:            reactive[str]  = reactive("menu")
    available_games: reactive[dict] = reactive({})

    def compose(self) -> ComposeResult:
        yield Static("", id="display")

    def on_mount(self) -> None:
        self._game_items: list[tuple[str, dict]] = []
        self.app.client.message_callback_add("naval/games/#", self._on_mqtt_message)
        self.app.client.subscribe("naval/games/#")
        self._refresh()

    def on_unmount(self) -> None:
        self.app.client.message_callback_remove("naval/games/#")
        self.app.client.unsubscribe("naval/games/#")

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        game_id = msg.topic.split("/")[-1]
        payload = msg.payload.decode()
        games   = dict(self.available_games)
        if payload == "closed":
            games.pop(game_id, None)
        else:
            try:
                data = json.loads(payload)
                if data.get("host") != self.app.player_id:
                    games[game_id] = data
            except json.JSONDecodeError:
                pass
        self.app.call_from_thread(setattr, self, "available_games", games)

    def watch_available_games(self, games: dict) -> None:
        self._game_items = list(games.items())
        self._refresh()

    def watch_cursor(self)       -> None: self._refresh()
    def watch_games_cursor(self) -> None: self._refresh()
    def watch_mode(self)         -> None: self._refresh()

    def _refresh(self) -> None:
        in_games = self.mode == "games"
        lines = ["\n[cyan]1v1[/]\n"]

        for i, opt in enumerate(self._OPTIONS):
            if in_games and i == 1:
                prefix = "[yellow]►[/] "
            elif not in_games and i == self.cursor:
                prefix = "[yellow]►[/] "
            else:
                prefix = "  "
            lines.append(f"{prefix}[yellow]{i + 1}.[/] {opt}")

        lines += ["", "[bold]Partidas disponíveis:[/]"]

        if not self._game_items:
            lines.append("  [dim]Nenhuma partida disponível[/]")
        else:
            for i, (_, game) in enumerate(self._game_items):
                prefix = "[yellow]►[/] " if (in_games and i == self.games_cursor) else "  "
                lines.append(f"{prefix}[cyan]{game.get('host', '?')}[/]")

        self.query_one("#display", Static).update("\n".join(lines))

    def action_cursor_up(self) -> None:
        if self.mode == "games":
            self.games_cursor = max(0, self.games_cursor - 1)
        else:
            self.cursor = max(0, self.cursor - 1)

    def action_cursor_down(self) -> None:
        if self.mode == "games":
            self.games_cursor = min(max(0, len(self._game_items) - 1), self.games_cursor + 1)
        else:
            self.cursor = min(len(self._OPTIONS) - 1, self.cursor + 1)

    def action_select(self) -> None:
        if self.mode == "games":
            if self._game_items:
                self._join(self.games_cursor)
        elif self.cursor == 0:
            self._criar()
        elif self.cursor == 1:
            self.mode = "games"
            self.games_cursor = 0
        else:
            self.app.push_screen(MatchmakingScreen())

    def action_back(self) -> None:
        if self.mode == "games":
            self.mode = "menu"
        else:
            self.app.pop_screen()

    @work
    async def _join(self, idx: int) -> None:
        if not self.app.broker_ok():
            self.notify("O broker não se encontra ativo.", severity="error")
            return
        if idx >= len(self._game_items):
            return
        game_id, game = self._game_items[idx]
        self.app.client.publish(f"naval/games/{game_id}", "closed", retain=True)
        self.app._game_topics.discard(f"naval/games/{game_id}")
        my_board = await self.app.push_screen_wait(PlacementScreen())
        await self.app.push_screen_wait(GameScreen(
            player_id    = self.app.player_id,
            game_id      = game_id,
            my_board     = my_board,
            is_host      = False,
            addr_or_port = game["addr"],
        ))

    @work
    async def _criar(self) -> None:
        if not self.app.broker_ok():
            self.notify("O broker não se encontra ativo.", severity="error")
            return
        game_id     = str(uuid.uuid4())
        server_info = await start_host_server(0)
        server, _   = server_info
        actual_port = server.sockets[0].getsockname()[1]
        actual_addr = f"{get_local_ip()}:{actual_port}"
        topic = f"naval/games/{game_id}"
        self.app.client.publish(
            topic,
            json.dumps({"host": self.app.player_id, "addr": actual_addr}),
            retain=True,
        )
        self.app._game_topics.add(topic)
        my_board = await self.app.push_screen_wait(PlacementScreen())
        await self.app.push_screen_wait(GameScreen(
            player_id    = self.app.player_id,
            game_id      = game_id,
            my_board     = my_board,
            is_host      = True,
            addr_or_port = actual_port,
            server_info  = server_info,
        ))


# ── MatchmakingScreen ─────────────────────────────────────────────

class MatchmakingScreen(Screen):

    CSS = """
    MatchmakingScreen { align: center middle; }
    #mm_status { text-align: center; }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancelar"),
    ]

    status: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Static("", id="mm_status")

    def on_mount(self) -> None:
        self._queue:     dict         = {}
        self._found:     tuple | None = None
        self._cancelled: bool         = False
        self._search()

    def on_unmount(self) -> None:
        self.app.client.message_callback_remove("naval/matchmaking/+")
        self.app.client.unsubscribe("naval/matchmaking/+")

    def watch_status(self, val: str) -> None:
        self.query_one("#mm_status", Static).update(val)

    def _handle_matchmaking_message(self, sender_id: str, data: dict | None) -> None:
        player_id = self.app.player_id
        if sender_id == player_id:
            return
        # Ignore clears: if we already have an addr for this player, keep it.
        # They cleared because they found a match; their TCP server is still up.
        if data is not None:
            self._queue[sender_id] = data["addr"]

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        sender_id = msg.topic.split("/")[-1]
        payload   = msg.payload.decode()
        if not payload:
            self.app.call_from_thread(self._handle_matchmaking_message, sender_id, None)
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        self.app.call_from_thread(self._handle_matchmaking_message, sender_id, data)

    @work(exit_on_error=False)
    async def _search(self) -> None:
        for n in range(3, 0, -1):
            self.status = f"[dim]A entrar na fila em {n}…[/]"
            await asyncio.sleep(1)

        if self._cancelled:
            return

        player_id = self.app.player_id
        my_addr   = self.app.my_addr

        self.app.client.message_callback_add("naval/matchmaking/+", self._on_mqtt_message)
        self.app.client.subscribe("naval/matchmaking/+")
        self.app.client.publish(
            f"naval/matchmaking/{player_id}",
            json.dumps({"player_id": player_id, "addr": my_addr}),
            retain=True,
        )
        self.status = "[dim]À procura de adversário… (Esc para cancelar)[/]"

        while not self._cancelled:
            await asyncio.sleep(0.25)
            if self._queue:
                opp_id, opp_addr = next(iter(self._queue.items()))
                self._found = (opp_id, opp_addr, player_id > opp_id)
                break

        if not self._found or self._cancelled:
            return

        self._cancelled = True
        opp_id, opp_addr, i_am_host = self._found
        game_id = f"{min(player_id, opp_id)}-{max(player_id, opp_id)}"
        self.status = f"[green]Adversário encontrado: {opp_id}! A iniciar…[/]"

        # Clear our retained presence so no other player tries to match with us
        self.app.client.publish(f"naval/matchmaking/{player_id}", b"", retain=True)

        if i_am_host:
            port = int(my_addr.split(":")[1])
            try:
                server_info = await start_host_server(port)
            except OSError:
                self.status = "[red]Porta ocupada. Tenta novamente.[/]"
                return
            my_board = await self.app.push_screen_wait(PlacementScreen())
            await self.app.push_screen_wait(GameScreen(
                player_id    = player_id,
                game_id      = game_id,
                my_board     = my_board,
                is_host      = True,
                addr_or_port = port,
                server_info  = server_info,
            ))
            self.app.pop_screen()
        else:
            my_board = await self.app.push_screen_wait(PlacementScreen())
            await self.app.push_screen_wait(GameScreen(
                player_id    = player_id,
                game_id      = game_id,
                my_board     = my_board,
                is_host      = False,
                addr_or_port = opp_addr,
            ))
            self.app.pop_screen()

    def action_cancel(self) -> None:
        self._cancelled = True
        self.app.client.publish(
            f"naval/matchmaking/{self.app.player_id}", b"", retain=True
        )
        self.app.pop_screen()


# ── MenuTorneioScreen ─────────────────────────────────────────────

class MenuTorneioScreen(Screen):

    CSS = """
    MenuTorneioScreen { align: center middle; }
    #display   { margin-bottom: 1; }
    #max_input { width: 30; border: tall $panel; padding: 0 1; }
    #chat_log  { height: 8; border: solid $accent; width: 60; display: none; }
    #chat_input { width: 60; display: none; }
    """

    BINDINGS = [
        ("up",     "cursor_up",   ""),
        ("down",   "cursor_down", ""),
        ("enter",  "select",      "Selecionar"),
        ("escape", "back",        "Voltar"),
    ]

    _OPTIONS = ["Criar torneio", "Entrar em torneio"]

    cursor:                reactive[int]  = reactive(0)
    tourney_cursor:        reactive[int]  = reactive(0)
    mode:                  reactive[str]  = reactive("menu")
    available_tournaments: reactive[dict] = reactive({})

    def __init__(self) -> None:
        super().__init__()
        # lobby state
        self.players: dict = {}
        self.max_players: int = 0
        self.current_tournament_id: str | None = None
        self._tourney_items: list = []
        self._my_lobby_topic: str | None = None
        self._tournament_started: bool = False
        self._i_am_host: bool = False
        self._host_id: str = ""
        # tournament round state
        self.active_players: list = []
        self.round: int = 1
        self.max_rounds: int = 0
        self.round_winners: dict = {}
        self._round_event: asyncio.Event | None = None
        self._bracket_history: list[dict] = []

    
    def compose(self) -> ComposeResult:
        yield Static("", id="display")
        yield RichLog(id="chat_log", markup=True, highlight=False)
        yield Input(placeholder="Mensagem de chat…", id="chat_input", disabled=True)

    def on_mount(self) -> None:
        self.app.client.message_callback_add("naval/tournament/#", self._on_mqtt_message)
        self.app.client.subscribe("naval/tournament/#")
        # RichLog is scrollable and would auto-focus on mount; prevent it from stealing keys
        self.query_one("#chat_log").can_focus = False
        self._refresh()
        self.set_focus(None)

    def on_unmount(self) -> None:
        if self.app.chat_mesh:
            asyncio.create_task(self.app.chat_mesh.stop())
            self.app.chat_mesh = None
        self.app.client.message_callback_remove("naval/tournament/#")
        self.app.client.unsubscribe("naval/tournament/#")
        tid = self.current_tournament_id
        if tid:
            # Clear lobby presence
            lobby_filter = f"naval/tournament/LOBBY/{tid}/+"
            self.app.client.message_callback_remove(lobby_filter)
            self.app.client.unsubscribe(lobby_filter)
            if self._my_lobby_topic:
                self.app.client.publish(self._my_lobby_topic, b"", retain=True)
            # Clear winner topics for every round
            for r in range(1, (self.max_rounds or 3) + 1):
                wf = f"naval/tournament/{tid}/winner/round{r}/+"
                self.app.client.message_callback_remove(wf)
                self.app.client.unsubscribe(wf)
                self.app.client.publish(
                    f"naval/tournament/{tid}/winner/round{r}/{self.app.player_id}",
                    b"", retain=True,
                )
            # Clear current match game address (if we were host of this match)
            if self.active_players and self.app.player_id in self.active_players:
                my_idx = self.active_players.index(self.app.player_id)
                opp_idx = my_idx + 1 if my_idx % 2 == 0 else my_idx - 1
                if 0 <= opp_idx < len(self.active_players):
                    opp_id  = self.active_players[opp_idx]
                    game_id = f"{min(self.app.player_id, opp_id)}-{max(self.app.player_id, opp_id)}"
                    self.app.client.publish(
                        f"naval/tournament/{tid}/game/{game_id}", b"", retain=True
                    )
            # If we created the tournament, remove its announcement
            if self._i_am_host:
                self.app.client.publish(f"naval/tournament/{tid}", b"", retain=True)

    # --- Lógica de Lobby Descentralizado ---

    def join_tournament(self, tournament_id: str, max_p: int, host_id: str = "") -> None:
        self._i_am_host = False
        self._host_id = host_id or self.app.player_id
        if self.current_tournament_id:
            old_filter = f"naval/tournament/LOBBY/{self.current_tournament_id}/+"
            self.app.client.message_callback_remove(old_filter)
            self.app.client.unsubscribe(old_filter)
            if self._my_lobby_topic:
                self.app.client.publish(self._my_lobby_topic, b"", retain=True)
            self.players.clear()
            self._tournament_started = False

        if self.app.chat_mesh:
            asyncio.create_task(self.app.chat_mesh.stop())
            self.app.chat_mesh = None

        self.current_tournament_id = tournament_id
        self.max_players = max_p
        lobby_filter = f"naval/tournament/LOBBY/{tournament_id}/+"
        self._my_lobby_topic = f"naval/tournament/LOBBY/{tournament_id}/{self.app.player_id}"

        self.app.client.message_callback_add(lobby_filter, self._on_lobby_update)
        self.app.client.subscribe(lobby_filter)
        self.app.client.publish(self._my_lobby_topic, json.dumps({
            "addr": self.app.my_addr
        }), retain=True)

        self.app.chat_mesh = ChatMesh(self.app.player_id, tournament_id)
        self.app.chat_mesh.on_message = lambda sid, txt: self.call_later(
            self._on_chat_message, sid, txt
        )
        asyncio.create_task(self.app.chat_mesh.start())

        self.mode = "lobby"

    def _on_lobby_update(self, client, userdata, msg) -> None:
        pid = msg.topic.split("/")[-1]
        payload = msg.payload.decode()
        if not payload:
            self.app.call_from_thread(self._update_lobby, pid, None)
            return
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return
        addr = data.get("addr")
        if not addr:
            return
        self.app.call_from_thread(self._update_lobby, pid, addr)

    def _update_lobby(self, pid: str, addr: str | None) -> None:
        if addr is None:
            self.players.pop(pid, None)
        else:
            self.players[pid] = addr
        self._refresh()
        # Only the host updates the headcount in the announcement (avoids races)
        if self.current_tournament_id and not self._tournament_started and self._i_am_host:
            self.app.client.publish(
                f"naval/tournament/{self.current_tournament_id}",
                json.dumps({
                    "host":            self._host_id,
                    "max_players":     self.max_players,
                    "current_players": len(self.players),
                }),
                retain=True,
            )
        if len(self.players) == self.max_players and not self._tournament_started:
            self._tournament_started = True
            self.active_players = sorted(self.players.keys())
            self.max_rounds     = {4: 2, 8: 3}.get(self.max_players, 3)
            self.round          = 1
            self.round_winners  = {}
            # Close the lobby: stop processing late joiners and clear own presence
            lobby_filter = f"naval/tournament/LOBBY/{self.current_tournament_id}/+"
            self.app.client.message_callback_remove(lobby_filter)
            self.app.client.unsubscribe(lobby_filter)
            if self._my_lobby_topic:
                self.app.client.publish(self._my_lobby_topic, b"", retain=True)
            # Remove the open announcement so no new players can see/join it
            if self._i_am_host:
                self.app.client.publish(
                    f"naval/tournament/{self.current_tournament_id}", b"", retain=True
                )
            self._run_tournament()

    @work(exit_on_error=False)
    async def _run_tournament(self) -> None:
        try:
            await self._tournament_loop()
        except Exception as exc:
            self.notify(f"Erro no torneio: {exc}", severity="error")
            self.mode = "menu"

    async def _tournament_loop(self) -> None:
        while len(self.active_players) > 1:
            self.round_winners = {}
            self._round_event  = asyncio.Event()
            round_initial      = list(self.active_players)

            winner_filter = (
                f"naval/tournament/{self.current_tournament_id}"
                f"/winner/round{self.round}/+"
            )
            self.app.client.message_callback_add(winner_filter, self._on_winner_msg)
            self.app.client.subscribe(winner_filter)

            my_idx = self.active_players.index(self.app.player_id)
            # BYE: odd player at the end (whole bracket disconnected upstream)
            if my_idx % 2 == 0 and my_idx + 1 >= len(self.active_players):
                match_winner = self.app.player_id
                opponent_id  = None
            else:
                opponent_id = (
                    self.active_players[my_idx + 1] if my_idx % 2 == 0
                    else self.active_players[my_idx - 1]
                )
                match_winner = await self.app.init_p2p_game(
                    opponent_id, self.current_tournament_id
                )

            if match_winner != self.app.player_id:
                # Record what we know: we lost our match
                self._record_round(round_initial, {opponent_id: True})
                self.app.client.message_callback_remove(winner_filter)
                self.app.client.unsubscribe(winner_filter)
                self.mode = "eliminated"
                return

            win_topic = (
                f"naval/tournament/{self.current_tournament_id}"
                f"/winner/round{self.round}/{self.app.player_id}"
            )
            self.app.client.publish(
                win_topic, json.dumps({"winner": self.app.player_id}), retain=True
            )
            self._register_winner(self.app.player_id)

            self.mode = "waiting"
            try:
                await asyncio.wait_for(self._round_event.wait(), timeout=120.0)
            except asyncio.TimeoutError:
                pass  # proceed with whoever we have

            self.app.client.message_callback_remove(winner_filter)
            self.app.client.unsubscribe(winner_filter)

            self._record_round(round_initial, self.round_winners)
            self.round         += 1
            self.active_players = sorted(self.round_winners.keys())
            self.round_winners  = {}

        self.mode = "champion"

    # --- Gestão de Mensagens ---

    def _on_winner_msg(self, client, userdata, msg) -> None:
        pid     = msg.topic.split("/")[-1]
        payload = msg.payload.decode()
        if not payload:
            return
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return
        if data.get("winner") != pid:
            return
        self.app.call_from_thread(self._register_winner, pid)

    def _register_winner(self, pid: str) -> None:
        self.round_winners[pid] = True
        self._refresh()
        expected = (len(self.active_players) + 1) // 2  # ceil: handles odd count from BYEs
        if len(self.round_winners) >= expected and self._round_event is not None:
            self._round_event.set()

    def _record_round(self, players: list[str], winners: dict) -> None:
        pairs = []
        for i in range(0, len(players) - 1, 2):
            p1, p2 = players[i], players[i + 1]
            winner = p1 if p1 in winners else (p2 if p2 in winners else None)
            pairs.append((p1, p2, winner))
        if len(players) % 2 == 1:
            pairs.append((players[-1], None, players[-1]))  # BYE
        self._bracket_history.append({"round": self.round, "pairs": pairs})
        self._refresh()

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        # Only process top-level announcement topics: naval/tournament/{id}
        # Deeper topics (LOBBY, winner, results) are handled by their own callbacks.
        parts = msg.topic.split("/")
        if len(parts) != 3:
            return

        tournament_id = parts[2]
        payload = msg.payload.decode()
        tournaments = dict(self.available_tournaments)

        if payload == "closed":
            tournaments.pop(tournament_id, None)
        else:
            try:
                data = json.loads(payload)
                if data.get("host") != self.app.player_id:
                    tournaments[tournament_id] = data
            except json.JSONDecodeError:
                pass
        self.app.call_from_thread(setattr, self, "available_tournaments", tournaments)

    # --- UI & Actions ---

    def watch_available_tournaments(self, tournaments: dict) -> None:
        self._tourney_items = list(tournaments.items())
        self._refresh()

    def watch_cursor(self)        -> None: self._refresh()
    def watch_tourney_cursor(self) -> None: self._refresh()
    def watch_mode(self)           -> None: self._refresh()

    def _bracket_lines(self) -> list[str]:
        if not self._bracket_history:
            return []
        lines = ["[bold]Resultado do torneio:[/]", ""]
        for entry in self._bracket_history:
            r        = entry["round"]
            is_final = (r == self.max_rounds)
            title    = "[bold]Final:[/]" if is_final else f"[bold]Ronda {r}:[/]"
            lines.append(title)
            for p1, p2, winner in entry["pairs"]:
                me1 = " [yellow](tu)[/]" if p1 == self.app.player_id else ""
                me2 = " [yellow](tu)[/]" if p2 == self.app.player_id else ""
                if p2 is None:
                    lines.append(f"  {p1}{me1}  — BYE  →  [green]{p1}[/]")
                elif winner is None:
                    lines.append(f"  {p1}{me1} vs {p2}{me2}  →  [dim]?[/]")
                else:
                    icon = " [bold yellow]🏆[/]" if is_final else " [green]✓[/]"
                    lines.append(f"  {p1}{me1} vs {p2}{me2}  →  [green]{winner}[/]{icon}")
            lines.append("")
        return lines

    def _refresh(self) -> None:
        in_tourneys = self.mode == "tournaments"
        lines = ["\n[cyan]Torneio[/]\n"]

        if self.mode == "lobby":
            lines += [
                "[bold]Lobby — a aguardar jogadores…[/]",
                f"  [cyan]{len(self.players)}/{self.max_players}[/] prontos",
                "",
            ]
            for pid in sorted(self.players):
                tag = "[yellow](tu)[/]" if pid == self.app.player_id else "[green]●[/]"
                lines.append(f"  {tag} {pid}")
            lines += ["", "[dim]Esc para sair do lobby[/]"]

        elif self.mode == "waiting":
            lines += [
                f"[bold]Ronda {self.round}/{self.max_rounds} — a aguardar resultados…[/]",
                "",
            ]
            for i in range(0, len(self.active_players) - 1, 2):
                p1, p2 = self.active_players[i], self.active_players[i + 1]
                me1    = " [yellow](tu)[/]" if p1 == self.app.player_id else ""
                me2    = " [yellow](tu)[/]" if p2 == self.app.player_id else ""
                winner = p1 if p1 in self.round_winners else (p2 if p2 in self.round_winners else None)
                status = f"[green]✓ {winner}[/]" if winner else "[dim]⏳ a jogar…[/]"
                lines.append(f"  {p1}{me1} vs {p2}{me2}  —  {status}")
            if len(self.active_players) % 2 == 1:
                bye = self.active_players[-1]
                tag = " [yellow](tu)[/]" if bye == self.app.player_id else ""
                lines.append(f"  {bye}{tag}  — [dim]BYE (avança automaticamente)[/]")

        elif self.mode == "creating":
            lines += [
                "[bold]Criar torneio[/]",
                "",
                "Prima [yellow]4[/] ou [yellow]8[/] para escolher o número de jogadores:",
                "  [yellow]4[/] — 4 jogadores  (2 rondas)",
                "  [yellow]8[/] — 8 jogadores  (3 rondas)",
                "",
                "[dim]Esc para cancelar[/]",
            ]

        elif self.mode == "eliminated":
            lines += [
                f"[red]Eliminado na ronda {self.round}/{self.max_rounds}.[/]",
                "",
            ]
            lines += self._bracket_lines()
            lines += ["[dim]Esc para voltar ao menu.[/]"]

        elif self.mode == "champion":
            lines += [
                "[bold yellow]Campeão do torneio![/]",
                "",
            ]
            lines += self._bracket_lines()
            lines += ["[dim]Esc para voltar ao menu.[/]"]

        else:
            for i, opt in enumerate(self._OPTIONS):
                prefix = "[yellow]►[/] " if (not in_tourneys and i == self.cursor) else "  "
                lines.append(f"{prefix}[yellow]{i + 1}.[/] {opt}")

            lines += ["", "[bold]Torneios disponíveis:[/]"]
            if not self._tourney_items:
                lines.append("  [dim]Nenhum torneio disponível[/]")
            else:
                for i, (tid, t) in enumerate(self._tourney_items):
                    prefix = "[yellow]►[/] " if (in_tourneys and i == self.tourney_cursor) else "  "
                    slots  = f"{t.get('current_players', 0)}/{t.get('max_players', 4)}"
                    lines.append(f"{prefix}[cyan]{t.get('host', '?')}[/] [dim]({slots})[/]")

        self.query_one("#display", Static).update("\n".join(lines))
        show_chat  = self.mode in ("lobby", "waiting", "eliminated", "champion")
        chat_ready = show_chat and self.app.chat_mesh is not None
        try:
            self.query_one("#chat_log").display = show_chat
            inp = self.query_one("#chat_input", Input)
            had_focus = inp.has_focus  # check BEFORE hiding; display=False clears has_focus
            inp.display  = show_chat
            inp.disabled = not chat_ready
            if not show_chat:
                # returning to menu/creating/tournaments — always clear any lingering focus
                self.set_focus(None)
            elif had_focus and not chat_ready:
                self.set_focus(None)
        except Exception:
            pass

    def _rewire_chat(self) -> None:
        """Re-attach MenuTorneioScreen's on_message handler after GameScreen clears it."""
        if self.app.chat_mesh:
            self.app.chat_mesh.on_message = lambda sid, txt: self.call_later(
                self._on_chat_message, sid, txt
            )

    def _on_chat_message(self, sender_id: str, text: str) -> None:
        try:
            self.query_one("#chat_log", RichLog).write(f"[cyan]{sender_id}:[/] {text}")
        except Exception:
            pass

    @on(Input.Submitted, "#chat_input")
    def on_chat(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or not self.app.chat_mesh:
            return
        asyncio.create_task(self.app.chat_mesh.send_all(text))
        self.query_one("#chat_log", RichLog).write(
            f"[yellow]{self.app.player_id}:[/] {text}"
        )
        self.query_one("#chat_input", Input).value = ""

    def action_select(self) -> None:
        if self.mode == "tournaments":
            if self._tourney_items:
                if not self.app.broker_ok():
                    self.notify("O broker não se encontra ativo.", severity="error")
                    return
                tid, data = self._tourney_items[self.tourney_cursor]
                self.join_tournament(tid, data.get("max_players", 4), host_id=data.get("host", ""))
        elif self.cursor == 0:
            self.mode = "creating"
        else:
            # Entrar em torneio (muda o modo para listar torneios disponíveis)
            self.mode = "tournaments"
            self.tourney_cursor = 0

    def action_cursor_up(self) -> None:
        if self.mode == "tournaments":
            self.tourney_cursor = max(0, self.tourney_cursor - 1)
        else:
            self.cursor = max(0, self.cursor - 1)

    def action_cursor_down(self) -> None:
        if self.mode == "tournaments":
            self.tourney_cursor = min(max(0, len(self._tourney_items) - 1), self.tourney_cursor + 1)
        else:
            self.cursor = min(len(self._OPTIONS) - 1, self.cursor + 1)

    # (action_select already implemented above; duplicate removed)

    def action_back(self) -> None:
        if self.mode == "waiting":
            return  # comprometido com o torneio — não pode sair a meio da ronda
        elif self.mode == "creating":
            self.mode = "menu"
        elif self.mode == "tournaments":
            self.mode = "menu"
        elif self.mode == "lobby":
            tid = self.current_tournament_id
            if tid:
                lobby_filter = f"naval/tournament/LOBBY/{tid}/+"
                self.app.client.message_callback_remove(lobby_filter)
                self.app.client.unsubscribe(lobby_filter)
                if self._my_lobby_topic:
                    self.app.client.publish(self._my_lobby_topic, b"", retain=True)
                if self._i_am_host:
                    self.app.client.publish(f"naval/tournament/{tid}", b"", retain=True)
            if self.app.chat_mesh:
                asyncio.create_task(self.app.chat_mesh.stop())
                self.app.chat_mesh = None
            self.players.clear()
            self._tournament_started = False
            self._i_am_host = False
            self.current_tournament_id = None
            self._my_lobby_topic = None
            self.mode = "menu"
        elif self.mode in ("eliminated", "champion"):
            self.active_players      = []
            self.round               = 1
            self.max_rounds          = 0
            self.round_winners       = {}
            self._round_event        = None
            self._tournament_started = False
            self._bracket_history    = []
            self.players.clear()
            self.current_tournament_id = None
            self._my_lobby_topic       = None
            self.mode = "menu"
        else:
            self.app.pop_screen()

    def on_key(self, event) -> None:
        if self.mode == "creating" and event.key in ("4", "8"):
            event.stop()
            self._create_tournament(int(event.key))

    def _create_tournament(self, max_players: int) -> None:
        if not self.app.broker_ok():
            self.notify("O broker não se encontra ativo.", severity="error")
            return
        tournament_id = str(uuid.uuid4())
        self.app.client.publish(
            f"naval/tournament/{tournament_id}",
            json.dumps({
                "host":        self.app.player_id,
                "max_players": max_players,
            }),
            retain=True,
        )
        self.join_tournament(tournament_id, max_players, host_id=self.app.player_id)
        self._i_am_host = True  # must be set after join_tournament (which resets it)


# ── GameScreen ────────────────────────────────────────────────────

class GameScreen(Screen):

    CSS = """
    GameScreen      { layout: vertical; }
    ContentSwitcher { height: 1fr; }
    #attack, #waiting { padding: 1 2; }
    #log            { height: 10; border: solid $primary; }
    #cursor_label   { margin-top: 1; }
    #chat_log       { height: 6; border: solid $accent; }
    #chat_input     { }
    """

    BINDINGS = [
        ("up",    "cursor_up",    ""),
        ("down",  "cursor_down",  ""),
        ("left",  "cursor_left",  ""),
        ("right", "cursor_right", ""),
        ("enter", "fire",         "Atacar"),
        ("r",     "surrender",    "Render"),
    ]

    def __init__(
        self,
        player_id:    str,
        game_id:      str,
        my_board:     Board,
        is_host:      bool,
        addr_or_port,
        server_info   = None,
        opponent_id:  Optional[str] = None,
        chat_mesh:    Optional[ChatMesh] = None,
    ) -> None:
        super().__init__()
        self.player_id    = player_id
        self.game_id      = game_id
        self.my_board     = my_board
        self.is_host      = is_host
        self.addr_or_port = addr_or_port
        self.server_info  = server_info
        self.game_ui      = GameUI()
        self.tracking:    dict[Cell, str] = {}
        self._game_ended  = False
        self._my_turn     = False
        self._cursor      = [0, 0]
        self._winner:     str | None = None
        self.opponent_id  = opponent_id
        self.chat_mesh    = chat_mesh

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ContentSwitcher(initial="waiting"):
            with Vertical(id="attack"):
                yield Static("", id="tracking_board")
                yield Static("", id="cursor_label")
            with Vertical(id="waiting"):
                yield Static("", id="my_board_display")
                yield Static("[dim]À espera de ligação e adversário…[/]", id="wait_status")
        yield RichLog(id="log", markup=True, highlight=False)
        yield RichLog(id="chat_log", markup=True, highlight=False)
        yield Input(placeholder="Chat…", id="chat_input")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Batalha Naval — {self.player_id}"
        self.query_one("#log", RichLog).can_focus = False
        self._wire_ui()
        self._render_boards()
        if self.chat_mesh:
            # Save the existing handler (MenuTorneioScreen's) so on_unmount can restore it.
            self._chat_prev_handler = self.chat_mesh.on_message
            self.chat_mesh.on_message = lambda sid, txt: self.call_later(
                self._on_chat_message, sid, txt
            )
        else:
            self.query_one("#chat_log").display  = False
            self.query_one("#chat_input").display = False
        if self.is_host:
            self._run_host()
        else:
            self._run_guest()

    def on_unmount(self) -> None:
        if self.chat_mesh:
            # Restore whatever handler was active before this screen mounted.
            # Setting None would race with _rewire_chat(); restoring exactly what we saved is safe.
            self.chat_mesh.on_message = self._chat_prev_handler

    def _wire_ui(self) -> None:
        ui = self.game_ui
        ui.on_log      = lambda msg:         self.call_later(self._log, msg)
        ui.on_tracking = lambda x, y, o, s: self.call_later(self._on_tracking, x, y, o, s)
        ui.on_my_board = lambda x, y, o:     self.call_later(self._render_boards)
        ui.on_turn     = lambda t:            self.call_later(self._set_turn, t)

    def _log(self, msg: str) -> None:
        self.query_one("#log", RichLog).write(msg)

    def _on_tracking(self, x: int, y: int, outcome: str, ship: str | None) -> None:
        self.tracking[(x, y)] = outcome
        self._render_boards()

    def _set_turn(self, my_turn: bool) -> None:
        self._my_turn = my_turn
        self.query_one(ContentSwitcher).current = "attack" if my_turn else "waiting"
        self._render_boards()

    def _render_boards(self) -> None:
        x, y = self._cursor
        cursor = (x, y) if self._my_turn else None
        self.query_one("#tracking_board",   Static).update(_render_tracking(self.tracking, cursor))
        self.query_one("#my_board_display", Static).update(_render_my_board(self.my_board))
        coord = f"{chr(ord('A') + x)}{y + 1}"
        label = f"Alvo: [bold cyan]{coord}[/]  [dim](↑↓←→ mover  Enter atacar  R render)[/]" if self._my_turn else ""
        self.query_one("#cursor_label", Static).update(label)

    def action_cursor_up(self)    -> None:
        if self._my_turn: self._cursor[1] = max(0, self._cursor[1] - 1); self._render_boards()

    def action_cursor_down(self)  -> None:
        if self._my_turn: self._cursor[1] = min(GRID_SIZE - 1, self._cursor[1] + 1); self._render_boards()

    def action_cursor_left(self)  -> None:
        if self._my_turn: self._cursor[0] = max(0, self._cursor[0] - 1); self._render_boards()

    def action_cursor_right(self) -> None:
        if self._my_turn: self._cursor[0] = min(GRID_SIZE - 1, self._cursor[0] + 1); self._render_boards()

    def action_fire(self) -> None:
        if self._game_ended:
            self.dismiss(self._winner)
            return
        if self._my_turn:
            x, y = self._cursor
            self.game_ui.submit_attack(f"{chr(ord('A') + x)}{y + 1}")

    def action_surrender(self) -> None:
        if self._my_turn:
            self.game_ui.submit_attack("render")

    def _on_chat_message(self, sender_id: str, text: str) -> None:
        try:
            self.query_one("#chat_log", RichLog).write(f"[cyan]{sender_id}:[/] {text}")
        except Exception:
            pass

    @on(Input.Submitted, "#chat_input")
    def on_chat(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or not self.chat_mesh:
            return
        asyncio.create_task(self.chat_mesh.send_all(text))
        self.query_one("#chat_log", RichLog).write(
            f"[yellow]{self.player_id}:[/] {text}"
        )
        self.query_one("#chat_input", Input).value = ""

    def on_key(self, event) -> None:
        if self._game_ended and event.key == "enter":
            event.stop()
            self.dismiss(self._winner)

    def _end_game(self, winner: Optional[str]) -> None:
        self._game_ended = True
        self._my_turn    = False
        self._winner     = winner

        if winner == self.player_id:
            self.report_victory(self.player_id, self.opponent_id)
            self.query_one("#log", RichLog).write("[bold green]Vitória! Resultado reportado.[/]")
        elif winner is None:
            self.query_one("#log", RichLog).write("[dim]Partida terminada.[/]")
        else:
            self.query_one("#log", RichLog).write("[bold red]Derrota.[/]")

        self.query_one("#log", RichLog).write("[dim]Prima Enter para voltar ao menu.[/]")

    def report_victory(self, winner_id: str, loser_id: str):
        result_topic = f"naval/results/{self.game_id}"
        self.app.client.publish(result_topic, json.dumps({
            "winner": winner_id,
            "loser": loser_id,
        }), retain=True)

    @work(exclusive=True, exit_on_error=False)
    async def _run_host(self) -> None:
        try:
            winner = await run_as_host(
                self.player_id, self.game_id, self.addr_or_port, self.my_board, self.game_ui,
                server_info=self.server_info,
            )
        except Exception:
            winner = self.player_id  # unhandled error: we're still here → we win
        self.call_later(self._end_game, winner)

    @work(exclusive=True, exit_on_error=False)
    async def _run_guest(self) -> None:
        try:
            winner = await run_as_guest(
                self.player_id, self.game_id, self.addr_or_port, self.my_board, self.game_ui
            )
        except Exception:
            winner = self.player_id  # unhandled error: we're still here → we win
        self.call_later(self._end_game, winner)


# ── App ───────────────────────────────────────────────────────────

class BatalhaNavalApp(App):

    player_id:    str              = ""
    my_addr:      str              = ""
    client:       mqtt.Client      = None
    _game_topics: set              = set()
    chat_mesh:    ChatMesh | None  = None

    async def init_p2p_game(self, opponent_id: str, tournament_id: str) -> str | None:
        """Run one tournament match. Host binds a fresh random port and advertises
        it via MQTT; guest waits for that address before connecting."""
        is_host = self.player_id < opponent_id
        game_id = f"{min(self.player_id, opponent_id)}-{max(self.player_id, opponent_id)}"
        game_topic = f"naval/tournament/{tournament_id}/game/{game_id}"

        if is_host:
            # Fresh random port every match — same pattern as 1v1 host
            server_info = await start_host_server(0)
            server, _   = server_info
            actual_port = server.sockets[0].getsockname()[1]
            actual_addr = f"{get_local_ip()}:{actual_port}"
            self.client.publish(game_topic, json.dumps({"addr": actual_addr}), retain=True)

            my_board = await self.push_screen_wait(PlacementScreen())
            winner   = await self.push_screen_wait(GameScreen(
                player_id=self.player_id, game_id=game_id, my_board=my_board,
                is_host=True, addr_or_port=actual_port, server_info=server_info,
                opponent_id=opponent_id, chat_mesh=self.chat_mesh,
            ))
            self.client.publish(game_topic, b"", retain=True)   # clean up after game
            # None means guest never connected (timed out) → we win by default
            return winner if winner is not None else self.player_id
        else:
            # Wait for host to publish their actual TCP address, then connect
            host_addr = await self._wait_game_addr(game_topic)
            if host_addr is None:
                # Host never showed up → guest wins by default (no game needed)
                return self.player_id

            my_board = await self.push_screen_wait(PlacementScreen())
            winner   = await self.push_screen_wait(GameScreen(
                player_id=self.player_id, game_id=game_id, my_board=my_board,
                is_host=False, addr_or_port=host_addr, opponent_id=opponent_id,
                chat_mesh=self.chat_mesh,
            ))
            # None means connection failed mid-game → we're still here → we win
            return winner if winner is not None else self.player_id

    async def _wait_game_addr(self, topic: str, timeout: float = 60.0) -> str | None:
        """Subscribe to `topic` and return the host's TCP address once published.
        Returns None if the host never appears within `timeout` seconds."""
        ready   = asyncio.Event()
        box: list[str | None] = [None]

        def _cb(client, userdata, msg):
            payload = msg.payload.decode()
            if not payload:
                return
            try:
                addr = json.loads(payload).get("addr")
                if addr:
                    box[0] = addr
                    self.call_from_thread(ready.set)
            except (json.JSONDecodeError, TypeError):
                pass

        self.client.message_callback_add(topic, _cb)
        self.client.subscribe(topic)
        try:
            await asyncio.wait_for(ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self.client.message_callback_remove(topic)
            self.client.unsubscribe(topic)
        return box[0]

    def on_mount(self) -> None:
        self.push_screen(SetupScreen())

    def broker_ok(self) -> bool:
        return self.client is not None and self.client.is_connected()

    def setup_mqtt(self) -> None:
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, self.player_id)
        self.client.connect(BROKER, PORT_MQTT)
        self.client.loop_start()
        self.client.publish(f"naval/players/{self.player_id}", "online", retain=True)
        # Clear any stale matchmaking entry left by a previous crash of this player
        self.client.publish(f"naval/matchmaking/{self.player_id}", b"", retain=True)

    def _teardown_mqtt(self) -> None:
        if not self.client:
            return
        try:
            self.client.publish(f"naval/players/{self.player_id}", "offline", retain=True)
            # Remove retained game topics so stale listings don't appear after a crash
            for topic in self._game_topics:
                self.client.publish(topic, b"", retain=True)
            self._game_topics.clear()
            self.client.loop_stop()
            self.client.disconnect()
        finally:
            self.client = None

    def on_unmount(self) -> None:
        self._teardown_mqtt()

    def on_exit(self) -> None:
        self._teardown_mqtt()

import json
import uuid
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
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


# ── Board rendering ───────────────────────────────────────────────

_EMPTY = "[dim]·[/]"
_MISS  = "[blue]○[/]"
_HIT   = "[yellow]×[/]"
_SUNK  = "[red]■[/]"
_SHIP  = "[green]█[/]"


def _render_tracking(tracking: dict) -> str:
    header = "     " + "   ".join("ABCDEFGHIJ")
    rows = [header]
    for y in range(GRID_SIZE):
        cells = []
        for x in range(GRID_SIZE):
            o = tracking.get((x, y))
            if   o == "miss": cells.append(_MISS)
            elif o == "hit":  cells.append(_HIT)
            elif o == "sunk": cells.append(_SUNK)
            else:             cells.append(_EMPTY)
        rows.append(f"{y + 1:>2}   " + "   ".join(cells))
    return "\n\n".join(rows)


def _render_my_board(my_board: Board) -> str:
    ship_cells: set[Cell] = set()
    hit_cells:  set[Cell] = set()
    for ship in my_board.ships:
        ship_cells |= ship.position
        hit_cells  |= ship.hits

    header = "     " + "   ".join("ABCDEFGHIJ")
    rows = [header]
    for y in range(GRID_SIZE):
        cells = []
        for x in range(GRID_SIZE):
            cell = Cell(x, y)
            if   cell in hit_cells:  cells.append(_HIT)
            elif cell in ship_cells: cells.append(_SHIP)
            else:                    cells.append(_EMPTY)
        rows.append(f"{y + 1:>2}   " + "   ".join(cells))
    return "\n\n".join(rows)


# ── SetupScreen ───────────────────────────────────────────────────

class SetupScreen(Screen):

    CSS = """
    SetupScreen { align: center middle; }
    #title { margin-bottom: 1; }
    .lbl   { color: cyan; margin-top: 1; margin-bottom: 0; }
    Input  { width: 36; border: tall $panel; padding: 0 1; margin-bottom: 0; }
    """

    def compose(self) -> ComposeResult:
        yield Static(TITLE_ART, id="title")
        yield Static("Nome de jogador", classes="lbl")
        yield Input(id="name_input")

    @on(Input.Submitted, "#name_input")
    def on_enter(self) -> None:
        name = self.query_one("#name_input", Input).value.strip()
        if name:
            self.app.player_id = name
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
        self.app.client.subscribe("naval/games/#")
        self._refresh()

    def on_show(self) -> None:
        self.app.client.on_message = self._on_mqtt_message

    def on_unmount(self) -> None:
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
                lines.append(f"{prefix}[cyan]{game['host']}[/]")

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
        if idx >= len(self._game_items):
            return
        game_id, game = self._game_items[idx]
        self.app.client.publish(f"naval/games/{game_id}", "closed", retain=True)
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
        game_id     = str(uuid.uuid4())
        server_info = await start_host_server(0)
        server, _   = server_info
        actual_port = server.sockets[0].getsockname()[1]
        actual_addr = f"{get_local_ip()}:{actual_port}"
        self.app.client.publish(
            f"naval/games/{game_id}",
            json.dumps({"host": self.app.player_id, "addr": actual_addr}),
            retain=True,
        )
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
        ("escape", "cancel",  "Cancelar"),
        ("enter",  "confirm", "Confirmar"),
    ]

    status: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Static("", id="mm_status")

    def on_mount(self) -> None:
        self._queue:      dict         = {}
        self._found:      tuple | None = None
        self._host_addr:  str | None   = None
        self._cancelled:  bool         = False
        self._search()

    def on_unmount(self) -> None:
        self.app.client.unsubscribe("naval/matchmaking")

    def watch_status(self, val: str) -> None:
        self.query_one("#mm_status", Static).update(val)

    def _handle_matchmaking_message(self, data: dict) -> None:
        player_id = self.app.player_id

        if data.get("command") == "clear":
            host_id   = data.get("host_id")
            host_addr = data.get("host_addr")
            if host_id != player_id and player_id < host_id:
                self._queue["__host__"] = (host_id, host_addr)
                self._host_addr = host_addr
            else:
                self._queue.clear()
            return

        if data.get("command") == "cancel":
            self._queue.pop(data.get("player_id"), None)
            return

        if data.get("player_id") != player_id:
            self._queue[data["player_id"]] = data["addr"]

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        payload = msg.payload.decode()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        self.app.call_from_thread(self._handle_matchmaking_message, data)

    @work
    async def _search(self) -> None:
        for n in range(3, 0, -1):
            self.status = f"[dim]A entrar na fila em {n}…[/]"
            await asyncio.sleep(1)

        if self._cancelled:
            return

        player_id = self.app.player_id
        my_addr   = self.app.my_addr

        self.app.client.subscribe("naval/matchmaking")
        self.app.client.on_message = self._on_mqtt_message
        self.app.client.publish(
            "naval/matchmaking",
            json.dumps({"player_id": player_id, "addr": my_addr}),
        )
        self.status = "[dim]À procura de adversário… (Esc para cancelar)[/]"

        while not self._cancelled:
            await asyncio.sleep(0.25)
            if "__host__" in self._queue:
                opp_id, opp_addr = self._queue.pop("__host__")
                self._found = (opp_id, opp_addr, False)
                break
            elif self._queue:
                opp_id, opp_addr = next(iter(self._queue.items()))
                self._queue.clear()
                self._found = (opp_id, opp_addr, player_id > opp_id)
                break

        if self._found and not self._cancelled:
            opp_id = self._found[0]
            self.status = f"[green]Adversário encontrado: {opp_id}!\n(Enter para entrar)[/]"

    def action_cancel(self) -> None:
        self._cancelled = True
        self.app.client.publish("naval/matchmaking", json.dumps({
            "command":   "cancel",
            "player_id": self.app.player_id,
        }))
        self.app.pop_screen()

    @work
    async def action_confirm(self) -> None:
        if self._found is None:
            return
        self._cancelled = True
        opp_id, opp_addr, i_am_host = self._found
        player_id = self.app.player_id
        game_id   = f"{min(player_id, opp_id)}-{max(player_id, opp_id)}"

        if i_am_host:
            server_info = await start_host_server(0)
            server, _   = server_info
            actual_port = server.sockets[0].getsockname()[1]
            actual_addr = f"{get_local_ip()}:{actual_port}"
            self.app.client.publish("naval/matchmaking", json.dumps({
                "command":   "clear",
                "host_id":   player_id,
                "host_addr": actual_addr,
            }))
            my_board = await self.app.push_screen_wait(PlacementScreen())
            await self.app.push_screen_wait(GameScreen(
                player_id    = player_id,
                game_id      = game_id,
                my_board     = my_board,
                is_host      = True,
                addr_or_port = actual_port,
                server_info  = server_info,
            ))
            self.app.pop_screen()
        else:
            for _ in range(150):
                if self._host_addr is not None:
                    break
                await asyncio.sleep(0.1)
            else:
                self.status = "[red]Anfitrião não respondeu. Tente novamente.[/]"
                return
            my_board = await self.app.push_screen_wait(PlacementScreen())
            await self.app.push_screen_wait(GameScreen(
                player_id    = player_id,
                game_id      = game_id,
                my_board     = my_board,
                is_host      = False,
                addr_or_port = self._host_addr,
            ))
            self.app.pop_screen()


# ── MenuTorneioScreen ─────────────────────────────────────────────

class MenuTorneioScreen(Screen):

    CSS = """
    MenuTorneioScreen { align: center middle; }
    #display   { margin-bottom: 1; }
    #max_input { width: 30; border: tall $panel; padding: 0 1; }
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

    def compose(self) -> ComposeResult:
        yield Static("", id="display")
        yield Input(placeholder="Jogadores: 4 ou 8", id="max_input")

    def on_mount(self) -> None:
        self._tourney_items: list[tuple[str, dict]] = []
        self.app.client.subscribe("naval/tournament/#")
        self.app.client.on_message = self._on_mqtt_message
        self.query_one("#max_input", Input).display = False
        self._refresh()

    def on_unmount(self) -> None:
        self.app.client.unsubscribe("naval/tournament/#")

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        tournament_id = msg.topic.split("/")[-1]
        payload       = msg.payload.decode()
        tournaments   = dict(self.available_tournaments)
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

    def watch_available_tournaments(self, tournaments: dict) -> None:
        self._tourney_items = list(tournaments.items())
        self._refresh()

    def watch_cursor(self)         -> None: self._refresh()
    def watch_tourney_cursor(self) -> None: self._refresh()
    def watch_mode(self)           -> None: self._refresh()

    def _refresh(self) -> None:
        in_tourneys = self.mode == "tournaments"
        lines = ["\n[cyan]Torneio[/]\n"]

        for i, opt in enumerate(self._OPTIONS):
            if in_tourneys and i == 1:
                prefix = "[yellow]►[/] "
            elif not in_tourneys and i == self.cursor:
                prefix = "[yellow]►[/] "
            else:
                prefix = "  "
            lines.append(f"{prefix}[yellow]{i + 1}.[/] {opt}")

        lines += ["", "[bold]Torneios disponíveis:[/]"]
        if not self._tourney_items:
            lines.append("  [dim]Nenhum torneio disponível[/]")
        else:
            for i, (_, t) in enumerate(self._tourney_items):
                prefix = "[yellow]►[/] " if (in_tourneys and i == self.tourney_cursor) else "  "
                slots  = f"{len(t.get('players', []))}/{t.get('max_players', 4)}"
                lines.append(f"{prefix}[cyan]{t['host']}[/] [dim]({slots})[/]")

        self.query_one("#display", Static).update("\n".join(lines))

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

    def action_select(self) -> None:
        if self.mode == "tournaments":
            pass  # TODO (P2): bracket join logic
        elif self.cursor == 0:
            inp = self.query_one("#max_input", Input)
            inp.display = True
            inp.focus()
        else:
            self.mode = "tournaments"
            self.tourney_cursor = 0

    def action_back(self) -> None:
        if self.mode == "tournaments":
            self.mode = "menu"
        else:
            self.app.pop_screen()

    @on(Input.Submitted, "#max_input")
    def on_max_players(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        inp = self.query_one("#max_input", Input)
        inp.display = False
        inp.value   = ""
        if val not in ("4", "8"):
            return
        tournament_id = str(uuid.uuid4())
        self.app.client.publish(
            f"naval/tournament/{tournament_id}",
            json.dumps({
                "host":        self.app.player_id,
                "addr":        self.app.my_addr,
                "max_players": int(val),
                "players":     [self.app.player_id],
            }),
            retain=True,
        )


# ── GameScreen ────────────────────────────────────────────────────

class GameScreen(Screen):

    CSS = """
    GameScreen      { layout: vertical; }
    ContentSwitcher { height: 1fr; }
    #attack, #waiting { padding: 1 2; }
    #log            { height: 10; border: solid $primary; }
    #attack_input   { width: 40; margin-top: 1; }
    """

    def __init__(
        self,
        player_id:    str,
        game_id:      str,
        my_board:     Board,
        is_host:      bool,
        addr_or_port,
        server_info   = None,
    ) -> None:
        super().__init__()
        self.player_id    = player_id
        self.game_id      = game_id
        self.my_board     = my_board
        self.is_host      = is_host
        self.addr_or_port = addr_or_port
        self.server_info  = server_info
        self.game_ui      = GameUI()
        self.tracking:    dict[tuple, str] = {}
        self._game_ended  = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with ContentSwitcher(initial="waiting"):
            with Vertical(id="attack"):
                yield Static("", id="tracking_board")
            with Vertical(id="waiting"):
                yield Static("", id="my_board_display")
                yield Static("[dim]À espera de ligação e adversário…[/]", id="wait_status")
        yield Input(
            placeholder="Coordenada (ex: A5)  ou  render (rende)",
            id="attack_input",
            disabled=True,
        )
        yield RichLog(id="log", markup=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Batalha Naval — {self.player_id}"
        self._wire_ui()
        self._render_boards()
        if self.is_host:
            self._run_host()
        else:
            self._run_guest()

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
        self.query_one(ContentSwitcher).current = "attack" if my_turn else "waiting"
        inp = self.query_one("#attack_input", Input)
        inp.disabled = not my_turn
        if my_turn:
            inp.focus()
        self._render_boards()

    def _render_boards(self) -> None:
        self.query_one("#tracking_board",   Static).update(_render_tracking(self.tracking))
        self.query_one("#my_board_display", Static).update(_render_my_board(self.my_board))

    @on(Input.Submitted, "#attack_input")
    def on_attack(self, event: Input.Submitted) -> None:
        coord = event.value.strip()
        if coord:
            self.game_ui.submit_attack(coord)
            self.query_one("#attack_input", Input).value = ""

    def on_key(self, event) -> None:
        if self._game_ended and event.key == "enter":
            event.stop()
            self.dismiss()

    def _end_game(self) -> None:
        self._game_ended = True
        self.query_one("#attack_input", Input).disabled = True
        self.query_one("#log", RichLog).write(
            "[dim]Prima Enter para voltar ao menu principal.[/]"
        )

    @work(exclusive=True)
    async def _run_host(self) -> None:
        await run_as_host(
            self.player_id, self.game_id, self.addr_or_port, self.my_board, self.game_ui,
            server_info=self.server_info,
        )
        self._end_game()

    @work(exclusive=True)
    async def _run_guest(self) -> None:
        await run_as_guest(
            self.player_id, self.game_id, self.addr_or_port, self.my_board, self.game_ui
        )
        self._end_game()


# ── App ───────────────────────────────────────────────────────────

class BatalhaNavalApp(App):

    player_id: str         = ""
    my_addr:   str         = ""
    client:    mqtt.Client = None

    def on_mount(self) -> None:
        self.push_screen(SetupScreen())

    def setup_mqtt(self) -> None:
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, self.player_id)
        self.client.connect(BROKER, PORT_MQTT)
        self.client.loop_start()
        self.client.publish(f"naval/players/{self.player_id}", "online", retain=True)

    def _teardown_mqtt(self) -> None:
        if not self.client:
            return
        try:
            self.client.publish(f"naval/players/{self.player_id}", "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        finally:
            self.client = None

    def on_unmount(self) -> None:
        self._teardown_mqtt()

    def on_exit(self) -> None:
        self._teardown_mqtt()

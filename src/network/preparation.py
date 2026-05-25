from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Footer, Header
from textual.reactive import reactive

from game.ship_factory import create_ship, FLEET
from game.board import Board, GRID_SIZE
from game.types import Cell

SHIPS = list(FLEET.keys())  # placement order: Carrier → Patrol Boat


# ── Screen (embedded in the main app via push_screen_wait) ───────

class PlacementScreen(Screen):

    CSS = """
    Screen {
        layout: horizontal;
    }
    #grid {
        width: 48;
        padding: 1 2;
    }
    #sidebar {
        width: 1fr;
        padding: 1 2;
    }
    """

    BINDINGS = [
        ("up",    "move_up",    "Cima"),
        ("down",  "move_down",  "Baixo"),
        ("left",  "move_left",  "Esquerda"),
        ("right", "move_right", "Direita"),
        ("r",     "rotate",     "Rodar"),
        ("enter", "place",      "Colocar"),
    ]

    cursor_x:   reactive[int]  = reactive(0)
    cursor_y:   reactive[int]  = reactive(0)
    horizontal: reactive[bool] = reactive(True)
    ship_index: reactive[int]  = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.placed_board = Board(ships=[])

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="grid")
        yield Static(id="sidebar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Batalha Naval — Colocação de Navios"
        self._refresh_all()

    # ── Helpers ───────────────────────────────────────────────────

    def _ship_name(self) -> str:
        return SHIPS[self.ship_index]

    def _ship_size(self) -> int:
        return FLEET[self._ship_name()]

    def _preview_cells(self) -> list[Cell]:
        if self.ship_index >= len(SHIPS):
            return []
        size = self._ship_size()
        if self.horizontal:
            return [Cell(self.cursor_x + i, self.cursor_y) for i in range(size)]
        else:
            return [Cell(self.cursor_x, self.cursor_y + i) for i in range(size)]

    def _placed_cells(self) -> set[Cell]:
        result: set[Cell] = set()
        for ship in self.placed_board.ships:
            result |= ship.position
        return result

    def _preview_valid(self) -> bool:
        placed = self._placed_cells()
        for cell in self._preview_cells():
            if not (0 <= cell.x < GRID_SIZE and 0 <= cell.y < GRID_SIZE):
                return False
            if cell in placed:
                return False
        return True

    # ── Rendering ─────────────────────────────────────────────────

    def _refresh_all(self) -> None:
        self._render_grid()
        self._render_sidebar()

    def _render_grid(self) -> None:
        preview = set(self._preview_cells())
        placed  = self._placed_cells()
        valid   = self._preview_valid()

        header = "     " + "   ".join("ABCDEFGHIJ")
        rows = [header]

        for y in range(GRID_SIZE):
            cells = []
            for x in range(GRID_SIZE):
                cell = Cell(x, y)
                if cell in preview:
                    cells.append("[cyan]░[/]" if valid else "[red]░[/]")
                elif cell in placed:
                    cells.append("[green]█[/]")
                else:
                    cells.append("[dim]·[/]")
            rows.append(f"{y + 1:>2}   " + "   ".join(cells))

        self.query_one("#grid", Static).update("\n\n".join(rows))

    def _render_sidebar(self) -> None:
        lines = ["[bold]Navios:[/]\n"]
        for i, name in enumerate(SHIPS):
            size = FLEET[name]
            if i < self.ship_index:
                lines.append(f"  [dim]✓ {name} ({size})[/]")
            elif i == self.ship_index:
                lines.append(f"  [cyan bold]▶ {name} ({size})[/]")
            else:
                lines.append(f"    {name} ({size})")

        orientation = "[cyan]Horizontal →[/]" if self.horizontal else "[cyan]Vertical ↓[/]"
        lines += [
            "",
            f"Orientação: {orientation}",
            "",
            "[dim]↑↓←→  Mover[/]",
            "[dim]R      Rodar[/]",
            "[dim]Enter  Colocar[/]",
        ]

        self.query_one("#sidebar", Static).update("\n".join(lines))

    # ── Watchers ──────────────────────────────────────────────────

    def watch_cursor_x(self)   -> None: self._render_grid()
    def watch_cursor_y(self)   -> None: self._render_grid()
    def watch_horizontal(self) -> None: self._refresh_all()
    def watch_ship_index(self) -> None: self._refresh_all()

    # ── Actions ───────────────────────────────────────────────────

    def action_move_up(self)    -> None: self.cursor_y = max(0, self.cursor_y - 1)
    def action_move_down(self)  -> None: self.cursor_y = min(GRID_SIZE - 1, self.cursor_y + 1)
    def action_move_left(self)  -> None: self.cursor_x = max(0, self.cursor_x - 1)
    def action_move_right(self) -> None: self.cursor_x = min(GRID_SIZE - 1, self.cursor_x + 1)

    def action_rotate(self) -> None:
        self.horizontal = not self.horizontal

    def action_place(self) -> None:
        if not self._preview_valid():
            return
        ship = create_ship(
            self._ship_name(),
            Cell(self.cursor_x, self.cursor_y),
            horizontal=self.horizontal,
        )
        self.placed_board.ships.append(ship)
        self.ship_index += 1
        if self.ship_index >= len(SHIPS):
            self.dismiss(self.placed_board)  # return board to caller


# ── Standalone wrapper (for testing without the main app) ─────────

class PlacementApp(App):

    def on_mount(self) -> None:
        self.push_screen(PlacementScreen(), self._on_done)

    def _on_done(self, result: Board) -> None:
        self.placed_board = result
        self.exit()


async def run_placement() -> Board:
    app = PlacementApp()
    await app.run_async()
    return app.placed_board

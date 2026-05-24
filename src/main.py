import asyncio
import aioconsole
import paho.mqtt.client as mqtt
import json
import uuid
import socket
from rich.console import Console
from rich.text import Text
from network.tcp_game import run_as_host, run_as_guest

BROKER = "localhost"
PORT = 1883

console = Console()

TITLE = """
██████╗  █████╗ ████████╗ █████╗ ██╗     ██╗  ██╗ █████╗ 
██╔══██╗██╔══██╗╚══██╔══╝██╔══██╗██║     ██║  ██║██╔══██╗
██████╔╝███████║   ██║   ███████║██║     ███████║███████║
██╔══██╗██╔══██║   ██║   ██╔══██║██║     ██╔══██║██╔══██║
██████╔╝██║  ██║   ██║   ██║  ██║███████╗██║  ██║██║  ██║
╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝
"""

available_games       = {}
available_tournaments = {}
matchmaking_queue     = {}

# ── MQTT ──────────────────────────────────────────────────────────

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def setup_mqtt(player_id: str) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, player_id)
    client.connect(BROKER, PORT)
    client.loop_start()
    return client


def on_games_message(client, userdata, msg):
    game_id = msg.topic.split("/")[-1]
    payload = msg.payload.decode()

    if payload == "closed":
        available_games.pop(game_id, None)
    else:
        try:
            available_games[game_id] = json.loads(payload)
        except json.JSONDecodeError:
            pass


def on_tournament_message(client, userdata, msg):
    tournment_id = msg.topic.split("/")[-1]
    payload = msg.payload.decode()
    
    if payload == "closed":
        available_tournaments.pop(tournment_id, None)
    else:
        try:
            available_tournaments[tournment_id] = json.loads(payload)
        except json.JSONDecodeError:
            pass


def on_matchmaking_message(client, userdata, msg, my_id):
    payload = msg.payload.decode()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return

    if data.get("command") == "clear":
        host_id   = data.get("host_id")
        host_addr = data.get("host_addr")
        if host_id != my_id and my_id < host_id:
            matchmaking_queue["__host__"] = (host_id, host_addr)
        else:
            matchmaking_queue.clear()
        return

    if data.get("command") == "cancel":
        matchmaking_queue.pop(data.get("player_id"), None)
        return

    if data.get("player_id") != my_id:
        matchmaking_queue[data["player_id"]] = data["addr"]

# ── UI ────────────────────────────────────────────────────────────

def show_title():
    console.print(Text(TITLE, style="bold cyan"))


def show_menu(options: list[str], title: str):
    console.print(f"\n[cyan]{title}[/]")

    for i, opt in enumerate(options, 1):
        console.print(f"[yellow]{i}.[/] {opt}")

    console.print()


def show_games_list(my_id: str):
    items = [
        (game_id, game) for game_id, game in available_games.items()
        if game.get("host") != my_id
    ]

    if not items:
        console.print("[dim]Sem partidas disponíveis.[/]")
        return None
    
    for i, (game_id, game) in enumerate(items, 1):
        console.print(f"[yellow]{i}.[/] {game['host']}")
    return items


def show_tournaments_list(my_id: str):
    items = [
        (tournment_id, tournament) for tournment_id, tournament in available_tournaments.items()
        if tournament.get("host") != my_id
    ]

    if not items:
        console.print("[dim]Sem torneios disponíveis.[/]")
        return None
    
    for i, (tournment_id, tournament) in enumerate(items, 1):
        slots = f"{len(tournament.get('players', []))}/{tournament.get('max_players', 4)}"
        console.print(f"  [yellow]{i}.[/] {tournament['host']} [dim]({slots})[/]")
    return items

# ── Matchmaking ───────────────────────────────────────────────────

async def menu_matchmaking(client: mqtt.Client, player_id: str, my_addr: str):
    # Countdown visual — sem leitura de stdin, sem threads, sem cancelamento aqui.
    console.print("[dim]Matchmaking iniciado, a entrar na fila em...[/]", end=" ")
    for n in range(3, 0, -1):
        print(n, end=" ", flush=True)
        await asyncio.sleep(1)
    print()

    # Entrar na queue. O aioconsole.ainput abaixo é o ÚNICO leitor de stdin
    # durante todo este fluxo — nunca é cancelado, resolve-se sempre de forma
    # natural (utilizador prime Enter ou adversário é encontrado e ele confirma).
    client.subscribe("naval/matchmaking")
    client.on_message = make_matchmaking_callback(player_id)
    client.publish("naval/matchmaking", json.dumps({"player_id": player_id, "addr": my_addr}))
    console.print("[dim]À procura de adversário... (Enter para cancelar)[/]")

    cancel_task = asyncio.create_task(aioconsole.ainput(""))
    found = None

    try:
        while True:
            await asyncio.sleep(0.25)
            if cancel_task.done():
                break  # utilizador cancelou
            if "__host__" in matchmaking_queue:
                opp_id, opp_addr = matchmaking_queue.pop("__host__")
                found = (opp_id, opp_addr, False)
                break
            elif matchmaking_queue:
                opp_id, opp_addr = next(iter(matchmaking_queue.items()))
                matchmaking_queue.clear()
                found = (opp_id, opp_addr, player_id > opp_id)
                break
    finally:
        client.unsubscribe("naval/matchmaking")
        matchmaking_queue.clear()

    if found is None:
        client.publish("naval/matchmaking", json.dumps({
            "command": "cancel",
            "player_id": player_id,
        }))
        console.print("[yellow]Matchmaking cancelado.[/]")
        return

    opp_id, opp_addr, i_am_host = found
    console.print(f"[green]Adversário encontrado: {opp_id}! (Enter para entrar na partida)[/]")
    await cancel_task  # se ainda não completou, aguarda o Enter do utilizador — resolve natural.

    game_id = f"{min(player_id, opp_id)}-{max(player_id, opp_id)}"
    if i_am_host:
        client.publish("naval/matchmaking", json.dumps({
            "command": "clear",
            "host_id": player_id,
            "host_addr": my_addr,
        }))
        await run_as_host(player_id, game_id, int(my_addr.split(":")[1]))
    else:
        await run_as_guest(player_id, game_id, opp_addr)

def make_matchmaking_callback(my_id: str):

    def callback(client, userdata, msg):
        on_matchmaking_message(client, userdata, msg, my_id)
    return callback

# ── 1v1 ───────────────────────────────────────────────────────────

async def menu_1v1(client: mqtt.Client, player_id: str, my_addr: str):
    client.subscribe("naval/games/#")
    client.on_message = on_games_message
    try:
        while True:
            show_menu(["Criar partida", "Entrar em partida", "Matchmaking", "Voltar"], title="1v1")
            cmd = (await aioconsole.ainput("> ")).strip()
            match cmd:
                case "1":
                    game_id = str(uuid.uuid4())
                    client.publish(f"naval/games/{game_id}",
                                   json.dumps({"host": player_id, "addr": my_addr}), retain=True)
                    console.print("[dim]À espera de adversário...[/]")
                    await run_as_host(player_id, game_id, int(my_addr.split(":")[1]))
                    break
                case "2":
                    items = show_games_list(player_id)
                    if items:
                        escolha = (await aioconsole.ainput("> ")).strip()
                        try:
                            game_id, game = items[int(escolha) - 1]
                            client.publish(f"naval/games/{game_id}", "closed", retain=True)
                            console.print(f"[green]A entrar na partida de {game['host']}[/]")
                            await run_as_guest(player_id, game_id, game["addr"])
                        except (ValueError, IndexError):
                            console.print("[red]Inválido.[/]")
                case "3":
                    await menu_matchmaking(client, player_id, my_addr)
                    client.on_message = on_games_message  # restaurar callback após matchmaking
                case "4":
                    break
                case "" | _:
                    if cmd:
                        console.print("[red]Inválido.[/]")
    finally:
        client.unsubscribe("naval/games/#")


# ── Torneio ───────────────────────────────────────────────────────

async def menu_torneio(client: mqtt.Client, player_id: str, my_addr: str):
    client.subscribe("naval/tournament/#")
    client.on_message = on_tournament_message
    try:
        while True:
            show_menu(["Criar torneio", "Entrar em torneio", "Voltar"], title="Torneio")
            cmd = (await aioconsole.ainput("> ")).strip()
            match cmd:
                case "1":
                    max_p = (await aioconsole.ainput("Jogadores (4 ou 8): ")).strip()
                    if max_p not in ("4", "8"):
                        console.print("[red]Apenas 4 ou 8.[/]")
                        continue
                    tournment_id = str(uuid.uuid4())
                    client.publish(f"naval/tournament/{tournment_id}", json.dumps({
                        "host": player_id, "addr": my_addr,
                        "max_players": int(max_p), "players": [player_id]
                    }), retain=True)
                    console.print(f"[dim]À espera de {max_p} jogadores...[/]")
                    # TODO (P2): gerir bracket
                    break
                case "2":
                    items = show_tournaments_list(player_id)
                    if items:
                        escolha = (await aioconsole.ainput("> ")).strip()
                        try:
                            tournment_id, tournament = items[int(escolha) - 1]
                            console.print(f"[green]A entrar no torneio de {tournament['host']}[/]")
                            # TODO (P2): lógica de entrada
                        except (ValueError, IndexError):
                            console.print("[red]Inválido.[/]")
                case "3":
                    break
                case "" | _:
                    if cmd:
                        console.print("[red]Inválido.[/]")
    finally:
        client.unsubscribe("naval/tournament/#")


# ── Principal ─────────────────────────────────────────────────────

async def menu_principal(client: mqtt.Client, player_id: str, my_addr: str):
    while True:
        show_menu(["1v1", "Torneio", "Sair"], title="Batalha Naval")
        cmd = (await aioconsole.ainput("> ")).strip()
        
        match cmd:
            case "1": await menu_1v1(client, player_id, my_addr)
            case "2": await menu_torneio(client, player_id, my_addr)
            case "3":
                client.publish(f"naval/players/{player_id}", "offline", retain=True)
                break
            case "" | _:
                if cmd:
                    console.print("[red]Inválido.[/]")


async def main():
    show_title()
    player_id = (await aioconsole.ainput("Nome: ")).strip()
    my_port   = find_free_port()
    my_addr   = f"localhost:{my_port}"
    console.print(f"[dim]Porta atribuída: {my_port}[/]")

    client = setup_mqtt(player_id)
    client.publish(f"naval/players/{player_id}", "online", retain=True)

    await menu_principal(client, player_id, my_addr)
    client.loop_stop()

asyncio.run(main())
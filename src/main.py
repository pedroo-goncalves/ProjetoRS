import asyncio
import aioconsole
import paho.mqtt.client as mqtt

BROKER = "localhost"
PORT = 1883

def setup_mqtt(player_id):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, player_id)
    client.connect(BROKER, PORT)
    client.subscribe("naval/players/#")
    client.on_message = on_message
    client.loop_start()
    return client

def on_message(client, userdata, msg):
    print(f"\n[MQTT] {msg.topic}: {msg.payload.decode()}")

async def terminal_loop(player_id, mqtt_client):
    print(f"Bem-vindo, {player_id}. Comandos: list, invite <id>, quit")
    while True:
        cmd = await aioconsole.ainput("> ")
        parts = cmd.strip().split()
        if not parts:
            continue
        match parts[0]:
            case "quit":
                break
            case "list":
                print("Jogadores online: (ainda não implementado)")
            case "invite":
                print(f"A convidar {parts[1]}... (ainda não implementado)")
            case _:
                print(f"Comando desconhecido.")

async def main():
    player_id = input("O teu nome: ")
    mqtt_client = setup_mqtt(player_id)
    mqtt_client.publish(f"naval/players/{player_id}", "online", retain=True)
    await terminal_loop(player_id, mqtt_client)

asyncio.run(main())
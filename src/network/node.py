import socket;
import threading;
import sys;

# Game logic

def handle_game(sock, is_my_turn):
    while True:
        if is_my_turn:
            msg = input("O teu turno: ");
            sock.send(msg.encode());
            is_my_turn = False;
        else:
            data = sock.recv(1024);
            if not data:
                print("Conexão encerrada.");
                break;
            msg = data.decode();
            print(f"Adversário: {msg}");
            is_my_turn = True;

# Server side

def start_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM);
    server.bind(('0.0.0.0', port));
    server.listen(5);
    print(f"Servidor a ouvir na porta {port}.");
    while True:
        conn, addr = server.accept();
        print(f"Conexão aceita de {addr}");
        t = threading.Thread(target=handle_game, args=(conn, False), daemon=True);
        t.start();

# Client side

def start_client(peer_host, peer_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM);
    sock.connect((peer_host, peer_port));
    print(f"Conectado a {peer_host}:{peer_port}");
    handle_game(sock, True);

# Main

my_port = int(sys.argv[1]);
peer_host = sys.argv[2] if len(sys.argv) > 2 else None;
peer_port = int(sys.argv[3]) if len(sys.argv) > 3 else None;

# Começar o servidor
t = threading.Thread(target=start_server, args=(my_port,), daemon=True);
t.start();

# Conectar ao peer
if peer_host and peer_port:
    start_client(peer_host, peer_port);
else:
    print("A espera de conexões...");
    threading.Event().wait();
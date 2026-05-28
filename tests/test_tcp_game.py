import asyncio
import pytest

import network.tcp_game as tcp_game


@pytest.mark.asyncio
async def test_my_attack_sends_fire_and_receives_result(monkeypatch):
    ui = tcp_game.GameUI()
    # queue an attack coordinate
    ui.submit_attack("A1")

    sent = {}

    async def fake_send_msg(writer, msg):
        # record the message that would have been sent
        sent['last'] = msg

    async def fake_recv_msg(reader):
        # simulate the opponent returning a result for our fire
        return {"command": "result", "outcome": "hit", "ship": "Patrol Boat"}

    monkeypatch.setattr(tcp_game, 'send_msg', fake_send_msg)
    monkeypatch.setattr(tcp_game, 'recv_msg', fake_recv_msg)

    # Call _my_attack; writer/reader are unused because we patched send/recv
    winner = await tcp_game._my_attack(None, None, "game1", ui, "p1", "p2")

    assert sent['last']['command'] == 'fire'
    assert 'x' in sent['last'] and 'y' in sent['last']
    assert winner is None


@pytest.mark.asyncio
async def test_their_attack_handles_fire_and_gameover(monkeypatch):
    ui = tcp_game.GameUI()

    # Simulate receiving a fire command at (0,0)
    async def fake_recv_msg(reader):
        return {"command": "fire", "x": 0, "y": 0}

    recorded = {}

    async def fake_send_msg(writer, msg):
        recorded['msg'] = msg

    monkeypatch.setattr(tcp_game, 'recv_msg', fake_recv_msg)
    monkeypatch.setattr(tcp_game, 'send_msg', fake_send_msg)

    # Create a mock board that will report game_over when hit
    class MockBoard:
        def register_shot(self, x, y):
            return ("sunk", "Patrol Boat", True)

    board = MockBoard()

    winner = await tcp_game._their_attack(None, None, 'p1', 'p2', 'game1', board, ui)

    # Since board reported game_over, _their_attack should return opponent id (attacker)
    assert winner == 'p2'
    assert recorded['msg']['command'] == 'gameover'


@pytest.mark.asyncio
async def test_game_session_returns_winner_from_attacks(monkeypatch):
    # Simulate that the first side to act immediately returns a winner
    async def fake_my_attack(writer, reader, game_id, ui, player_id, opponent_id):
        return 'p1'

    monkeypatch.setattr(tcp_game, '_my_attack', fake_my_attack)

    # Use dummy reader/writer and a simple UI
    ui = tcp_game.GameUI()

    winner = await tcp_game.game_session(None, None, 'p1', 'p2', 'game1', True, None, ui)
    assert winner == 'p1'

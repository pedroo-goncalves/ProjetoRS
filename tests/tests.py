# Game logic tests - MADE WITH AI
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from game.ship_factory import create_ship
from game.board import Board
from game.types import Cell

def test_hit():
    ship = create_ship("Patrol Boat", (0, 0), horizontal=True)
    assert ship.register_hit((0, 0)) == True

def test_miss_returns_correct_tuple():
    b = Board(ships=[create_ship("Patrol Boat", (0, 0), horizontal=True)])
    assert b.register_shot(5, 5) == ("miss", None, False)

def test_sunk_returns_game_over_when_last_ship():
    b = Board(ships=[create_ship("Patrol Boat", (0, 0), horizontal=True)])
    b.register_shot(0, 0)
    result = b.register_shot(1, 0)
    assert result == ("sunk", "Patrol Boat", True)


# --- edge cases ---

def test_shooting_same_cell_twice_still_counts_as_hit():
    b = Board(ships=[create_ship("Patrol Boat", (0, 0), horizontal=True)])
    b.register_shot(0, 0)
    result = b.register_shot(0, 0)
    assert result == ("hit", "Patrol Boat", False)


def test_sunk_with_other_ships_alive_is_not_game_over():
    patrol = create_ship("Patrol Boat", (0, 0), horizontal=True)
    battleship = create_ship("Battleship", (5, 5), horizontal=True)
    b = Board(ships=[patrol, battleship])
    b.register_shot(0, 0)
    result = b.register_shot(1, 0)
    assert result == ("sunk", "Patrol Boat", False)


def test_ship_at_boundary_is_not_placed():
    # Patrol Boat at (9,0) horizontal occupies (9,0) and (10,0).
    # x=10 is one past the valid 0-9 range and must be rejected.
    b = Board(ships=[])
    boundary_ship = create_ship("Patrol Boat", (9, 0), horizontal=True)
    b.place_ship(boundary_ship)
    assert len(b.ships) == 0


def test_overlapping_ship_is_not_placed():
    b = Board(ships=[create_ship("Patrol Boat", (0, 0), horizontal=True)])
    overlapping = create_ship("Destroyer", (1, 0), horizontal=True)
    b.place_ship(overlapping)
    assert len(b.ships) == 1


def test_vertical_ship_occupies_cells_along_y_axis():
    ship = create_ship("Patrol Boat", (0, 0), horizontal=False)
    assert Cell(0, 0) in ship.position
    assert Cell(0, 1) in ship.position
    assert Cell(1, 0) not in ship.position


def test_invalid_ship_name_raises_key_error():
    with pytest.raises(KeyError):
        create_ship("Dreadnought", (0, 0), horizontal=True)


def test_empty_board_is_game_over():
    b = Board(ships=[])
    assert b.is_game_over() == True


def test_shooting_already_sunk_ship_returns_miss():
    # After sinking the only ship, shooting the same cell again should be a miss,
    # not a second ("sunk", ..., True) — the game is already over.
    b = Board(ships=[create_ship("Patrol Boat", (0, 0), horizontal=True)])
    b.register_shot(0, 0)
    b.register_shot(1, 0)  # ship is now sunk
    result = b.register_shot(0, 0)
    assert result == ("miss", None, False)
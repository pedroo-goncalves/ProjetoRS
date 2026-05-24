from .types import Cell
from .ship import Ship

# SOURCE: https://en.wikipedia.org/wiki/Battleship_(game)
FLEET = {
    "Carrier":      5,
    "Battleship":   4,
    "Destroyer":    3,
    "Submarine":    3,
    "Patrol Boat":  2
}

# Please don't use the constructor direclty
# I don't know how to make it private 
def create_ship(name:str, starting_pos:Cell, horizontal:bool) -> Ship:
    positions = calculate_positions(name, starting_pos, horizontal)

    return Ship(name, positions)


def calculate_positions(name:str, starting_pos:Cell, horizontal:bool) -> set[Cell]:
    size = FLEET[name]
    positions = {starting_pos}
    x,y = starting_pos

    if horizontal:
        for i in range(1, size):
            positions.add(Cell(x + i, y))
    else:
        for i in range(1, size):
            positions.add(Cell(x, y + i))

    return positions
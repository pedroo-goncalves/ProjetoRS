from .types import Cell
from .ship import Ship

from dataclasses import dataclass

GRID_SIZE = 10

@dataclass
class Board:
    ships: list[Ship]

    def place_ship(self, ship:Ship) -> bool:
        if self.validate_placement(ship):
            self.ships.append(ship)
            return True

        return False


    def register_shot(self, x:int,y:int) -> tuple[str, str | None, bool]:
        shot = Cell(x,y)

        for ship in self.ships:
            # Check if ship was already sunken or shot in that place
            was_sunk    = ship.is_sunk()
            already_hit = shot in ship.hits  # snapshot before register_hit mutates ship.hits

            if ship.register_hit(shot):
                if was_sunk or already_hit:
                    return ("miss", None, False)

                if ship.is_sunk():
                    return ("sunk", ship.name, self.is_game_over())

                return ("hit", ship.name, False)

        return ("miss", None, False)


    # HELPER FUNCTIONS
    def validate_placement(self, ship:Ship) -> bool:
        return self._is_in_bounds(ship) and not self._is_overlapping(ship)


    def _is_in_bounds(self, ship:Ship) -> bool:

        for position in ship.position:
            # Unpack cell tuple
            x,y = position

            if x < 0 or x >= GRID_SIZE:
                return False

            if y < 0 or y >= GRID_SIZE:
                return False

        return True


    def _is_overlapping(self, ship:Ship) -> bool:
        """Check if ship's position is already occupied by other ship"""

        # For each spot the ship wants to occupy
        for position in ship.position:
            # Verify if its already occupied
            for placed_ship in self.ships:
                if position in placed_ship.position:
                    return True

        return False


    def is_game_over(self) -> bool:
        gg = True

        for ship in self.ships:
            if ship.is_sunk() == False:
                gg = False
                break

        return gg

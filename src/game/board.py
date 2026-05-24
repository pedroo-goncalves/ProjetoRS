from types import Cell
from ship import Ship

from dataclasses import dataclass

GRID_SIZE = 10

@dataclass
class board:
    ships: list[Ship]

    def place_ship(self, ship:Ship) -> None:      
        if self.validate_placement():
            self.ships.append(ship)


    def register_shot(self, x:int,y:int) -> tuple[str, str | None, bool]:
        for ship in self.ships:

            if ship.register_hit(Cell(x,y)):
                return ("hit", ship.name, self.is_game_over())
            
        return ("miss", None, False)


    # HELPER FUNCTIONS
    def validate_placement(self, ship:Ship) -> bool:
        return self._is_in_bounds() and not self._is_overlapping()


    def _is_in_bounds(self, ship:Ship) -> bool:

        for position in ship.position:
            # Unwrap cell tuple
            x,y = position

            if x < 0 or x > GRID_SIZE:
                return False
        
            if y < 0 or y > GRID_SIZE:
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
                

            
        


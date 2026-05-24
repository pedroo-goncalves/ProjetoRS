from types import Cell
from dataclasses import dataclass, field

@dataclass
class Ship:
    name: str
    position: set[Cell]
    # Where a ship was hit
    hits: set[Cell] = field(default_factory=set)

    def register_hit(self, cell:Cell) -> bool:
        if cell in self.position:
            self.hits.add(cell)
            return True
        
        return False

    def is_sunk(self) -> bool:
        # If all cells of boat were hit
        if self.position == self.hits:
            return True
        
        return False
    
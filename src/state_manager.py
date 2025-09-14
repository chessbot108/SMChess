#!/usr/bin/env python3
from typing import List, Optional, Set
from dataclasses import dataclass


@dataclass(frozen=True)
class PuzzleState:
    """
    Represents a unique board state within the context of a puzzle.
    Frozen dataclass ensures immutability and hashability for deduplication.
    """
    puzzle_id: str
    moves_uci: tuple  # Sequence of UCI moves from puzzle start (tuple for hashability)
    message_id: Optional[int] = None  # Associated message ID if exists

    def __eq__(self, other) -> bool:
        if not isinstance(other, PuzzleState):
            return False
        return (self.puzzle_id == other.puzzle_id and
                self.moves_uci == other.moves_uci)

    def __hash__(self) -> int:
        return hash((self.puzzle_id, self.moves_uci))


@dataclass
class SearchCriteria:
    """Search criteria for finding puzzle states, all fields optional for flexible matching"""
    puzzle_id: Optional[str] = None
    moves_uci: Optional[tuple] = None
    move_count: Optional[int] = None
    move_count_min: Optional[int] = None
    move_count_max: Optional[int] = None
    message_id: Optional[int] = None


class StateManager:
    """
    Manages unique puzzle states with deduplication and flexible search.

    Key features:
    - Deduplicates identical states (same puzzle + fen)
    - Fluid copying of states to create new ones
    - Flexible search by various criteria
    """

    def __init__(self):
        self.states: Set[PuzzleState] = set()  # Automatic deduplication
        self.current_state: Optional[PuzzleState] = None

    def create_state(self, puzzle_id: str, moves_uci: List[str], message_id: Optional[int] = None) -> PuzzleState:
        """
        Create a new puzzle state. If identical state exists, returns existing one.
        """
        state = PuzzleState(
            puzzle_id=puzzle_id,
            moves_uci=tuple(moves_uci),
            message_id=message_id
        )

        # Add to set (no-op if already exists due to deduplication)
        self.states.add(state)
        return state

    def copy_state(self, source_state: PuzzleState,
                   new_puzzle_id: Optional[str] = None,
                   new_moves_uci: Optional[List[str]] = None,
                   new_message_id: Optional[int] = None) -> PuzzleState:
        """
        Fluid copying - create new state based on existing one with optional overrides.
        """
        return self.create_state(
            puzzle_id=new_puzzle_id or source_state.puzzle_id,
            moves_uci=new_moves_uci or list(source_state.moves_uci),
            message_id=new_message_id if new_message_id is not None else source_state.message_id
        )

    def set_current_state(self, state: PuzzleState) -> None:
        """Set the active puzzle state"""
        self.current_state = state

    def get_current_state(self) -> Optional[PuzzleState]:
        """Get the current active state"""
        return self.current_state

    def search_states(self, criteria: SearchCriteria) -> List[PuzzleState]:
        """
        Search for puzzle states matching the given criteria.
        All criteria fields are optional - only non-None fields are used for filtering.
        """
        results = []

        for state in self.states:
            # Check puzzle_id match
            if criteria.puzzle_id is not None and state.puzzle_id != criteria.puzzle_id:
                continue

            # Check moves_uci match
            if criteria.moves_uci is not None and state.moves_uci != criteria.moves_uci:
                continue

            # Check exact move_count match
            if criteria.move_count is not None and len(state.moves_uci) != criteria.move_count:
                continue

            # Check move_count range
            if criteria.move_count_min is not None and len(state.moves_uci) < criteria.move_count_min:
                continue
            if criteria.move_count_max is not None and len(state.moves_uci) > criteria.move_count_max:
                continue

            # Check message_id match
            if criteria.message_id is not None and state.message_id != criteria.message_id:
                continue

            results.append(state)

        return results

    def get_states_for_puzzle(self, puzzle_id: str) -> List[PuzzleState]:
        """Get all states for a specific puzzle"""
        return self.search_states(SearchCriteria(puzzle_id=puzzle_id))

    def state_exists(self, puzzle_id: str, moves_uci: List[str]) -> bool:
        """Check if a specific state already exists"""
        test_state = PuzzleState(puzzle_id=puzzle_id, moves_uci=tuple(moves_uci))
        return test_state in self.states

    def get_state_count(self) -> int:
        """Get total number of unique states"""
        return len(self.states)

    def clear_puzzle_states(self, puzzle_id: str) -> int:
        """Remove all states for a specific puzzle, return count removed"""
        initial_count = len(self.states)
        self.states = {state for state in self.states if state.puzzle_id != puzzle_id}

        # Clear current state if it belonged to this puzzle
        if self.current_state and self.current_state.puzzle_id == puzzle_id:
            self.current_state = None

        return initial_count - len(self.states)
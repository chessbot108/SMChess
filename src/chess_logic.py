#!/usr/bin/env python3
import chess
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class MoveResult(Enum):
    """Result of attempting a move"""
    SUCCESS = "success"
    INVALID_MOVE = "invalid_move"
    PUZZLE_SOLVED = "puzzle_solved"
    WRONG_MOVE = "wrong_move"


@dataclass
class GameState:
    """Current state of the chess game/puzzle"""
    board: chess.Board
    moves_uci: List[str]     # All moves in UCI notation
    current_move_index: int  # Index in the puzzle solution
    is_player_turn: bool
    puzzle_solved: bool = False


class ChessLogic:
    """Handles chess move execution and puzzle logic with state management"""

    def __init__(self, state_manager=None):
        self.game_state: Optional[GameState] = None
        self.solution_moves: List[str] = []
        self.state_manager = state_manager
        self.puzzle_id: Optional[str] = None

    def initialize_puzzle(self, puzzle_id: str, fen: str, solution_moves: List[str]) -> GameState:
        """Initialize a new puzzle"""
        board = chess.Board(fen)
        self.solution_moves = solution_moves
        self.puzzle_id = puzzle_id
        self.game_state = GameState(
            board=board,
            moves_uci=[],
            current_move_index=0,
            is_player_turn=True
        )
        return self.game_state

    def _replay_moves_on_board(self, initial_fen: str, moves_uci: List[str]) -> chess.Board:
        """Helper method to replay moves on a board"""
        board = chess.Board(initial_fen)
        for move_uci in moves_uci:
            board.push(chess.Move.from_uci(move_uci))
        return board

    def load_from_state(self, puzzle_state, initial_fen: str, solution_moves: List[str]) -> None:
        """Load game state from a puzzle state"""
        board = self._replay_moves_on_board(initial_fen, list(puzzle_state.moves_uci))

        self.puzzle_id = puzzle_state.puzzle_id
        self.solution_moves = solution_moves
        self.game_state = GameState(
            board=board,
            moves_uci=list(puzzle_state.moves_uci),
            current_move_index=len(puzzle_state.moves_uci),
            is_player_turn=len(puzzle_state.moves_uci) % 2 == 0
        )

    def play_move(self, move_str: str) -> Tuple[MoveResult, str]:
        """
        Play a single chess move in either SAN (e4, Nf3) or UCI (e2e4, g1f3) format.

        Args:
            move_str: The move to play in SAN or UCI notation

        Returns:
            Tuple of (MoveResult, message) where message describes what happened
        """
        if not self.game_state:
            return MoveResult.INVALID_MOVE, "No puzzle initialized"

        board = self.game_state.board
        move = None

        try:
            # Try SAN first
            move = board.parse_san(move_str)
        except (ValueError, chess.InvalidMoveError, chess.IllegalMoveError):
            try:
                # Try UCI
                move = chess.Move.from_uci(move_str)
                if move not in board.legal_moves:
                    move = None
            except (ValueError, chess.InvalidMoveError):
                pass

        if not move:
            return MoveResult.INVALID_MOVE, f"'{move_str}' is not a valid move"

        # Check against puzzle solution if we're tracking it
        if (self.game_state.is_player_turn and
            self.game_state.current_move_index < len(self.solution_moves)):
            expected_move = self.solution_moves[self.game_state.current_move_index]
            if move.uci() != expected_move:
                return MoveResult.WRONG_MOVE, "Puzzle expects a different move"

        # Execute the move
        san_move = board.san(move)
        board.push(move)

        self.game_state.moves_uci.append(move.uci())

        if self.game_state.is_player_turn:
            self.game_state.current_move_index += 1

        self.game_state.is_player_turn = not self.game_state.is_player_turn

        # Create new state after successful move
        if self.state_manager and self.puzzle_id:
            new_state = self.state_manager.create_state(
                puzzle_id=self.puzzle_id,
                moves_uci=self.game_state.moves_uci.copy()
            )
            self.state_manager.set_current_state(new_state)

        # Check if puzzle is solved
        if (self.game_state.current_move_index >= len(self.solution_moves) or
            board.is_checkmate() or board.is_stalemate()):
            self.game_state.puzzle_solved = True
            return MoveResult.PUZZLE_SOLVED, f"Puzzle solved with {san_move}!"

        return MoveResult.SUCCESS, san_move

    def play_move_tree(self, moves: List[str]) -> Tuple[MoveResult, List[str]]:
        """
        Play a sequence of moves, automatically handling opponent responses.

        For puzzles, after each player move, the opponent's response from the
        solution is played automatically.

        Args:
            moves: List of moves in SAN or UCI format to play in sequence

        Returns:
            Tuple of (final_result, list_of_messages) describing each move played
        """
        results = []

        for move_str in moves:
            result, message = self.play_move(move_str)
            results.append(message)

            if result in [MoveResult.INVALID_MOVE, MoveResult.PUZZLE_SOLVED]:
                break

            # Auto-play opponent response for puzzles
            if (result == MoveResult.SUCCESS and
                not self.game_state.is_player_turn and
                self.game_state.current_move_index < len(self.solution_moves)):
                opponent_move = self.solution_moves[self.game_state.current_move_index]
                opp_result, opp_message = self.play_move(opponent_move)
                results.append(f"Opponent: {opp_message}")

                if opp_result == MoveResult.PUZZLE_SOLVED:
                    result = opp_result
                    break

        return result, results

    def rollback_moves(self, count: int = 1) -> bool:
        """Rollback the last N moves"""
        if not self.game_state or count <= 0:
            return False

        moves_popped = 0
        while moves_popped < count and self.game_state.board.move_stack:
            self.game_state.board.pop()
            self.game_state.moves_uci.pop()
            moves_popped += 1

        # Recalculate game state
        self.game_state.current_move_index = max(0,
            self.game_state.current_move_index - moves_popped)
        self.game_state.is_player_turn = len(self.game_state.moves_uci) % 2 == 0
        self.game_state.puzzle_solved = False

        return moves_popped > 0

    def get_current_position(self) -> Dict:
        """Get current position info"""
        if not self.game_state:
            return {}

        return {
            "fen": self.game_state.board.fen(),
            "moves_uci": self.game_state.moves_uci.copy(),
            "is_player_turn": self.game_state.is_player_turn,
            "puzzle_solved": self.game_state.puzzle_solved,
            "legal_moves": [self.game_state.board.san(move)
                          for move in self.game_state.board.legal_moves]
        }

    def get_hint(self) -> str:
        """Get hint for next move"""
        if (not self.game_state or
            self.game_state.puzzle_solved or
            self.game_state.current_move_index >= len(self.solution_moves)):
            return "No hint available"

        next_move_uci = self.solution_moves[self.game_state.current_move_index]
        try:
            move = chess.Move.from_uci(next_move_uci)
            san_move = self.game_state.board.san(move)
            return f"Try: {san_move}"
        except (ValueError, chess.InvalidMoveError):
            return f"Try: {next_move_uci}"
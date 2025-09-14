#!/usr/bin/env python3
import chess
from stockfish import Stockfish

try:
    from .state_manager import PuzzleState
    from .puzzle_manager import PuzzleManager
except ImportError:
    from state_manager import PuzzleState
    from puzzle_manager import PuzzleManager


def evaluate_position(puzzle_state: PuzzleState, puzzle_manager: PuzzleManager) -> float:
    """
    Evaluate a puzzle state and return position evaluation.

    Args:
        puzzle_state: The puzzle state to evaluate
        puzzle_manager: Manager to get puzzle data

    Returns:
        Position evaluation in centipawns (positive = white advantage)
        Returns 0.0 if evaluation fails
    """
    try:
        # Get puzzle and build board
        current_puzzle = puzzle_manager.get_current_puzzle()
        if not current_puzzle or current_puzzle.puzzle_id != puzzle_state.puzzle_id:
            return 0.0

        board = chess.Board(current_puzzle.fen)
        for move_uci in puzzle_state.moves_uci:
            board.push(chess.Move.from_uci(move_uci))

        # Evaluate with Stockfish
        stockfish = Stockfish(depth=12)
        stockfish.set_fen_position(board.fen())

        evaluation = stockfish.get_evaluation()
        if evaluation["type"] == "mate":
            return 29900.0 if evaluation["value"] > 0 else -29900.0
        else:
            return float(evaluation["value"])

    except Exception:
        return 0.0
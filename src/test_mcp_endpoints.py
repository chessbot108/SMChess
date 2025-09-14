#!/usr/bin/env python3
"""Pytest test suite for all MCP endpoints"""
import pytest
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Import the underlying components directly
from src.puzzle_manager import PuzzleManager
from src.chess_logic import ChessLogic
from src.state_manager import StateManager


@pytest.fixture
async def setup_test_environment():
    """Set up test environment with puzzle manager and chess logic"""
    puzzle_manager = PuzzleManager()
    chess_logic = ChessLogic(state_manager=puzzle_manager.state_manager)

    # Fetch a puzzle for testing
    puzzle = await puzzle_manager.fetch_puzzle()
    chess_logic.initialize_puzzle(
        puzzle_id=puzzle.puzzle_id,
        fen=puzzle.fen,
        solution_moves=puzzle.moves
    )

    yield puzzle_manager, chess_logic, puzzle

    # Cleanup
    await puzzle_manager.close()


class TestMCPEndpoints:
    """Test suite for MCP endpoints functionality"""

    @pytest.mark.asyncio
    async def test_puzzle_manager_fetch_puzzle(self):
        """Test successful puzzle fetching via PuzzleManager"""
        puzzle_manager = PuzzleManager()
        try:
            puzzle = await puzzle_manager.fetch_puzzle()

            assert puzzle.puzzle_id is not None
            assert puzzle.rating > 0
            assert isinstance(puzzle.themes, list)
            assert len(puzzle.themes) > 0
            assert puzzle.fen is not None
            assert len(puzzle.moves) > 0
        finally:
            await puzzle_manager.close()

    @pytest.mark.asyncio
    async def test_puzzle_manager_with_params(self):
        """Test puzzle fetching with angle and difficulty parameters"""
        puzzle_manager = PuzzleManager()
        try:
            puzzle = await puzzle_manager.fetch_puzzle(angle="endgame", difficulty="easier")
            assert puzzle.puzzle_id is not None
            assert puzzle.rating > 0
        finally:
            await puzzle_manager.close()

    @pytest.mark.asyncio
    async def test_state_manager_operations(self):
        """Test state manager functionality"""
        puzzle_manager = PuzzleManager()
        try:
            puzzle = await puzzle_manager.fetch_puzzle()

            # Test initial state creation
            initial_state = puzzle_manager.get_current_state()
            assert initial_state is not None
            assert initial_state.puzzle_id == puzzle.puzzle_id
            assert len(initial_state.moves_uci) == 0

            # Test state creation with moves
            test_moves = ["e2e4", "e7e5"]
            new_state = puzzle_manager.create_state_after_moves(test_moves)
            assert new_state is not None
            assert list(new_state.moves_uci) == test_moves
        finally:
            await puzzle_manager.close()

    @pytest.mark.asyncio
    async def test_state_search_functionality(self):
        """Test state search with criteria"""
        puzzle_manager = PuzzleManager()
        try:
            puzzle = await puzzle_manager.fetch_puzzle()
            from src.state_manager import SearchCriteria

            # Search for initial state
            criteria = SearchCriteria(puzzle_id=puzzle.puzzle_id, move_count=0)
            results = puzzle_manager.search_states_by_criteria(criteria)
            assert len(results) >= 1  # Should find at least the initial state
        finally:
            await puzzle_manager.close()

    @pytest.mark.asyncio
    async def test_chess_logic_no_puzzle(self):
        """Test chess logic when no puzzle is loaded"""
        state_manager = StateManager()
        chess_logic = ChessLogic(state_manager=state_manager)

        # Should fail when no puzzle is initialized
        from src.chess_logic import MoveResult
        result, message = chess_logic.play_move("e2e4")
        assert result == MoveResult.INVALID_MOVE

    @pytest.mark.asyncio
    async def test_chess_logic_with_puzzle(self):
        """Test chess logic after loading a puzzle"""
        puzzle_manager = PuzzleManager()
        try:
            puzzle = await puzzle_manager.fetch_puzzle()
            chess_logic = ChessLogic(state_manager=puzzle_manager.state_manager)
            chess_logic.initialize_puzzle(
                puzzle_id=puzzle.puzzle_id,
                fen=puzzle.fen,
                solution_moves=puzzle.moves
            )

            # Try a common chess move that should be legal (even if wrong for puzzle)
            result, message = chess_logic.play_move("e2e4")
            from src.chess_logic import MoveResult
            # Should be either valid move or invalid move, not a system error
            assert result in [MoveResult.SUCCESS, MoveResult.WRONG_MOVE, MoveResult.INVALID_MOVE]
            assert message is not None
        finally:
            await puzzle_manager.close()

    @pytest.mark.asyncio
    async def test_chess_logic_invalid_move(self):
        """Test chess logic with invalid move format"""
        puzzle_manager = PuzzleManager()
        try:
            puzzle = await puzzle_manager.fetch_puzzle()
            chess_logic = ChessLogic(state_manager=puzzle_manager.state_manager)
            chess_logic.initialize_puzzle(
                puzzle_id=puzzle.puzzle_id,
                fen=puzzle.fen,
                solution_moves=puzzle.moves
            )

            # Try invalid move
            from src.chess_logic import MoveResult
            result, message = chess_logic.play_move("invalid_move")
            assert result == MoveResult.INVALID_MOVE
        finally:
            await puzzle_manager.close()

    @pytest.mark.asyncio
    async def test_board_generation(self):
        """Test board generation from puzzle state"""
        puzzle_manager = PuzzleManager()
        try:
            puzzle = await puzzle_manager.fetch_puzzle()
            current_state = puzzle_manager.get_current_state()

            # Test board reconstruction
            board = puzzle_manager.get_board_from_state(current_state)
            assert board is not None
            assert board.fen() is not None

            # Test with moves - use the first solution move which should be legal
            if puzzle.moves:
                test_moves = [puzzle.moves[0]]  # First solution move is guaranteed legal
                new_state = puzzle_manager.create_state_after_moves(test_moves)
                if new_state:
                    board_with_moves = puzzle_manager.get_board_from_state(new_state)
                    assert board_with_moves is not None
                    assert board_with_moves.fen() != board.fen()  # Should be different
        finally:
            await puzzle_manager.close()

    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """Test a complete workflow: get puzzle, make moves, check states"""
        puzzle_manager = PuzzleManager()
        try:
            # 1. Get a new puzzle
            puzzle = await puzzle_manager.fetch_puzzle()
            assert puzzle.puzzle_id is not None

            # 2. Initialize chess logic
            chess_logic = ChessLogic(state_manager=puzzle_manager.state_manager)
            chess_logic.initialize_puzzle(
                puzzle_id=puzzle.puzzle_id,
                fen=puzzle.fen,
                solution_moves=puzzle.moves
            )

            # 3. Check initial state
            initial_state = puzzle_manager.get_current_state()
            assert initial_state is not None
            assert initial_state.puzzle_id == puzzle.puzzle_id
            assert len(initial_state.moves_uci) == 0

            # 4. Try to make a legal chess move (may or may not be correct for puzzle)
            result, message = chess_logic.play_move("e2e4")
            from src.chess_logic import MoveResult
            # Should process the move (valid, wrong, or invalid)
            assert result in [MoveResult.SUCCESS, MoveResult.WRONG_MOVE, MoveResult.INVALID_MOVE]

            # 5. If move was valid, check the new state
            if result in [MoveResult.SUCCESS, MoveResult.WRONG_MOVE]:
                new_state = puzzle_manager.get_current_state()
                assert new_state is not None
                assert len(new_state.moves_uci) >= 1

                # 6. Test board reconstruction
                board = puzzle_manager.get_board_from_state(new_state)
                assert board is not None
                assert board.fen() is not None
        finally:
            await puzzle_manager.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
#!/usr/bin/env python3
import json
import httpx
import chess
import logging
from typing import List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path
try:
    from .state_manager import StateManager, PuzzleState, SearchCriteria
except ImportError:
    from state_manager import StateManager, PuzzleState, SearchCriteria


@dataclass
class PuzzleData:
    """Represents a chess puzzle from Lichess"""
    puzzle_id: str
    fen: str
    moves: List[str]  # Solution moves in UCI format
    rating: int
    rating_deviation: int
    popularity: int
    nb_plays: int
    themes: List[str]
    game_url: str
    opening_tags: List[str]


class PuzzleManager:
    """Manages chess puzzles from Lichess API with state tracking"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "SMChess-MCP-Server/1.0"}
        )
        self.current_puzzle: Optional[PuzzleData] = None
        self.user_difficulty = 1200  # Starting difficulty rating
        self.state_manager = StateManager()
        self.logger = logging.getLogger(__name__)

    async def fetch_puzzle(self, angle: Optional[str] = None, difficulty: Optional[str] = None) -> PuzzleData:
        """
        Fetch a puzzle from Lichess API using their filter parameters.

        Args:
            angle: The theme or opening to filter puzzles with (e.g. "middlegame", "endgame", "mate")
            difficulty: Desired difficulty relative to user rating ("easiest", "easier", "normal", "harder", "hardest")
        """
        # Build the URL with query parameters
        url = "https://lichess.org/api/puzzle/next"
        params = {}

        if angle:
            params['angle'] = angle
        if difficulty:
            params['difficulty'] = difficulty

        try:
            self.logger.info(f"Fetching puzzle from Lichess API: {url} with params: {params}")
            response = await self.client.get(url, params=params)
            self.logger.info(f"Response status: {response.status_code}")

            response.raise_for_status()

            data = response.json()
            self.logger.info(f"Response data keys: {list(data.keys())}")

            puzzle = data["puzzle"]
            game = data.get("game", {})

            # Extract the FEN from the game PGN at the initialPly
            fen = self._extract_fen_from_pgn(game.get("pgn", ""), puzzle.get("initialPly", 0))

            puzzle_data = PuzzleData(
                puzzle_id=puzzle["id"],
                fen=fen,
                moves=puzzle["solution"],
                rating=puzzle["rating"],
                rating_deviation=puzzle.get("ratingDeviation", 0),
                popularity=puzzle.get("popularity", 0),
                nb_plays=puzzle.get("plays", 0),
                themes=puzzle.get("themes", []),
                game_url=game.get("pgn", ""),
                opening_tags=game.get("opening", {}).get("name", "").split() if game.get("opening") else []
            )

            self.current_puzzle = puzzle_data
            self._save_puzzle(puzzle_data)

            # Create initial puzzle state
            initial_state = self.state_manager.create_state(
                puzzle_id=puzzle_data.puzzle_id,
                moves_uci=[],
                message_id=None
            )
            self.state_manager.set_current_state(initial_state)

            self.logger.info(f"Successfully fetched puzzle {puzzle_data.puzzle_id}")
            return puzzle_data

        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP error fetching puzzle: {e.response.status_code} - {e.response.text}")
            return self._get_fallback_puzzle()
        except httpx.RequestError as e:
            self.logger.error(f"Request error fetching puzzle: {str(e)}")
            return self._get_fallback_puzzle()
        except KeyError as e:
            self.logger.error(f"Missing expected key in API response: {e}")
            return self._get_fallback_puzzle()
        except Exception as e:
            self.logger.error(f"Unexpected error fetching puzzle: {str(e)}")
            return self._get_fallback_puzzle()

    def _get_fallback_puzzle(self) -> PuzzleData:
        """Provide a fallback puzzle when API fails"""
        puzzle_data = PuzzleData(
            puzzle_id="fallback_001",
            fen="r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
            moves=["d1h5", "g6h5"],  # Simple back rank mate pattern
            rating=1200,
            rating_deviation=50,
            popularity=90,
            nb_plays=1000,
            themes=["backRankMate", "mate", "mateIn2"],
            game_url="",
            opening_tags=["Italian", "Game"]
        )

        self.current_puzzle = puzzle_data

        # Create initial puzzle state
        initial_state = self.state_manager.create_state(
            puzzle_id=puzzle_data.puzzle_id,
            moves_uci=[],
            message_id=None
        )
        self.state_manager.set_current_state(initial_state)

        return puzzle_data

    def _save_puzzle(self, puzzle: PuzzleData) -> None:
        """Save puzzle to local storage"""
        puzzle_file = self.data_dir / f"puzzle_{puzzle.puzzle_id}.json"
        with open(puzzle_file, 'w') as f:
            json.dump(asdict(puzzle), f, indent=2)

    def _load_puzzle(self, puzzle_id: str) -> Optional[PuzzleData]:
        """Load puzzle from local storage"""
        puzzle_file = self.data_dir / f"puzzle_{puzzle_id}.json"
        if puzzle_file.exists():
            with open(puzzle_file, 'r') as f:
                data = json.load(f)
                return PuzzleData(**data)
        return None

    def get_current_puzzle(self) -> Optional[PuzzleData]:
        """Get the currently active puzzle"""
        return self.current_puzzle

    def adjust_difficulty(self, success: bool) -> None:
        """Adjust user difficulty based on puzzle completion"""
        if success:
            self.user_difficulty = min(self.user_difficulty + 50, 2500)
        else:
            self.user_difficulty = max(self.user_difficulty - 25, 800)

    def get_puzzle_hint(self, current_state: Optional[PuzzleState] = None) -> str:
        """Get a hint for the current puzzle based on current state"""
        if not self.current_puzzle or not self.current_puzzle.moves:
            return "No puzzle active or no solution available"

        if current_state is None:
            current_state = self.state_manager.get_current_state()

        if not current_state:
            return "No current state available"

        move_number = len(current_state.moves_uci)
        if move_number >= len(self.current_puzzle.moves):
            return "No more moves in the solution"

        next_move = self.current_puzzle.moves[move_number]

        # Convert UCI to more readable format
        try:
            # Recreate board from moves
            board = chess.Board(self.current_puzzle.fen)
            for move_uci in current_state.moves_uci:
                board.push(chess.Move.from_uci(move_uci))

            move = chess.Move.from_uci(next_move)
            san_move = board.san(move)
            return f"Try: {san_move}"
        except (ValueError, chess.InvalidMoveError):
            return f"Try: {next_move}"

    def create_state_after_moves(self, moves_uci: List[str], message_id: Optional[int] = None) -> Optional[PuzzleState]:
        """Create a new puzzle state after playing a sequence of moves"""
        if not self.current_puzzle:
            return None

        return self.state_manager.create_state(
            puzzle_id=self.current_puzzle.puzzle_id,
            moves_uci=moves_uci,
            message_id=message_id
        )

    def get_board_from_state(self, state: PuzzleState) -> chess.Board:
        """Reconstruct board position from a puzzle state"""
        if not self.current_puzzle:
            raise ValueError("No current puzzle available")

        board = chess.Board(self.current_puzzle.fen)
        for move_uci in state.moves_uci:
            board.push(chess.Move.from_uci(move_uci))

        return board

    def search_states_by_criteria(self, criteria: SearchCriteria) -> List[PuzzleState]:
        """Search puzzle states using the provided criteria"""
        return self.state_manager.search_states(criteria)

    def get_current_state(self) -> Optional[PuzzleState]:
        """Get the current puzzle state"""
        return self.state_manager.get_current_state()

    def set_current_state(self, state: PuzzleState) -> None:
        """Set the current puzzle state"""
        self.state_manager.set_current_state(state)

    def _extract_fen_from_pgn(self, pgn: str, initial_ply: int) -> str:
        """Extract FEN position from PGN at the given ply number"""
        try:
            # Create a board and play moves up to initial_ply
            board = chess.Board()

            if not pgn:
                return board.fen()

            # Split PGN into moves
            moves = pgn.split()

            # Play moves up to the initial ply (half-moves)
            for i, move_san in enumerate(moves):
                if i >= initial_ply:
                    break

                try:
                    move = board.parse_san(move_san)
                    board.push(move)
                except (ValueError, chess.InvalidMoveError) as e:
                    self.logger.warning(f"Invalid move in PGN: {move_san} - {e}")
                    break

            return board.fen()

        except Exception as e:
            self.logger.error(f"Error extracting FEN from PGN: {e}")
            # Return starting position as fallback
            return chess.Board().fen()

    async def close(self) -> None:
        """Clean up resources"""
        await self.client.aclose()
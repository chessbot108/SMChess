#!/usr/bin/env python3
import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

import chess.svg
import cairosvg
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
try:
    # Try relative imports first (when run as module)
    from .puzzle_manager import PuzzleManager
    from .chess_logic import ChessLogic, MoveResult
    from .state_manager import SearchCriteria, PuzzleState
    from .position_evaluator import evaluate_position
except ImportError:
    # Fall back to absolute imports (when run directly)
    from puzzle_manager import PuzzleManager
    from chess_logic import ChessLogic, MoveResult
    from state_manager import SearchCriteria, PuzzleState
    from position_evaluator import evaluate_position

# Create FastAPI app
app = FastAPI(title="SMChess Puzzle Server")

# Create FastMCP instance
mcp = FastMCP("SMChess Puzzle Server")

# Global instances
puzzle_manager = PuzzleManager()
chess_logic = ChessLogic(state_manager=puzzle_manager.state_manager)
message_counter = 0

# Setup directories
IMAGES_DIR = Path("static/images")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_DIR = Path("data/memory")
MEMORY_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_FILE = MEMORY_DIR / "user_preferences.json"

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@mcp.tool(description="Fetch a new chess puzzle from Lichess with optional theme and difficulty filters")
async def get_new_puzzle(angle: Optional[str] = None, difficulty: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch a new puzzle from Lichess API.

    Args:
        angle: The theme or opening to filter puzzles with (e.g. "middlegame", "endgame", "mate")
        difficulty: Desired difficulty relative to user rating ("easiest", "easier", "normal", "harder", "hardest")
    """
    try:
        # Fetch the puzzle with Lichess API parameters
        puzzle = await puzzle_manager.fetch_puzzle(angle=angle, difficulty=difficulty)

        # Initialize chess logic with the new puzzle
        chess_logic.initialize_puzzle(
            puzzle_id=puzzle.puzzle_id,
            fen=puzzle.fen,
            solution_moves=puzzle.moves
        )

        return {
            "success": True,
            "puzzle_id": puzzle.puzzle_id,
            "rating": puzzle.rating,
            "themes": puzzle.themes,
            "fen": puzzle.fen,
            "solution_length": len(puzzle.moves),
            "user_difficulty": puzzle_manager.user_difficulty,
            "message": f"New puzzle loaded! Rating: {puzzle.rating}, Themes: {', '.join(puzzle.themes[:3])}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to fetch new puzzle"
        }

@mcp.tool(description="Attempt to play a single chess move in UCI notation")
def attempt_move(
    move_uci: str,
    from_state_moves_uci: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Attempt to play one chess move in UCI notation and get detailed feedback.

    Args:
        move_uci: Single move in UCI format (e.g. "e2e4", "g1f3")
        from_state_moves_uci: Optional starting position as UCI moves

    Returns:
        {
            "success": bool,
            "move_valid": bool,
            "result": "success" | "invalid_move" | "wrong_move" | "puzzle_solved",
            "message": str,
            "puzzle_state": {
                "puzzle_id": str,
                "current_fen": str,
                "moves_uci": List[str],
                "puzzle_solved": bool
            } (only if move_valid=true),
            "error": str (if success=false)
        }
    """
    try:
        current_puzzle = puzzle_manager.get_current_puzzle()
        if not current_puzzle:
            return {
                "success": False,
                "error": "No active puzzle. Use get_new_puzzle to start."
            }

        # Handle starting from specific state
        if from_state_moves_uci is not None:
            search_results = puzzle_manager.search_states_by_criteria(
                SearchCriteria(
                    puzzle_id=current_puzzle.puzzle_id,
                    moves_uci=tuple(from_state_moves_uci)
                )
            )

            if search_results:
                target_state = search_results[0]
            else:
                target_state = puzzle_manager.create_state_after_moves(from_state_moves_uci)

            if target_state:
                chess_logic.load_from_state(target_state, current_puzzle.fen, current_puzzle.moves)
                puzzle_manager.set_current_state(target_state)

        # Attempt the single move
        result, message = chess_logic.play_move(move_uci)

        # Handle difficulty adjustment
        if result == MoveResult.PUZZLE_SOLVED:
            puzzle_manager.adjust_difficulty(True)
        elif result == MoveResult.WRONG_MOVE:
            puzzle_manager.adjust_difficulty(False)

        # Build response based on result
        response = {
            "success": True,
            "move_valid": result != MoveResult.INVALID_MOVE,
            "result": result.value,
            "message": message
        }

        # Add puzzle state if move was valid
        if result != MoveResult.INVALID_MOVE:
            current_state = puzzle_manager.get_current_state()
            position_info = chess_logic.get_current_position()

            response["puzzle_state"] = {
                "puzzle_id": current_puzzle.puzzle_id,
                "current_fen": position_info.get("fen", ""),
                "moves_uci": current_state.moves_uci if current_state else [],
                "puzzle_solved": position_info.get("puzzle_solved", False)
            }

        return response

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def generate_board_image(board, message_id: int) -> str:
    """Generate PNG image of chess board and return local URL"""
    # Generate SVG
    svg_data = chess.svg.board(board=board, size=400)

    # Convert to PNG
    png_filename = f"board_{message_id}.png"
    png_path = IMAGES_DIR / png_filename

    cairosvg.svg2png(bytestring=svg_data.encode('utf-8'), write_to=str(png_path))

    # Return local URL
    port = int(os.environ.get("PORT", 8000))
    return f"http://localhost:{port}/static/images/{png_filename}"

@mcp.tool(description="Send a message with optional puzzle state image")
def send_message(
    message: str,
    puzzle_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Send a message and optionally attach a puzzle state as an image.

    Args:
        message: The text message to send
        puzzle_state: Optional puzzle state dict with keys:
                     - puzzle_id: str
                     - moves_uci: List[str]
                     - message_id: int (optional)

    Returns:
        {
            "success": bool,
            "message_id": int,
            "message": str,
            "image_url": str (optional),
            "error": str (if success=false)
        }
    """
    global message_counter

    try:
        # Increment message counter
        message_counter += 1
        current_message_id = message_counter

        response = {
            "success": True,
            "message_id": current_message_id,
            "message": message
        }

        # Handle puzzle state if provided
        if puzzle_state is not None:
            puzzle_id = puzzle_state.get("puzzle_id")
            moves_uci = puzzle_state.get("moves_uci", [])

            if not puzzle_id:
                return {
                    "success": False,
                    "error": "puzzle_id is required in puzzle_state"
                }

            # Validate puzzle_id matches current puzzle
            current_puzzle = puzzle_manager.get_current_puzzle()
            if not current_puzzle:
                return {
                    "success": False,
                    "error": "No active puzzle."
                }

            if puzzle_id != current_puzzle.puzzle_id:
                return {
                    "success": False,
                    "error": f"Puzzle ID mismatch. Current: {current_puzzle.puzzle_id}, provided: {puzzle_id}"
                }

            # Create PuzzleState object
            state: PuzzleState = puzzle_manager.state_manager.create_state(
                puzzle_id=puzzle_id,
                moves_uci=moves_uci,
                message_id=current_message_id
            )

            # Generate board image
            board = puzzle_manager.get_board_from_state(state)
            image_url = generate_board_image(board, current_message_id)

            response["image_url"] = image_url

        return response

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@mcp.tool(description="Search for a specific puzzle state or create a new one if it doesn't exist")
def get_state(
    puzzle_id: Optional[str] = None,
    moves_uci: Optional[List[str]] = None,
    move_count: Optional[int] = None,
    move_count_min: Optional[int] = None,
    move_count_max: Optional[int] = None,
    message_id: Optional[int] = None,
    create_if_missing: bool = False
) -> Dict[str, Any]:
    """
    Search for puzzle states matching given criteria, or create a new state.

    If no search criteria are provided, returns the current active puzzle state.

    Args:
        puzzle_id: Exact puzzle ID to match (defaults to current puzzle if not specified)
        moves_uci: Exact sequence of UCI moves to match (e.g. ["e2e4", "e7e5"])
        move_count: Exact number of moves played
        move_count_min: Minimum number of moves played
        move_count_max: Maximum number of moves played
        message_id: Exact message ID to match
        create_if_missing: If True and no states match, create a new state using current puzzle

    Returns:
        {
            "success": bool,
            "state_found": bool,
            "puzzle_id": str,
            "moves_uci": List[str],
            "move_count": int,
            "message_id": int,
            "fen": str,
            "is_current_state": bool,
            "total_matching_states": int,
            "state_created": bool (optional),
            "error": str (if success=false)
        }
    """
    try:
        # If no search criteria provided, return current state
        if all(param is None for param in [puzzle_id, moves_uci, move_count, move_count_min, move_count_max, message_id]):
            current_state = puzzle_manager.get_current_state()
            current_puzzle = puzzle_manager.get_current_puzzle()

            if not current_state or not current_puzzle:
                return {
                    "success": False,
                    "error": "No active puzzle or state. Use get_new_puzzle first."
                }

            # Get board position for this state
            board = puzzle_manager.get_board_from_state(current_state)

            return {
                "success": True,
                "state_found": True,
                "puzzle_id": current_state.puzzle_id,
                "moves_uci": list(current_state.moves_uci),
                "move_count": len(current_state.moves_uci),
                "message_id": current_state.message_id,
                "fen": board.fen(),
                "is_current_state": True,
                "total_matching_states": 1
            }

        # Build search criteria
        criteria = SearchCriteria(
            puzzle_id=puzzle_id,
            moves_uci=tuple(moves_uci) if moves_uci else None,
            move_count=move_count,
            move_count_min=move_count_min,
            move_count_max=move_count_max,
            message_id=message_id
        )

        # Search for matching states
        matching_states = puzzle_manager.search_states_by_criteria(criteria)

        if matching_states:
            # Return the first matching state
            state = matching_states[0]
            current_puzzle = puzzle_manager.get_current_puzzle()

            if not current_puzzle or current_puzzle.puzzle_id != state.puzzle_id:
                return {
                    "success": False,
                    "error": f"Found state for puzzle {state.puzzle_id}, but it's not the current puzzle"
                }

            # Get board position for this state
            board = puzzle_manager.get_board_from_state(state)
            current_state = puzzle_manager.get_current_state()

            return {
                "success": True,
                "state_found": True,
                "puzzle_id": state.puzzle_id,
                "moves_uci": list(state.moves_uci),
                "move_count": len(state.moves_uci),
                "message_id": state.message_id,
                "fen": board.fen(),
                "is_current_state": current_state == state,
                "total_matching_states": len(matching_states)
            }

        # No matching states found
        if not create_if_missing:
            return {
                "success": True,
                "state_found": False,
                "total_matching_states": 0,
                "search_criteria": {
                    "puzzle_id": puzzle_id,
                    "moves_uci": moves_uci,
                    "move_count": move_count,
                    "move_count_min": move_count_min,
                    "move_count_max": move_count_max,
                    "message_id": message_id
                }
            }

        # Create new state if requested
        current_puzzle = puzzle_manager.get_current_puzzle()
        if not current_puzzle:
            return {
                "success": False,
                "error": "Cannot create state: no active puzzle. Use get_new_puzzle first."
            }

        # Use current puzzle if not specified
        target_moves = moves_uci or []

        # Create the new state
        new_state = puzzle_manager.create_state_after_moves(
            moves_uci=target_moves,
            message_id=message_id
        )

        if not new_state:
            return {
                "success": False,
                "error": "Failed to create new state"
            }

        # Get board position
        board = puzzle_manager.get_board_from_state(new_state)

        return {
            "success": True,
            "state_found": False,
            "state_created": True,
            "puzzle_id": new_state.puzzle_id,
            "moves_uci": list(new_state.moves_uci),
            "move_count": len(new_state.moves_uci),
            "message_id": new_state.message_id,
            "fen": board.fen(),
            "is_current_state": False,
            "total_matching_states": 0
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@mcp.tool(description="Evaluate a chess position using Stockfish engine")
def evaluate_chess_position(
    puzzle_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Evaluate a chess position using Stockfish engine.

    Args:
        puzzle_state: Optional puzzle state dict with keys:
                     - puzzle_id: str
                     - moves_uci: List[str]
                     If not provided, evaluates current puzzle state

    Returns:
        {
            "success": bool,
            "evaluation": float (centipawns, positive = white advantage),
            "puzzle_id": str,
            "moves_uci": List[str],
            "fen": str,
            "error": str (if success=false)
        }
    """
    try:
        # Use current state if no puzzle_state provided
        if puzzle_state is None:
            current_state = puzzle_manager.get_current_state()
            current_puzzle = puzzle_manager.get_current_puzzle()

            if not current_state or not current_puzzle:
                return {
                    "success": False,
                    "error": "No active puzzle state. Use get_new_puzzle first."
                }

            # Evaluate current position
            evaluation = evaluate_position(current_state, puzzle_manager)
            board = puzzle_manager.get_board_from_state(current_state)

            return {
                "success": True,
                "evaluation": evaluation,
                "puzzle_id": current_state.puzzle_id,
                "moves_uci": list(current_state.moves_uci),
                "fen": board.fen()
            }

        # Handle specific puzzle state
        puzzle_id = puzzle_state.get("puzzle_id")
        moves_uci = puzzle_state.get("moves_uci", [])

        if not puzzle_id:
            return {
                "success": False,
                "error": "puzzle_id is required in puzzle_state"
            }

        # Validate puzzle_id matches current puzzle
        current_puzzle = puzzle_manager.get_current_puzzle()
        if not current_puzzle:
            return {
                "success": False,
                "error": "No active puzzle. Use get_new_puzzle first."
            }

        if puzzle_id != current_puzzle.puzzle_id:
            return {
                "success": False,
                "error": f"Puzzle ID mismatch. Current: {current_puzzle.puzzle_id}, provided: {puzzle_id}"
            }

        # Create PuzzleState object for evaluation
        state: PuzzleState = PuzzleState(
            puzzle_id=puzzle_id,
            moves_uci=tuple(moves_uci)
        )

        # Evaluate position
        evaluation = evaluate_position(state, puzzle_manager)
        board = puzzle_manager.get_board_from_state(state)

        return {
            "success": True,
            "evaluation": evaluation,
            "puzzle_id": puzzle_id,
            "moves_uci": moves_uci,
            "fen": board.fen()
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@mcp.tool(description="Read user preferences and interaction history from persistent memory")
def read_memory() -> Dict[str, Any]:
    """
    Read user preferences and interaction patterns from persistent memory.

    This should contain estimates on user preferences including:
    - Talk style preferences (formal, casual, technical, etc.)
    - Difficulty preferences (beginner, intermediate, advanced)
    - Preferred puzzle themes/tags (tactics, endgame, openings, etc.)
    - Learning patterns and progress tracking
    - Communication preferences and feedback patterns

    Returns:
        {
            "success": bool,
            "memory": Dict containing user preferences and patterns,
            "error": str (if success=false)
        }
    """
    try:
        if MEMORY_FILE.exists():
            with open(MEMORY_FILE, 'r') as f:
                memory_data = json.load(f)
        else:
            # Default empty memory structure
            memory_data = {
                "talk_style": "adaptive",
                "difficulty_preference": "normal",
                "preferred_themes": [],
                "puzzle_history": {
                    "total_attempted": 0,
                    "success_rate": 0.0,
                    "common_mistakes": []
                },
                "communication_style": {
                    "prefers_hints": True,
                    "likes_explanations": True,
                    "feedback_preference": "encouraging"
                },
                "learning_patterns": {
                    "learns_from_mistakes": True,
                    "prefers_step_by_step": False,
                    "retention_notes": []
                },
                "last_updated": None
            }

        return {
            "success": True,
            "memory": memory_data
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to read memory: {str(e)}"
        }

@mcp.tool(description="Write user preferences and interaction patterns to persistent memory")
def write_memory(memory_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Write user preferences and interaction patterns to persistent memory.

    This should contain estimates on user preferences including:
    - Talk style preferences (formal, casual, technical, etc.)
    - Difficulty preferences (beginner, intermediate, advanced)
    - Preferred puzzle themes/tags (tactics, endgame, openings, etc.)
    - Learning patterns and progress tracking
    - Communication preferences and feedback patterns

    Args:
        memory_data: Dictionary containing user preferences and patterns to store

    Returns:
        {
            "success": bool,
            "message": str,
            "error": str (if success=false)
        }
    """
    try:
        # Add timestamp to memory data
        from datetime import datetime
        memory_data["last_updated"] = datetime.now().isoformat()

        # Write to file with proper formatting
        with open(MEMORY_FILE, 'w') as f:
            json.dump(memory_data, f, indent=2)

        return {
            "success": True,
            "message": f"Memory updated successfully. Stored {len(memory_data)} preference categories."
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to write memory: {str(e)}"
        }

@mcp.tool(description="Get system information about available puzzle types and parameters")
def get_system_info() -> Dict[str, Any]:
    """
    Get system information about available puzzle angles, difficulties, and server capabilities.

    This serves as a system prompt containing:
    - Available Lichess puzzle angles/themes
    - Difficulty level options
    - Server capabilities and endpoints
    - Usage guidelines for puzzle requests

    Returns:
        {
            "success": bool,
            "system_info": Dict containing comprehensive system information,
            "error": str (if success=false)
        }
    """
    try:
        system_info = {
            "server_name": "SMChess Puzzle Server",
            "description": "MCP server for interactive chess puzzle solving with Lichess integration",

            "available_angles": {
                "tactical_themes": [
                    "advancedPawn", "attackingF2F7", "attraction", "backRankMate",
                    "bishopEndgame", "bodenMate", "castling", "capturingDefender",
                    "crushing", "doubleBishopMate", "doubleCheck", "dovetailMate",
                    "enPassant", "endgame", "exposedKing", "fork", "hangingPiece",
                    "hookMate", "interference", "intermezzo", "kingsideAttack",
                    "knightEndgame", "mate", "mateIn1", "mateIn2", "mateIn3",
                    "mateIn4", "mateIn5", "middlegame", "oneMove", "opening",
                    "pawnEndgame", "pin", "promotion", "queenEndgame",
                    "queenRookEndgame", "queensideAttack", "quietMove",
                    "rookEndgame", "sacrifice", "skewer", "smotheredMate",
                    "superGM", "trappedPiece", "underPromotion", "xRayAttack",
                    "zugzwang"
                ],
                "puzzle_lengths": ["short", "long", "veryLong"],
                "popular_themes": ["tactics", "middlegame", "endgame", "mate", "fork", "pin", "skewer"],
                "difficulty_themes": ["crushing", "mate", "sacrifice", "quietMove"]
            },

            "available_difficulties": {
                "options": ["easiest", "easier", "normal", "harder", "hardest"],
                "descriptions": {
                    "easiest": "Much easier than user's rating",
                    "easier": "Easier than user's rating",
                    "normal": "Around user's rating (default ~1500)",
                    "harder": "Harder than user's rating",
                    "hardest": "Much harder than user's rating"
                }
            },

            "mcp_endpoints": [
                {
                    "name": "get_new_puzzle",
                    "description": "Fetch a new chess puzzle with optional theme and difficulty filters",
                    "parameters": ["angle (optional)", "difficulty (optional)"]
                },
                {
                    "name": "attempt_move",
                    "description": "Attempt to play a single chess move in UCI notation",
                    "parameters": ["move_uci", "from_state_moves_uci (optional)"]
                },
                {
                    "name": "get_state",
                    "description": "Search for specific puzzle states or get current state",
                    "parameters": ["puzzle_id", "moves_uci", "move_count", "message_id", "create_if_missing"]
                },
                {
                    "name": "send_message",
                    "description": "Send a message with optional puzzle state image",
                    "parameters": ["message", "puzzle_state (optional)"]
                },
                {
                    "name": "evaluate_chess_position",
                    "description": "Evaluate a chess position using Stockfish engine",
                    "parameters": ["puzzle_state (optional)"]
                },
                {
                    "name": "read_memory",
                    "description": "Read user preferences and interaction history",
                    "parameters": []
                },
                {
                    "name": "write_memory",
                    "description": "Write user preferences and interaction patterns",
                    "parameters": ["memory_data"]
                },
                {
                    "name": "get_system_info",
                    "description": "Get system information about available puzzle types",
                    "parameters": []
                }
            ],

            "usage_guidelines": {
                "puzzle_requests": "Users can request specific themes like 'endgame tactics' or 'mate in 2'",
                "difficulty_scaling": "Difficulty adapts based on user success/failure with puzzles",
                "move_format": "All moves use UCI notation (e.g. e2e4, g1f3, e7e8q)",
                "natural_language": "Users can interact in natural language - parse requests for themes/difficulty",
                "memory_usage": "Track user preferences and learning patterns in persistent memory"
            },

            "technical_details": {
                "puzzle_source": "Lichess.org puzzle database",
                "engine_evaluation": "Stockfish chess engine",
                "image_generation": "SVG to PNG board visualization",
                "state_management": "Comprehensive puzzle state tracking with deduplication",
                "memory_storage": "JSON-based persistent user preference storage"
            }
        }

        return {
            "success": True,
            "system_info": system_info
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get system info: {str(e)}"
        }

# Mount MCP endpoints onto FastAPI app
app.mount("/mcp", mcp.http_app)

if __name__ == "__main__":
    import sys

    if "--mcp" in sys.argv:
        # Run in pure MCP mode for testing with mcp-inspector
        mcp.run()
    else:
        # Run in FastAPI mode for production
        import uvicorn

        port = int(os.environ.get("PORT", 8000))
        host = "0.0.0.0"

        print(f"Starting FastAPI server with MCP on {host}:{port}")
        print(f"MCP endpoints available at: http://{host}:{port}/mcp/")
        print(f"Static files available at: http://{host}:{port}/static/")

        uvicorn.run(app, host=host, port=port)

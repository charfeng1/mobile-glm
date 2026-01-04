"""
Preference tool for storing and retrieving user preferences.

Uses a simple JSON file for storage with semantic categories
that are agent-defined (not pre-configured).
"""

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

# Storage location
PREFERENCES_FILE = Path(__file__).parent / "agent_workspace" / "preferences.json"


def _load_preferences() -> dict:
    """Load preferences from JSON file."""
    if PREFERENCES_FILE.exists():
        try:
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_preferences(prefs: dict) -> None:
    """Save preferences to JSON file."""
    PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)


@tool(
    "preference",
    "Store and retrieve user preferences with semantic categories. "
    "Categories are flexible - use any that make sense (food, apps, habits, contacts, etc.).",
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "get", "list", "delete"],
                "description": "Action to perform: set (save), get (query), list (show all), delete (remove)",
            },
            "category": {
                "type": "string",
                "description": "Category for the preference (e.g., 'food', 'apps', 'habits'). Required for set/get/delete.",
            },
            "key": {
                "type": "string",
                "description": "Key within the category (e.g., 'spice_level', 'favorite_app'). Required for set/get/delete.",
            },
            "value": {
                "type": "string",
                "description": "Value to store. Required for set action.",
            },
        },
        "required": ["action"],
    },
)
async def preference_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Handle preference operations."""
    action = args.get("action")
    category = args.get("category", "").strip()
    key = args.get("key", "").strip()
    value = args.get("value", "")

    try:
        if action == "set":
            # Validate required params
            if not category:
                return _error("category is required for set action")
            if not key:
                return _error("key is required for set action")
            if value is None:
                return _error("value is required for set action")

            prefs = _load_preferences()
            if category not in prefs:
                prefs[category] = {}
            prefs[category][key] = value
            _save_preferences(prefs)

            return _success(f"Saved: {category}/{key} = {value}")

        elif action == "get":
            if not category:
                return _error("category is required for get action")
            if not key:
                return _error("key is required for get action")

            prefs = _load_preferences()
            if category in prefs and key in prefs[category]:
                value = prefs[category][key]
                return _success(f"{category}/{key} = {value}")
            else:
                return _success(f"No preference found for {category}/{key}")

        elif action == "list":
            prefs = _load_preferences()

            if category:
                # List specific category
                if category in prefs:
                    items = prefs[category]
                    if items:
                        lines = [f"{category}:"]
                        for k, v in items.items():
                            lines.append(f"  {k}: {v}")
                        return _success("\n".join(lines))
                    else:
                        return _success(f"Category '{category}' is empty")
                else:
                    return _success(f"Category '{category}' not found")
            else:
                # List all
                if not prefs:
                    return _success("No preferences stored yet")
                lines = []
                for cat, items in prefs.items():
                    lines.append(f"{cat}:")
                    for k, v in items.items():
                        lines.append(f"  {k}: {v}")
                return _success("\n".join(lines))

        elif action == "delete":
            if not category:
                return _error("category is required for delete action")
            if not key:
                return _error("key is required for delete action")

            prefs = _load_preferences()
            if category in prefs and key in prefs[category]:
                del prefs[category][key]
                # Clean up empty categories
                if not prefs[category]:
                    del prefs[category]
                _save_preferences(prefs)
                return _success(f"Deleted: {category}/{key}")
            else:
                return _success(f"No preference found for {category}/{key} (nothing to delete)")

        else:
            return _error(f"Unknown action: {action}. Use: set, get, list, or delete")

    except Exception as e:
        return _error(f"Error: {str(e)}")


def _success(message: str) -> dict[str, Any]:
    """Return success response."""
    return {"content": [{"type": "text", "text": message}]}


def _error(message: str) -> dict[str, Any]:
    """Return error response."""
    return {"content": [{"type": "text", "text": f"Error: {message}"}]}

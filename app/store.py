import json
import os
from typing import Any, Dict, List


STATE_PATH = os.getenv("STATE_PATH", "state.json")


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {"history": []}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"history": []}
        if "history" not in data or not isinstance(data["history"], list):
            data["history"] = []
        return data
    except Exception:
        return {"history": []}


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def add_history_item(state: Dict[str, Any], item: Dict[str, Any], max_items: int = 200) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = state.get("history", [])
    history.append(item)
    # 최근 max_items개만 유지
    if len(history) > max_items:
        history = history[-max_items:]
    state["history"] = history
    return state

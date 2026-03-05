from typing import Any, Dict, Iterable, List, Optional, Tuple

from nekro_agent.api.schemas import AgentCtx
from nekro_agent.models.db_chat_channel import DBChatChannel, DefaultPreset

CONFIDENCE_MAP = {
    "VERY_HIGH": 0.95,
    "HIGH": 0.8,
    "MEDIUM": 0.6,
    "LOW": 0.3,
    "VERY_LOW": 0.1,
}

TYPE_TO_UNIT = {
    "FACTS": "fact",
    "PREFERENCES": "preference",
    "GOALS": "plan",
    "TRAITS": "context",
    "RELATIONSHIPS": "context",
    "EVENTS": "event",
    "TOPICS": "context",
}


def coerce_type_tags(metadata: Optional[Dict[str, Any]]) -> List[str]:
    if not metadata:
        return []
    value = metadata.get("TYPE") or metadata.get("type")
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).upper() for v in value if str(v).strip()]
    return [str(value).upper()]


def normalize_label(label: str) -> str:
    return str(label).strip().lower()


def build_labels(user_id: str, session_id: Optional[str], tags: Iterable[str]) -> List[str]:
    labels = []
    if user_id:
        labels.append(normalize_label(f"user:{user_id}"))
    if session_id:
        labels.append(normalize_label(f"session:{session_id}"))
    for tag in tags:
        labels.append(normalize_label(tag))
    return list(dict.fromkeys([l for l in labels if l]))


def map_unit_type(tags: List[str]) -> Optional[str]:
    if not tags:
        return None
    return TYPE_TO_UNIT.get(tags[0])

def extract_source_thread_id(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    if not metadata:
        return None
    v = metadata.get("SOURCE_THREAD_ID") or metadata.get("source_thread_id")
    if v is None:
        return None
    t = str(v).strip()
    return t if t else None

def extract_event_dates(metadata: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    if not metadata:
        return None, None
    start = metadata.get("EVENT_START") or metadata.get("event_start")
    end = metadata.get("EVENT_END") or metadata.get("event_end")
    s = str(start).strip() if start is not None else None
    e = str(end).strip() if end is not None else None
    return (s if s else None, e if e else None)


def extract_importance(metadata: Optional[Dict[str, Any]]) -> Optional[float]:
    if not metadata:
        return None
    value = metadata.get("IMPORTANCE") or metadata.get("importance")
    if value is None:
        return None
    try:
        num = float(value)
    except Exception:
        return None
    if num < 0 or num > 1:
        return None
    return num


def extract_confidence(metadata: Optional[Dict[str, Any]]) -> Optional[float]:
    if not metadata:
        return None
    value = metadata.get("CONFIDENCE") or metadata.get("confidence")
    if value is None:
        return None
    if isinstance(value, str):
        key = value.strip().upper()
        if key in CONFIDENCE_MAP:
            return CONFIDENCE_MAP[key]
    try:
        num = float(value)
    except Exception:
        return None
    if num < 0 or num > 1:
        return None
    return num


def extract_title(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    if not metadata:
        return None
    value = metadata.get("TITLE") or metadata.get("title")
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def merge_metadata(
    base: Optional[Dict[str, Any]],
    user_id: str,
    agent_id: str,
) -> Dict[str, Any]:
    merged: Dict[str, Any] = dict(base or {})
    if user_id:
        merged.setdefault("user_id", user_id)
    if agent_id:
        merged.setdefault("agent_id", agent_id)
    return merged


def match_tags(metadata: Any, tags: Optional[List[str]]) -> bool:
    if not tags:
        return True
    if not isinstance(metadata, dict):
        return False
    value = metadata.get("TYPE") or metadata.get("type")
    if value is None:
        return False
    wanted = {t.upper() for t in tags}
    if isinstance(value, list):
        return any(str(v).upper() in wanted for v in value)
    return str(value).upper() in wanted


def match_user(metadata: Any, user_id: str) -> bool:
    if not isinstance(metadata, dict):
        return False
    if user_id and str(metadata.get("user_id")) != str(user_id):
        return False
    return True


def match_user_session(metadata: Any, user_id: str, session_id: Optional[str]) -> bool:
    if not isinstance(metadata, dict):
        return False
    if user_id and str(metadata.get("user_id")) != str(user_id):
        return False
    if session_id is not None and str(metadata.get("session_id")) != str(session_id):
        return False
    return True


async def get_preset_id(_ctx: AgentCtx) -> str:
    channel = await DBChatChannel.get_or_none(chat_key=_ctx.chat_key)
    if channel:
        preset = await channel.get_preset()
        if isinstance(preset, DefaultPreset):
            return "default"
        return str(preset.id)
    return "default"

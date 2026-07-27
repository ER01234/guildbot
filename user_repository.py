import json
import logging
import time
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field
from tinydb import Query, TinyDB

logger = logging.getLogger(__name__)


class UserDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    first_name: str = ""
    last_name: str = ""
    eat_books: list[str] = Field(default_factory=list)
    devour_books: list[str] = Field(default_factory=list)
    user_lvl: int = 1
    user_tax_gold: int = 0
    user_tax_trophies: int = 0
    initial_trophies: Optional[int] = None
    current_trophies: Optional[int] = None
    tax_free: bool = False
    buffer_tax_free: bool = False
    registered_on: float = Field(default_factory=time.time)
    last_show: Optional[float] = None


class UserRepository:
    SCHEMA_VERSION = 2

    def __init__(self, db_path: str = "/guildbot/data/users_db.json"):
        self.db_path = db_path
        self._fix_bom_if_needed()
        self.db = TinyDB(self.db_path, ensure_ascii=False, indent=2, encoding="utf-8-sig")
        self.users_table = self.db.table("users")
        self.meta_table = self.db.table("meta")
        self._bootstrap()

    def _fix_bom_if_needed(self):
        """Исправляет UTF-8 BOM в файле БД если он есть."""
        try:
            with open(self.db_path, 'rb') as f:
                raw = f.read(3)
                if raw != b'\xef\xbb\xbf':
                    return
            with open(self.db_path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (FileNotFoundError, json.JSONDecodeError, IOError):
            pass

    def load_users(self) -> Dict[str, UserDocument]:
        result: Dict[str, UserDocument] = {}
        for item in self.users_table.all():
            validated = UserDocument.model_validate(item)
            result[str(validated.user_id)] = validated
        return result

    def save_users(self, users: Dict[str, UserDocument]) -> None:
        if not users:
            return

        self.users_table.truncate()
        validated_rows: list[UserDocument] = []

        for user_id_str, raw_data in users.items():
            normalized = self._normalize_user(raw_data.model_dump(), fallback_user_id=user_id_str)
            validated_rows.append(normalized)

        if validated_rows:
            self.users_table.insert_multiple([row.model_dump() for row in validated_rows])

        self._set_schema_version(self.SCHEMA_VERSION)

    def close(self) -> None:
        self.db.close()

    def _bootstrap(self) -> None:
        migrated = self._run_migrations_if_needed()
        if migrated:
            self._rewrite_all_as_latest()

    def _run_migrations_if_needed(self) -> bool:
        current_version = self._get_schema_version()
        if current_version >= self.SCHEMA_VERSION:
            return False

        if current_version < self.SCHEMA_VERSION:
            self._migrate_to_v2()
            current_version = self.SCHEMA_VERSION
            self._set_schema_version(current_version)
            return True

        return False

    def _migrate_to_v2(self) -> None:
        rows = self.users_table.all()
        self.users_table.truncate()
        migrated: list[UserDocument] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            migrated.append(self._normalize_user(row, fallback_user_id=row.get("user_id")))

        if migrated:
            self.users_table.insert_multiple([row.model_dump() for row in migrated])

    def _rewrite_all_as_latest(self) -> None:
        rows = self.users_table.all()
        self.users_table.truncate()
        rewritten: list[UserDocument] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            rewritten.append(self._normalize_user(row, fallback_user_id=row.get("user_id")))

        if rewritten:
            self.users_table.insert_multiple([row.model_dump() for row in rewritten])

        self._set_schema_version(self.SCHEMA_VERSION)

    def _normalize_user(self, raw_data: Dict[str, Any], fallback_user_id: Any) -> UserDocument:
        data = dict(raw_data)
        user_id = data.get("user_id", fallback_user_id)

        normalized = {
            "user_id": self._to_int(user_id, default=0),
            "first_name": self._to_str(data.get("first_name"), default=""),
            "last_name": self._to_str(data.get("last_name"), default=""),
            "eat_books": self._to_str_list(data.get("eat_books")),
            "devour_books": self._to_str_list(data.get("devour_books")),
            "user_lvl": self._to_int(data.get("user_lvl"), default=1),
            "user_tax_gold": self._to_int(data.get("user_tax_gold"), default=0),
            "user_tax_trophies": self._to_int(data.get("user_tax_trophies"), default=0),
            "initial_trophies": self._to_optional_int(data.get("initial_trophies")),
            "current_trophies": self._to_optional_int(data.get("current_trophies")),
            "tax_free": self._to_bool(data.get("tax_free"), default=False),
            "buffer_tax_free": self._to_bool(data.get("buffer_tax_free"), default=False),
            "registered_on": self._to_float(data.get("registered_on"), default=time.time()),
            "last_show": self._to_optional_float(data.get("last_show")),
        }

        return UserDocument.model_validate(normalized)

    def _get_schema_version(self) -> int:
        key_query = Query()
        record = self.meta_table.get(key_query.key == "schema_version")
        if not record:
            return 0
        return self._to_int(record.get("value"), default=0)

    def _set_schema_version(self, version: int) -> None:
        key_query = Query()
        self.meta_table.upsert({"key": "schema_version", "value": int(version)}, key_query.key == "schema_version")

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y"}:
                return True
            if lowered in {"false", "0", "no", "n"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    @staticmethod
    def _to_str(value: Any, default: str) -> str:
        if value is None:
            return default
        return str(value).strip()

    @staticmethod
    def _to_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

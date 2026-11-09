from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .settings import entity_config_path, validate_app_id


@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str
    scope: str
    aliases: tuple[str, ...]
    require_mention: bool
    regex: re.Pattern[str]

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "scope": self.scope,
            "require_mention": self.require_mention,
        }


@dataclass(frozen=True)
class EntityConfig:
    app_id: int
    game: str
    version: str
    entities: tuple[Entity, ...]
    raw: dict
    content_hash: str

    def by_id(self, entity_id: str) -> Entity:
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise KeyError(entity_id)


@dataclass(frozen=True)
class EntityMatch:
    entity: Entity
    clauses: tuple[str, ...]
    evidence: str


def _alias_pattern(alias: str) -> str:
    return rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"


def _canonical_hash(raw: dict) -> str:
    payload = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_entity_config(app_id: int, path: Path | None = None) -> EntityConfig:
    validate_app_id(app_id)
    path = Path(path) if path else entity_config_path(app_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Entity config must be a JSON object")
    if raw.get("app_id") != app_id:
        raise ValueError("Entity config app_id mismatch")
    if not isinstance(raw.get("config_version"), str) or not raw["config_version"]:
        raise ValueError("Entity config requires config_version")
    if not isinstance(raw.get("game"), str) or not raw["game"]:
        raise ValueError("Entity config requires game")
    items = raw.get("entities")
    if not isinstance(items, list) or not items:
        raise ValueError("Entity config requires a non-empty entities list")

    entities: list[Entity] = []
    ids: set[str] = set()
    aliases_seen: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Entity entry must be an object")
        entity_id = item.get("entity_id")
        name = item.get("name")
        scope = item.get("scope", "clause")
        aliases = item.get("aliases")
        require_mention = item.get("require_mention", scope != "document")
        if not isinstance(entity_id, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", entity_id):
            raise ValueError("Invalid entity_id")
        if entity_id in ids:
            raise ValueError(f"Duplicate entity_id: {entity_id}")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Entity {entity_id} requires name")
        if scope not in {"document", "clause"}:
            raise ValueError(f"Entity {entity_id} has invalid scope")
        if not isinstance(require_mention, bool):
            raise ValueError(f"Entity {entity_id} require_mention must be boolean")
        if not isinstance(aliases, list) or not aliases or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError(f"Entity {entity_id} requires non-empty string aliases")
        normalized = tuple(sorted({a.strip().lower() for a in aliases}, key=lambda a: (-len(a), a)))
        for alias in normalized:
            owner = aliases_seen.get(alias)
            if owner and owner != entity_id:
                raise ValueError(f"Alias {alias!r} belongs to both {owner} and {entity_id}")
            aliases_seen[alias] = entity_id
        pattern = re.compile("|".join(_alias_pattern(a) for a in normalized), re.IGNORECASE)
        entities.append(Entity(
            entity_id=entity_id,
            name=name,
            scope=scope,
            aliases=normalized,
            require_mention=require_mention,
            regex=pattern,
        ))
        ids.add(entity_id)

    return EntityConfig(
        app_id=app_id,
        game=raw["game"],
        version=raw["config_version"],
        entities=tuple(entities),
        raw=raw,
        content_hash=_canonical_hash(raw),
    )


def _merge_short_fragments(parts: list[str]) -> list[str]:
    merged: list[str] = []
    for part in parts:
        part = part.strip(" ,")
        if not part:
            continue
        tokens = re.findall(r"[A-Za-z0-9']+", part)
        if merged and (len(tokens) < 3 or not re.search(r"[A-Za-z]", part)):
            merged[-1] = f"{merged[-1]}, {part}"
        else:
            merged.append(part)
    return merged


def split_clauses(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    normalized = text.replace("\r", " ")
    normalized = re.sub(r"(?<![.!?;])\n+", ". ", normalized)
    normalized = re.sub(r"\n+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\.{2,}", ".", normalized)
    normalized = re.sub(r"\s+([.!?;])", r"\1", normalized).strip()
    clauses: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+", normalized):
        sentence = sentence.strip()
        if not sentence:
            continue
        contrast = re.split(
            r"\s+(?=(?:but|however|although|though|while|whereas|yet|still)\b)",
            sentence,
            flags=re.IGNORECASE,
        )
        for chunk in contrast:
            parts = re.split(r",\s+", chunk)
            clauses.extend(_merge_short_fragments(parts))
    return clauses or [normalized]


def match_entities(text: str, config: EntityConfig) -> list[EntityMatch]:
    clauses = split_clauses(text)
    if not clauses:
        return []
    matches: list[EntityMatch] = []
    for entity in config.entities:
        if entity.scope == "document":
            if entity.require_mention and not any(entity.regex.search(c) for c in clauses):
                continue
            selected = clauses if entity.require_mention else [text.strip()]
        else:
            selected = [c for c in clauses if entity.regex.search(c)]
        if not selected:
            continue
        evidence = " | ".join(selected)
        if len(evidence) > 800:
            evidence = evidence[:797] + "..."
        matches.append(EntityMatch(entity=entity, clauses=tuple(selected), evidence=evidence))
    return matches

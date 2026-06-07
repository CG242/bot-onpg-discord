import re
from dataclasses import dataclass

import config

SCORE_PATTERN = re.compile(r"(\d+)\s*-\s*(\d+)", re.IGNORECASE)
NOTE_PATTERN = re.compile(r"\([^)]*\)")


@dataclass
class ParsedMatch:
    player1: str
    score1: int
    score2: int
    player2: str
    ft_type: int
    winner_side: int  # 1 or 2


def sanitize_player_name(name) -> str:
    if hasattr(name, "display_name"):
        name = name.display_name
    name = str(name or "")
    line = name.splitlines()[0].strip()
    line = NOTE_PATTERN.sub("", line).strip()
    score = SCORE_PATTERN.search(line)
    if score:
        line = line[: score.start()].strip()
    return line[:64] if line else ""


def is_valid_player_name(name: str) -> bool:
    if not name or not name.strip():
        return False
    if "\n" in name or "\r" in name:
        return False
    if SCORE_PATTERN.search(name):
        return False
    return len(name.strip()) <= 64


def format_display_name(normalized_name: str, fallback: str = "") -> str:
    source = normalized_name or normalize_name(fallback)
    if not source:
        return "Inconnu"
    return " ".join(part.capitalize() for part in source.split())


def normalize_name(name: str) -> str:
    cleaned = sanitize_player_name(name) if name else ""
    cleaned = " ".join(cleaned.split()).lower()
    if cleaned.startswith("le "):
        cleaned = cleaned[3:].strip()
    return cleaned


def _clean_line(line: str) -> str:
    line = NOTE_PATTERN.sub("", line)
    return " ".join(line.strip().split())


def parse_single_line(line: str) -> ParsedMatch | None:
    text = _clean_line(line)
    if not text or text.startswith("/"):
        return None

    match = SCORE_PATTERN.search(text)
    if not match:
        return None

    score1 = int(match.group(1))
    score2 = int(match.group(2))

    if score1 == score2 or score1 < 0 or score2 < 0:
        return None

    player1 = text[: match.start()].strip()
    player2 = text[match.end() :].strip()

    if not player1 or not player2:
        return None

    ft_type = max(score1, score2)
    if ft_type not in config.FT_TYPES:
        return None

    winner_side = 1 if score1 > score2 else 2

    return ParsedMatch(
        player1=player1,
        score1=score1,
        score2=score2,
        player2=player2,
        ft_type=ft_type,
        winner_side=winner_side,
    )


def parse_match_message(content: str) -> ParsedMatch | None:
    """Compatibilité : retourne le premier match d'un message."""
    matches = parse_all_matches(content)
    return matches[0] if matches else None


def parse_all_matches(content: str) -> list[ParsedMatch]:
    results: list[ParsedMatch] = []
    for line in content.splitlines():
        parsed = parse_single_line(line)
        if parsed:
            results.append(parsed)

    if not results and content.strip():
        parsed = parse_single_line(content)
        if parsed:
            results.append(parsed)

    return results

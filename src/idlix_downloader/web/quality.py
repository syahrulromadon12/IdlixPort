"""Semantic quality choices survive changing format IDs, never changing quality."""

import hashlib
import json
from dataclasses import asdict, dataclass

from .errors import AppError

PROTOCOLS = {"http", "https", "m3u8_native", "http_dash_segments"}


@dataclass(frozen=True)
class Choice:
    quality_class: int
    width: int | None
    height: int
    evidence: str
    video_codec: str
    audio_codec: str
    audio_available: bool

    @property
    def choice_id(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:24]

    def public(self):
        return {"choice_id": self.choice_id, **asdict(self)}


def fingerprint(item, audio_available, separate_codec="unknown"):
    height = item.get("height")
    if not isinstance(height, int) or height <= 0 or item.get("has_drm"):
        return None
    if item.get("vcodec") == "none":
        return None
    width = item.get("width")
    width = width if isinstance(width, int) and width > 0 else None
    note = item.get("format_note", "")
    evidence = (
        "label"
        if note == "resolusi dari label manifest"
        else "probe"
        if note == "dimensi dari sampel video"
        else "dimensions"
    )
    required = item.get("session_required_height") or max(
        height, (width * 9 + 15) // 16 if width else height
    )
    quality_class = next(
        (n for n in (144, 240, 360, 480, 720, 1080, 1440, 2160, 4320) if required <= n), required
    )
    return Choice(
        quality_class,
        width,
        height,
        evidence,
        item.get("vcodec") or "unknown",
        (separate_codec if item.get("acodec") in (None, "none") else item["acodec"]),
        audio_available,
    )


def candidates(info):
    formats = info.get("formats") or ([info] if info.get("url") else [])
    audio_formats = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"]
    separate_audio = bool(audio_formats)
    separate_codec = (
        (max(audio_formats, key=lambda f: f.get("abr") or 0).get("acodec") or "unknown")
        if audio_formats
        else "unknown"
    )
    limit = info.get("session_max_height")
    result = []
    for item in formats:
        audio = item.get("acodec") not in (None, "none") or separate_audio
        choice = fingerprint(item, audio, separate_codec)
        if choice and (not limit or choice.quality_class <= limit):
            result.append((choice, item))
    return result


def choices(info):
    return [
        choice.public()
        for choice in sorted(
            {choice.choice_id: choice for choice, _ in candidates(info)}.values(),
            key=lambda value: (value.quality_class, value.choice_id),
        )
    ]


def select(info, policy, expected=None):
    items = candidates(info)
    if policy == "choice":
        items = [(c, f) for c, f in items if c.public() == expected]
        if len(items) != 1:
            raise AppError("QUALITY_CHANGED")
    elif policy in ("best", "max_1080"):
        if policy == "max_1080":
            items = [(c, f) for c, f in items if c.quality_class <= 1080]
        if not items:
            raise AppError("UNKNOWN_RESOLUTION")
        items = [max(items, key=lambda pair: (pair[0].quality_class, pair[1].get("tbr") or 0))]
    else:
        raise AppError("QUALITY_CHANGED")
    choice, video = items[0]
    if not choice.audio_available:
        raise AppError("QUALITY_CHANGED")
    selected = [video]
    if video.get("acodec") in (None, "none"):
        audio = [
            f
            for f in info.get("formats", [])
            if f.get("vcodec") == "none" and f.get("acodec") != "none"
        ]
        if not audio:
            raise AppError("QUALITY_CHANGED")
        selected.append(max(audio, key=lambda f: f.get("abr") or 0))
    return choice.public(), "+".join(f["format_id"] for f in selected), selected

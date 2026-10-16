from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

AUDIO_BITRATES = {"128", "192", "320"}


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    kind: Literal["video", "audio"] = "video"
    # video: "best" or a max height in pixels ("1080", "720", ...); audio: kbps ("128" | "192" | "320")
    quality: str = "best"

    @model_validator(mode="after")
    def _check_quality(self) -> "DownloadRequest":
        if self.kind == "audio":
            if self.quality == "best":
                self.quality = "192"
            if self.quality not in AUDIO_BITRATES:
                raise ValueError("Audio quality must be 128, 192 or 320.")
        elif self.quality != "best":
            if not self.quality.isdigit() or not (100 <= int(self.quality) <= 4320):
                raise ValueError("Video quality must be 'best' or a height such as 720.")
        return self

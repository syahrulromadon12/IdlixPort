"""Browser contracts intentionally exclude paths, cookies and format selectors."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..downloader import validate_url


class InspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=1, max_length=8192)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        return validate_url(value)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    inspection_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    choice_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")
    policy: Literal["choice", "best", "max_1080"] = "choice"
    folder_id: str = Field(default="default", pattern=r"^(default|[a-f0-9]{16})$")

    @model_validator(mode="after")
    def matching_policy(self):
        if (self.policy == "choice") != (self.choice_id is not None):
            raise ValueError("Concrete choice and policy do not match")
        return self


class ConnectRequest(InspectionRequest):
    profile_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    consent: bool

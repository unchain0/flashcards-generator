from __future__ import annotations

from pydantic import BaseModel, Field


class HealthRead(BaseModel):
    status: str


class AuthRead(BaseModel):
    authenticated: bool


class NotebookLMStatusRead(BaseModel):
    authenticated: bool
    status: str
    message: str


class CompanionTokenRead(BaseModel):
    access_token: str
    expires_in: int


class CompanionTokenVerify(BaseModel):
    access_token: str = Field(min_length=1, max_length=4096)


class CompanionIdentityRead(BaseModel):
    subject: str

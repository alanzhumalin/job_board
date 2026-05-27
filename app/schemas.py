from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class JobBase(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    company: str = Field(min_length=2, max_length=255)
    location: str = Field(min_length=2, max_length=255)
    employment_type: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=10)
    requirements: str = Field(min_length=10)
    salary_range: str | None = Field(default=None, max_length=255)


class JobCreate(JobBase):
    is_open: bool = True


class JobUpdate(JobBase):
    is_open: bool = True


class JobRead(JobBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_open: bool
    created_at: datetime
    updated_at: datetime


class ApplicationCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=50)
    cover_letter: str | None = Field(default=None, max_length=5000)


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    full_name: str
    email: EmailStr
    phone: str | None
    cover_letter: str | None
    created_at: datetime


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminApplicationsResponse(BaseModel):
    applications: list[ApplicationRead]

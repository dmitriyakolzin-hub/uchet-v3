"""Приложение не должно запускаться без надёжного JWT_SECRET."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _start_app(**env_overrides):
    """Импортирует app.main в отдельном процессе с заданным окружением."""
    env = {k: v for k, v in os.environ.items() if k != "JWT_SECRET"}
    env.update({k: v for k, v in env_overrides.items() if v is not None})
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_does_not_start_without_jwt_secret():
    result = _start_app()
    assert result.returncode != 0
    assert "JWT_SECRET: не задано" in result.stderr


@pytest.mark.parametrize(
    "secret",
    ["", "short", "your-secret-key-change-in-production", "change-me", "  " + "a" * 10 + "  "],
)
def test_does_not_start_with_weak_jwt_secret(secret):
    result = _start_app(JWT_SECRET=secret)
    assert result.returncode != 0
    assert "JWT_SECRET" in result.stderr


def test_does_not_start_without_db_password():
    env = {"JWT_SECRET": "s" * 64, "POSTGRES_PASSWORD": ""}
    result = _start_app(**env)
    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr


def test_starts_with_good_secret():
    result = _start_app(JWT_SECRET="s" * 64)
    assert result.returncode == 0, result.stderr

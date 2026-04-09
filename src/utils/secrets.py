"""Read API keys / tokens from Docker secrets first, env vars second.

In production (docker-compose.prod.yml) the backend container mounts each secret
as a file at `/run/secrets/<secret_name>`. In dev / staging the backend uses
plain env vars from .env / .env.staging. This helper unifies both lookup paths
so call sites don't have to know which mode they're running in.

Usage:
    from src.utils.secrets import read_secret

    api_key = read_secret("openai_api_key", "OPENAI_API_KEY")
    # 1. Tries /run/secrets/openai_api_key (Docker secret file)
    # 2. Falls back to os.environ["OPENAI_API_KEY"]
    # 3. Returns "" if neither is set
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Where Docker mounts secrets inside the container. Override at test time
# by setting DOCKER_SECRETS_DIR if needed.
SECRETS_DIR = Path(os.getenv("DOCKER_SECRETS_DIR", "/run/secrets"))


def read_secret(secret_name: str, env_var: str, default: str = "") -> str:
    """Return the value of a secret, preferring the Docker secrets file path.

    Args:
        secret_name: lowercase Docker secret name (matches the `secrets:` block
                     in docker-compose.prod.yml). Example: "openai_api_key".
        env_var:     uppercase env var name to fall back to. Example: "OPENAI_API_KEY".
        default:     value to return if neither source has it. Defaults to "".

    Returns:
        The secret value with surrounding whitespace stripped, or `default`.
    """
    secret_path = SECRETS_DIR / secret_name
    if secret_path.exists() and secret_path.is_file():
        try:
            value = secret_path.read_text(encoding="utf-8").strip()
            if value:
                # Don't log the value itself — just confirm the source
                logger.debug("secret_loaded_from_file", name=secret_name)
                return value
        except OSError as e:
            logger.warning(
                "secret_file_unreadable",
                name=secret_name,
                path=str(secret_path),
                error=str(e),
            )

    # Fall back to env var
    return os.getenv(env_var, default)

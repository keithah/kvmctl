"""Environment-backed configuration shared by CLI integrations."""
from __future__ import annotations

import os
from dataclasses import dataclass

from kvmctl.client import KvmClient


@dataclass(frozen=True)
class Settings:
    url: str
    token: str | None = None
    user: str | None = None
    password: str | None = None
    verify: bool | str = True
    host: str | None = None
    write_enabled: bool = False
    ssh_allowlist: tuple[str, ...] = ()


def settings_from_env(environ: dict[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    url = env.get("KVMCTL_URL", "").strip()
    if not url:
        raise ValueError("KVMCTL_URL is required")
    ca_bundle = env.get("KVMCTL_CA_BUNDLE", "").strip()
    verify: bool | str = ca_bundle or (not _truthy(env.get("KVMCTL_INSECURE")))
    allowlist = tuple(x.strip() for x in env.get("KVMCTL_SSH_ALLOWLIST", "").split(",") if x.strip())
    return Settings(
        url=url,
        token=env.get("KVMCTL_TOKEN") or None,
        user=env.get("KVMCTL_USER") or None,
        password=env.get("KVMCTL_PASSWORD") or None,
        verify=verify,
        host=env.get("KVMCTL_HOST") or None,
        write_enabled=_truthy(env.get("KVMCTL_WRITE_ENABLED")),
        ssh_allowlist=allowlist,
    )


def client_from_settings(settings: Settings) -> KvmClient:
    client = KvmClient(settings.url, verify=settings.verify, host=settings.host)
    if settings.token:
        client.set_token(settings.token)
    elif settings.user and settings.password:
        client.login(settings.user, settings.password)
    else:
        raise ValueError("KVMCTL_TOKEN or both KVMCTL_USER and KVMCTL_PASSWORD are required")
    return client


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}

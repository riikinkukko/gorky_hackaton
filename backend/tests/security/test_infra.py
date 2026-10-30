"""Инфраструктура: статические проверки compose, Dockerfile, Caddy, CI, .env.example.

Ничего не запускают — читают файлы репозитория, поэтому идут в обычном `make test` и в CI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from .conftest import ROOT


def _compose(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    return data


def _ports(cfg: dict[str, Any], service: str) -> list[str]:
    return [str(p) for p in (cfg.get("services", {}).get(service, {}) or {}).get("ports", [])]


# ---------------------------------------------------------------- порты (регрессия)


def test_base_compose_publishes_no_ports() -> None:
    cfg = _compose("docker-compose.yml")
    for name, svc in cfg["services"].items():
        assert not svc.get("ports"), f"{name}: порты публикуются только в override/prod/shared"


def test_shared_mode_listens_on_localhost_only() -> None:
    cfg = _compose("docker-compose.shared.yml")
    assert _ports(cfg, "db") == []
    assert _ports(cfg, "api") == []
    assert all(p.startswith("127.0.0.1:") for p in _ports(cfg, "caddy"))


def test_prod_mode_publishes_only_http() -> None:
    cfg = _compose("docker-compose.prod.yml")
    assert _ports(cfg, "db") == [] and _ports(cfg, "api") == []
    published = {p.split(":")[-1].split("/")[0] for p in _ports(cfg, "caddy")}
    assert published <= {"80", "443"}


def test_prod_and_shared_set_app_env_prod() -> None:
    for f in ("docker-compose.prod.yml", "docker-compose.shared.yml"):
        assert _compose(f)["services"]["api"]["environment"]["APP_ENV"] == "prod", f


def test_override_binds_db_to_localhost() -> None:
    for p in _ports(_compose("docker-compose.override.yml"), "db"):
        assert p.startswith("127.0.0.1:"), p


# ---------------------------------------------------------------- контейнеры


def _dockerfile_final_stage(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split("\nFROM ")[-1]


def test_api_image_runs_as_non_root() -> None:
    stage = _dockerfile_final_stage(ROOT / "backend" / "Dockerfile")
    users = re.findall(r"^USER\s+(\S+)", stage, flags=re.M)
    assert users and users[-1] not in ("root", "0", "0:0")


def test_shared_mode_limits_cpu_and_pids() -> None:
    svcs = _compose("docker-compose.shared.yml")["services"]
    for name in ("api", "db", "web", "caddy"):
        limits = (svcs.get(name) or {}).get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("memory"), f"{name}: memory"
        assert limits.get("cpus"), f"{name}: cpus"
        assert (svcs.get(name) or {}).get("pids_limit"), f"{name}: pids_limit"


# ---------------------------------------------------------------- секреты


def test_env_files_ignored() -> None:
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gi and "!.env.example" in gi
    di = (ROOT / "backend" / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in di


def test_env_example_has_no_real_secrets() -> None:
    secret_keys = ("GIGACHAT_CREDENTIALS", "YANDEX_API_KEY", "YANDEX_IAM_TOKEN")
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() in secret_keys:
            assert value.strip() == "", f"{key} должен быть пустым в .env.example"


def test_no_private_keys_in_tree() -> None:
    pattern = re.compile(r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")
    skip = {".git", "node_modules", ".venv", "__pycache__", "dist"}
    hits = []
    for p in ROOT.rglob("*"):
        if any(part in skip for part in p.parts) or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(str(p.relative_to(ROOT)))
        except OSError:
            continue
    assert not hits, hits


def test_prod_refuses_default_db_password() -> None:
    from app.config import Settings

    with pytest.raises(Exception):  # noqa: B017
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="prod",
            storage_impl="postgres",
            database_url="postgresql+psycopg://app:app@db:5432/app",
            session_secret="s" * 48,
        )


# ---------------------------------------------------------------- веб-сервер


def test_caddy_sets_basic_headers() -> None:
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    for h in ("Strict-Transport-Security", "X-Content-Type-Options", "Permissions-Policy"):
        assert h in caddy


def test_caddy_sets_csp_and_hides_server() -> None:
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in caddy and "frame-ancestors 'none'" in caddy
    assert "-Server" in caddy


def test_caddy_enforces_csp() -> None:
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    enforced = re.findall(r"^\s*Content-Security-Policy\s", caddy, flags=re.M)
    assert enforced, "нужен заголовок Content-Security-Policy, не только -Report-Only"


# ---------------------------------------------------------------- CI / деплой


def _deploy() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    )
    return data


def test_third_party_actions_pinned_by_sha() -> None:
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        for uses in re.findall(r"uses:\s*([^\s#]+)", wf.read_text(encoding="utf-8")):
            if uses.startswith(("actions/", "./")):
                continue
            assert re.search(r"@[0-9a-f]{40}$", uses), f"{wf.name}: {uses}"


def test_deploy_job_protected() -> None:
    job = _deploy()["jobs"]["deploy"]
    assert job.get("environment"), "секреты VPS — в environment с required reviewers"
    ssh = next(s for s in job["steps"] if "ssh-action" in str(s.get("uses", "")))
    assert ssh["with"].get("fingerprint"), "проверка ключа хоста"
    assert "root" not in str(ssh["with"].get("username", "")).lower()


def test_remote_deploy_ignores_arguments() -> None:
    """Forced command: скрипт не читает аргументы и SSH_ORIGINAL_COMMAND (SEC-04)."""
    text = (ROOT / "deploy" / "remote-deploy.sh").read_text(encoding="utf-8")
    code = "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#"))
    assert "set -euo pipefail" in code
    for bad in ("$1", "$@", "$*", "SSH_ORIGINAL_COMMAND", "eval "):
        assert bad not in code, bad


def test_harden_script_installs_restricted_key() -> None:
    text = (ROOT / "deploy" / "harden-deploy-user.sh").read_text(encoding="utf-8")
    assert re.search(r'restrict,command=\\?"sudo -n /usr/local/sbin/gorky-deploy\\?"', text)
    assert "gpasswd -d deploy docker" in text
    assert "visudo -c" in text


def test_harden_script_cannot_lock_out_root() -> None:
    """grep -vF "" удаляет все строки: при пустом/битом gh_actions.pub root теряет все ключи.

    Нужны: отказ при отсутствии/пустом/многострочном ключе; отказ, если это единственный ключ
    root; правка authorized_keys через временный файл с проверкой, что остальные ключи на месте.
    """
    text = (ROOT / "deploy" / "harden-deploy-user.sh").read_text(encoding="utf-8")
    assert 'grep -vF "$(awk' not in text
    assert re.search(r"\[ -s \"\$key\" \]|\[\[ -s \$key \]\]", text), "ключ обязателен"


# ---------------------------------------------------------------- v3: адрес клиента (SEC-17)


def test_uvicorn_trusts_only_private_proxies() -> None:
    text = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r'--forwarded-allow-ips[=", ]+([^"\s]+)', text)
    assert m, "нужен явный список доверенных прокси"
    assert m.group(1) != "*", (
        "доверять только частным сетям: 127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,…"
    )


def test_caddy_trusts_private_proxies_for_client_ip() -> None:
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert re.search(r"trusted_proxies\s+static\s+private_ranges", caddy)


def test_nginx_overwrites_real_ip_in_every_location() -> None:
    """Регрессия: каждый proxy_pass в nginx-voice.conf перезаписывает X-Real-IP адресом клиента."""
    conf = (ROOT / "deploy" / "shared" / "nginx-voice.conf").read_text(encoding="utf-8")
    blocks = re.findall(r"location[^{]*\{([^}]*)\}", conf)
    assert blocks
    for b in blocks:
        if "proxy_pass" in b:
            assert "proxy_set_header X-Real-IP $remote_addr;" in b
            assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in b


# ---------------------------------------------------------------- v3: деплой и CSP


def test_remote_deploy_is_serialized() -> None:
    text = (ROOT / "deploy" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert "flock" in text


def test_csp_connect_src_same_origin_only() -> None:
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    m = re.search(r"connect-src([^;]*);", caddy)
    assert m
    sources = m.group(1).split()
    assert "wss:" not in sources and "ws:" not in sources and "https:" not in sources

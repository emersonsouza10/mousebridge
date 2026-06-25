"""Credencial única global: injeção de usuário/senha por conexão, no cliente."""

from __future__ import annotations

from conninfo.config import ConnDef, Credentials, ConnInfoConfig, load_conninfo_config


def _cfg(creds: Credentials) -> ConnInfoConfig:
    return ConnInfoConfig(credentials=creds)


def test_global_credentials_injected_for_login_engines():
    cfg = _cfg(Credentials(username="leitor", password="s3nha"))
    conn = ConnDef(id="ora", engine="oracle", name="O", dsn={"dsn": "HOMOL"})
    dsn, secret = cfg.resolve_login(conn, agent_secret=None)
    assert dsn == {"dsn": "HOMOL", "user": "leitor"}
    assert secret == "s3nha"


def test_non_login_engine_ignores_credentials():
    cfg = _cfg(Credentials(username="leitor", password="s3nha"))
    conn = ConnDef(id="x", engine="outro", name="X", dsn={"file": "/x.db"})
    dsn, secret = cfg.resolve_login(conn, agent_secret=None)
    assert dsn == {"file": "/x.db"}
    assert secret is None


def test_connection_specific_user_wins_over_global():
    cfg = _cfg(Credentials(username="global", password="g"))
    conn = ConnDef(id="ora", engine="oracle", name="O", dsn={"dsn": "HOMOL", "user": "especifico"})
    dsn, secret = cfg.resolve_login(conn, agent_secret=None)
    assert dsn["user"] == "especifico"  # dsn da conexão tem precedência
    assert secret == "g"                # senha global ainda se aplica


def test_agent_secret_overrides_global_password():
    cfg = _cfg(Credentials(username="leitor", password="global"))
    conn = ConnDef(id="ora", engine="oracle", name="O", dsn={"dsn": "HOMOL"})
    _, secret = cfg.resolve_login(conn, agent_secret="senha-do-agente")
    assert secret == "senha-do-agente"


def test_password_from_env_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("CONNINFO_DB_PASSWORD", "do-ambiente")
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(
        "conninfo:\n  credentials:\n    username: leitor\n    password_env: CONNINFO_DB_PASSWORD\n",
        encoding="utf-8",
    )
    cfg = load_conninfo_config(str(cfg_file))
    assert cfg.credentials.username == "leitor"
    assert cfg.credentials.password == "do-ambiente"

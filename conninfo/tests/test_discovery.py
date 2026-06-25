"""Descoberta mesclada no catálogo vivo: tnsnames aparece em list_connections."""

from __future__ import annotations

from conninfo.ci_discovery import discover, merged_connections
from conninfo.ci_gateway.api import list_connections
from conninfo.config import ConnDef, ConnInfoConfig, DiscoveryConfig
from conninfo.tests.test_e2e import _HostThread, _wait_listening, _free_port
from conninfo.tests.test_oracle import TNSNAMES
from zephyrlink.config import SecurityConfig

KEY = "chave-de-teste"


def _config_with_tns(tmp_path, *, manual=()):
    f = tmp_path / "tnsnames.ora"
    f.write_text(TNSNAMES, encoding="utf-8")
    return ConnInfoConfig(
        enabled=True,
        port=_free_port(),
        connections=tuple(manual),
        discovery=DiscoveryConfig(oracle_enabled=True, oracle_tnsnames=(str(f),)),
        security=SecurityConfig(shared_key=KEY),
    )


def test_discover_finds_tnsnames_aliases(tmp_path):
    config = _config_with_tns(tmp_path)
    found = {c.id: c for c in discover(config)}
    assert set(found) == {"oracle_homol", "oracle_prd", "oracle_producao"}
    assert found["oracle_homol"].engine == "oracle"
    assert found["oracle_homol"].source == "tnsnames"
    assert found["oracle_homol"].dsn == {"dsn": "HOMOL"}
    assert found["oracle_homol"].read_only is True  # padrão seguro


def test_manual_overrides_discovered_by_id(tmp_path):
    manual = (ConnDef(id="oracle_homol", engine="oracle", name="Override", dsn={"dsn": "OUTRO"}),)
    config = _config_with_tns(tmp_path, manual=manual)
    by_id = {c.id: c for c in merged_connections(config)}
    assert by_id["oracle_homol"].name == "Override"  # manual venceu
    assert by_id["oracle_homol"].source == "manual"
    assert by_id["oracle_homol"].dsn == {"dsn": "OUTRO"}
    assert "oracle_prd" in by_id  # demais descobertas seguem presentes


def test_discovered_appears_in_live_catalog(tmp_path):
    config = _config_with_tns(tmp_path)
    host = _HostThread(config)
    host.start()
    _wait_listening("127.0.0.1", config.port)
    try:
        cat = {c["id"]: c for c in list_connections(host="127.0.0.1", port=config.port, key=KEY)}
        assert "oracle_homol" in cat
        assert cat["oracle_homol"]["engine"] == "oracle"
        assert cat["oracle_homol"]["source"] == "tnsnames"
    finally:
        host.stop()

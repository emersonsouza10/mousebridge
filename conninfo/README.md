# connInfo

Execução remota controlada de **Oracle** sobre o canal do MouseBridge. O **servidor
(agente)** nunca conecta no banco: ele pede, e a **máquina cliente** (`ProviderHost`)
executa localmente com o Oracle Client/credenciais/VPN dela e devolve só resultados em
JSON. Não é túnel de rede — trafega SQL e linhas, não bytes de conexão.

> **Escopo:** Oracle-only. A arquitetura de providers continua plugável, mas o produto
> hoje só registra o `oracle` (`python-oracledb`). Os testes ponta a ponta rodam o
> `OracleProvider` de verdade sobre um `oracledb` falso (sqlite stdlib, só no test code),
> então a suíte passa sem um Oracle real.

Arquitetura completa em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md); requisitos em
[`../SPEC_conninfo.md`](../SPEC_conninfo.md).

## Papéis (atenção à inversão, como no foshar)

| Conceito | Onde roda | Componente | Papel TCP |
|---|---|---|---|
| tem o banco | máquina **cliente** | `ProviderHost` | **escuta** |
| roda o agente | máquina **servidor** | `Gateway` / API | **disca** |

## Uso

**No cliente (dono do banco)** — publica as conexões e escuta:

```bash
python -m conninfo host -c config.yaml
```

**No servidor (agente)** — lista e consulta:

```bash
python -m conninfo connections --host 10.0.0.5
python -m conninfo query --host 10.0.0.5 --conn oracle_prod --sql "select * from dual"
```

## Como o agente chama (lib in-process)

```python
import conninfo

conn = conninfo.connect("oracle_prod", host="10.0.0.5", key="segredo")
for row in conn.execute("select owner, table_name from dba_tables where owner='HUMASTER'"):
    print(row)

cols = conn.describe("HUMASTER.CLIENTE")      # colunas
idx  = conn.list_indexes("HUMASTER.CLIENTE")  # índices
pk   = conn.list_pk("HUMASTER.CLIENTE")       # chave primária
fk   = conn.list_fk("HUMASTER.CLIENTE")       # chaves estrangeiras
plan = conn.explain("select * from humaster.cliente where id = :1")

conn.begin()                                   # transação (em conexão read-write)
conn.execute("update humaster.cliente set ativo = 1 where id = 42")
conn.commit()                                  # ou conn.rollback()
conn.close()
```

É a API uniforme que o OpenClaw (Oracle Advisor, Incident Analyzer, Inventário…) consome.

## Estado atual

**Protocolo/transporte:** `list_connections / connect / execute / fetch (streaming) /
cancel / close_cursor / disconnect`; **transações** `begin / commit / rollback`;
**metadata** `version / list_tables / describe_table / list_indexes / list_constraints /
list_pk / list_fk / explain`; multiplexação por `req_id`; sessões em memória no cliente
com timeout/reaping; reuso de HMAC + TLS + allowlist do core.

**Cache de metadata:** resultados de metadata são cacheados por conexão com TTL
(`conninfo.metadata_cache_ttl_s`, padrão 300s; 0 desliga) — economiza ida ao dicionário
de dados para agentes de inventário.

**Provider:** **Oracle** (`python-oracledb`, thin/thick). Único engine registrado.
Credencial **única global** (`conninfo.credentials`) para todas as conexões, resolvida
no cliente; a senha pode vir de `password_env` (fora do arquivo) e nunca trafega.

**Segurança (SPEC §16/§22, RNF05):** **read-only por padrão** (DDL/DML bloqueados);
**ACL por usuário** (identidade `user`+`token` no handshake, catálogo filtrado, whitelist
de conexões, `mode: read-only` que força só-leitura mesmo em conexão `rw`); limite de
linhas; **timeout server-side** com cancelamento; **cancelamento remoto** (`conn.cancel()`);
**rate-limit por usuário**; auditoria JSON-lines (hash + preview, nunca senha).

**Descoberta:** scanner de `tnsnames.ora` (`ci_discovery/oracle.py`) **mesclado no
catálogo vivo** — os aliases aparecem em `list_connections` e no `conninfo status`
(cadastro manual vence em conflito de `id`). O caminho do `tnsnames.ora` pode ser
indicado em `conninfo.discovery.oracle.tnsnames`; se omitido, usa `$TNS_ADMIN` e
`$ORACLE_HOME/network/admin`.

```yaml
conninfo:
  discovery:
    oracle:
      enabled: true
      tnsnames: "C:\\oracle\\network\\admin\\tnsnames.ora"   # ou uma lista; ou omita p/ usar env
```

**Não exercitado contra Oracle real:** os providers rodam nos testes sobre um `oracledb`
falso (sqlite). Thin/thick, wallet, EZConnect e a metadata via dicionário de dados real
ainda precisam ser validados num Oracle de verdade.

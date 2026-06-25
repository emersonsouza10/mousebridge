# connInfo — Database Gateway sobre o canal do MouseBridge

> **Status:** desenho de arquitetura (pré-implementação). Este documento define a
> arquitetura, o protocolo, o modelo de mensagens, as sessões, os providers, a descoberta,
> a segurança, o plano por fases e a análise crítica **antes** de escrever código.

---

## 1. Princípio

O servidor (onde roda o agente / OpenClaw) **não tem** Oracle Client, psql, ODBC nem VPN.
A máquina cliente **já acessa** os bancos com as ferramentas instaladas. A connInfo
reaproveita essa capacidade: transporta apenas **comandos SQL e resultados** sobre o canal
seguro do MouseBridge. O servidor nunca fala com o banco — todo acesso ocorre no cliente.

```
Agente OpenClaw
      │  conn = connInfo.connect("oracle_prd"); conn.execute(sql)
connInfo API (lib in-process, no servidor/agente)
      │
ci_protocol  ── envelope JSON + framing uint32 ──┐
      │                                          │  conexão TCP dedicada
ci_gateway (lado requisitante, ESCUTA o agente)  │  reusa zephyrlink.transport
      │                                          │  (HMAC + TLS + allowlist)
══════ Canal criptografado MouseBridge ══════════╪═══════════════════════════
      │                                          │
ci_provider_host (lado DONO do banco, no cliente)┘
      │
DatabaseProvider (oracle / postgres / sqlserver / mysql / sqlite)
      │  driver nativo (python-oracledb, psycopg, pyodbc, …)
Banco de Dados
```

---

## 2. Terminologia — atenção à inversão de papéis

⚠️ **Ponto crítico.** No código do MouseBridge, **quem possui o recurso roda o serviço que
escuta**, e quem requisita roda o cliente. No foshar: a máquina dona dos arquivos roda
`FosharService` (servidor TCP); quem edita roda `FosharClient`. A connInfo segue o **mesmo
padrão** — e isso é o inverso da nomenclatura informal do pedido.

| Papel conceitual (pedido) | Onde roda | Componente connInfo | Análogo TCP |
|---|---|---|---|
| "MouseBridge Client" — tem o banco | máquina **cliente** | `ProviderHost` | **servidor** (escuta) |
| "MouseBridge Server" — roda o agente | máquina **servidor** | `Gateway` + `connInfo API` | **cliente** (conecta) |

Ou seja: o processo que detém as conexões de banco (`ProviderHost`) **escuta**; o agente,
através da `connInfo API`, **disca** para ele. Toda a documentação abaixo usa
**ProviderHost** (lado banco/cliente) e **Gateway/API** (lado agente/servidor) para evitar a
ambiguidade da palavra "servidor".

---

## 3. Estrutura do projeto

O foshar mora em `mousebridge/foshar/` (top-level), **não** em `skills/`. Para manter a
convenção do repositório, a connInfo segue o mesmo padrão — um pacote top-level `conninfo/`.
(O pedido sugeriu `skills/conninfo/`; recomendo **não** introduzir um diretório `skills/`
agora para não divergir do foshar/zephyrlink. O mapeamento de nomes está abaixo.)

```
conninfo/
    __init__.py
    __main__.py              # CLI: host | shell | discover | status | gui
    config.py                # lê config.yaml (seção conninfo: + reusa security:)
    util.py

    ci_protocol/
        __init__.py
        messages.py          # CiMsgType (StrEnum) + CiMessage (envelope {"t","d"})
        channel.py           # CiChannel (framing) + accept/connect handshake HMAC
        mux.py               # multiplexação req_id/session sobre 1 socket

    ci_gateway/              # lado AGENTE (requisitante)
        __init__.py
        api.py               # connInfo.connect(...) → Connection/Cursor (lib pública)
        client.py            # cliente RPC assíncrono multiplexado
        cursor.py            # iterador de streaming (fetchmany)

    ci_host/                 # lado BANCO (dono do recurso, escuta)
        __init__.py
        service.py           # ProviderHost: aceita conexões, handshake, despacha
        session.py           # Session + SessionManager (timeout, reaping)
        registry.py          # ProviderRegistry: engine → classe de provider
        executor.py          # thread pool: roda DBAPI bloqueante fora do event loop

    ci_discovery/
        __init__.py
        base.py              # ConnectionScanner (interface) + ConnectionRef
        oracle.py            # tnsnames.ora / sqlnet.ora / TNS_ADMIN / ORACLE_HOME
        postgres.py          # pg_service.conf / .pgpass
        sqlserver.py         # DSNs e drivers ODBC
        mysql.py             # my.cnf / .mylogin.cnf
        sqlite.py            # arquivos .db cadastrados

    providers/
        base.py              # DatabaseProvider (ABC) + DbError hierarchy
        oracle.py
        postgres.py
        sqlserver.py
        mysql.py
        sqlite.py

    ci_security/
        __init__.py
        acl.py               # whitelist de conexões + permissões por usuário
        audit.py             # trilha append-only JSON-lines (espelha launcher/audit)
        limits.py            # row limit, time limit, rate limit

    docs/
        ARQUITETURA.md       # este arquivo
    examples/
        config.conninfo.example.yaml
    tests/
```

**Plugabilidade:** a lógica de banco vive só em `providers/` e `ci_discovery/`. `ci_protocol`,
`ci_gateway` e `ci_host` não conhecem nenhum banco específico — falam só com a interface
`DatabaseProvider` e com o `ProviderRegistry`. Nenhuma lógica do MouseBridge vaza para
dentro dos providers.

---

## 4. Protocolo de comunicação

Mesma base do foshar: **conexão TCP dedicada** (não disputa o socket de input/arquivos),
**framing uint32 big-endian** de `zephyrlink.transport.framing`, **handshake HMAC** e **TLS
opcional** de `zephyrlink.transport.security`, **allowlist de host** na aceitação. Envelope
JSON idêntico: `{"t": tipo, "d": dados}`.

### 4.1 Diferença essencial vs foshar: multiplexação

O foshar é **sequencial** — um pedido por vez por conexão (`request()` envia e bloqueia
esperando o `FS_REPLY`). Isso **não serve** para a connInfo, por três motivos:

1. **Múltiplas sessões simultâneas** sobre o mesmo canal (vários bancos / consultas).
2. **Streaming**: um `fetchmany` em andamento não pode bloquear um `describe_table` paralelo.
3. **Cancelamento remoto**: enquanto uma consulta longa está rodando e segurando a resposta,
   é preciso conseguir enviar `CANCEL` pelo mesmo canal lógico.

Solução: **multiplexação** com correlação por `req_id` (+ `session`). Cada mensagem carrega
um `req_id`; o lado que recebe despacha de forma assíncrona e responde fora de ordem. No
`ProviderHost`, cada execução de SQL roda num **thread do executor** (drivers DBAPI são
síncronos/bloqueantes), deixando o event loop livre para receber `CANCEL`/controle e servir
outras sessões. `ci_protocol/mux.py` mantém um dicionário `req_id → Future` no lado do
Gateway e um `req_id → asyncio.Task` no lado do Host.

```
Gateway                                   ProviderHost
  │  EXECUTE(req=7, session=s1, sql=…) ──▶ │ cria Task(req=7) ─▶ executor thread → cursor
  │  EXECUTE(req=8, session=s2, sql=…) ──▶ │ cria Task(req=8) ─▶ executor thread
  │  ◀── RESULT_META(req=8, columns=…) ──  │ (s2 terminou primeiro)
  │  CANCEL(req=7) ──────────────────────▶ │ provider.cancel(s1) (OCIBreak/pg_cancel)
  │  ◀── ERROR(req=7, "cancelado") ──────  │
```

### 4.2 Fluxo de uma consulta com streaming

```
connect:   CONNECT(connection="oracle_prd")        → CONNECT_OK(session="abc123")
execute:   EXECUTE(session, sql, max_rows, timeout) → RESULT_META(req, cursor="cur9", columns=[…])
fetch:     FETCH(cursor="cur9", n=100)              → RESULT_CHUNK(req, rows=[…], last=false)
fetch:     FETCH(cursor="cur9", n=100)              → RESULT_CHUNK(req, rows=[…], last=true)
close cur: CLOSE_CURSOR(cursor="cur9")              → REPLY(ok=true)
disconnect:DISCONNECT(session)                      → REPLY(ok=true)
```

O servidor (agente) consome em partes: a `connInfo API` expõe `cursor.fetchmany(n)` e
iteração lazy; só puxa o próximo `RESULT_CHUNK` quando o consumidor pede (backpressure
natural — ver §9).

---

## 5. Modelo de mensagens

`ci_protocol/messages.py`, espelhando `FsMsgType`/`FsMessage`:

```python
class CiMsgType(StrEnum):
    # handshake (reusa o desafio-resposta HMAC do core)
    AUTH_CHALLENGE = "auth_challenge"
    AUTH_RESPONSE  = "auth_response"
    AUTH_OK        = "auth_ok"          # d: {"connections": [...catálogo permitido...]}
    AUTH_FAIL      = "auth_fail"
    # descoberta / catálogo
    LIST_CONNECTIONS = "list_connections"
    # ciclo de sessão
    CONNECT     = "connect"             # d: {connection, req_id}
    CONNECT_OK  = "connect_ok"          # d: {session, engine, req_id}
    DISCONNECT  = "disconnect"          # d: {session, req_id}
    # execução
    EXECUTE     = "execute"             # d: {session, sql, params?, max_rows, timeout_ms, req_id}
    RESULT_META = "result_meta"         # d: {req_id, cursor, columns:[{name,type,nullable}], rowcount?}
    FETCH       = "fetch"               # d: {cursor, n, req_id}
    RESULT_CHUNK= "result_chunk"        # d: {req_id, rows:[[...]], last:bool}
    CLOSE_CURSOR= "close_cursor"        # d: {cursor, req_id}
    CANCEL      = "cancel"              # d: {req_id_alvo}  (ou {cursor})
    # transação
    BEGIN = "begin"; COMMIT = "commit"; ROLLBACK = "rollback"   # d: {session, req_id}
    # metadata (mapeiam para métodos do provider)
    DESCRIBE     = "describe"           # d: {session, table, req_id}
    LIST_TABLES  = "list_tables"        # d: {session, schema?, req_id}
    LIST_VIEWS   = "list_views"
    LIST_INDEXES = "list_indexes"       # d: {session, table, req_id}
    LIST_PK      = "list_pk"
    LIST_FK      = "list_fk"
    EXPLAIN      = "explain"            # d: {session, sql, req_id}
    VERSION      = "version"            # d: {session, req_id}
    # resposta genérica de controle (ok/erro), espelha FS_REPLY
    REPLY = "reply"                     # d: {req_id, ok:bool, error?, **dados}
    ERROR = "error"                     # d: {req_id, code, message}
```

`CiMessage` é igual ao `FsMessage`: `frozen dataclass`, `encode()`/`decode()` com envelope
`{"t","d"}` e `ProtocolError` para payload malformado.

### 5.1 Exemplos (API externa pedida)

```jsonc
// → list_connections
{"action":"list_connections"}
// ← [{"id":"oracle_prod","engine":"oracle","name":"Oracle Produção"}]

// → connect
{"action":"connect","connection":"oracle_prod"}
// ← {"session":"abc123"}

// → execute
{"action":"execute","session":"abc123","sql":"select * from dual"}

// → describe_table
{"action":"describe_table","table":"HUMASTER.CLIENTE"}

// → disconnect
{"action":"disconnect","session":"abc123"}
```

> O `action` plano do pedido é a **fachada pública** (o que o agente vê na lib). Internamente
> ele é traduzido para `CiMsgType` + `req_id` no envelope `{"t","d"}`. Mantém a API amigável
> sem perder a multiplexação/correlação no fio.

---

## 6. Providers

`providers/base.py` — interface única que todo banco implementa. Cada método recebe um
**handle de sessão** (a conexão DBAPI viva) e devolve estruturas neutras (dict/list), nunca
objetos específicos do driver.

```python
class DatabaseProvider(ABC):
    engine: str                      # "oracle", "postgres", …
    paramstyle: str                  # "named", "qmark", "pyformat" (normalização de binds)

    @abstractmethod
    def connect(self, ref: ConnectionRef, secret: Secret) -> Conn: ...
    @abstractmethod
    def disconnect(self, conn: Conn) -> None: ...
    @abstractmethod
    def execute(self, conn: Conn, sql: str, params, *, max_rows, timeout_ms) -> CursorResult: ...
    @abstractmethod
    def fetchmany(self, cursor: CursorResult, n: int) -> tuple[list[Row], bool]: ...  # (rows, last)
    @abstractmethod
    def cancel(self, conn: Conn) -> None: ...           # OCIBreak / pg_cancel / KILL QUERY
    # transação
    def begin(self, conn): ...
    def commit(self, conn): ...
    def rollback(self, conn): ...
    # metadata — implementação default via SQL do dicionário de dados, override por banco
    def describe(self, conn, table) -> list[Column]: ...
    def list_tables(self, conn, schema=None) -> list[str]: ...
    def list_views(self, conn, schema=None) -> list[str]: ...
    def list_indexes(self, conn, table) -> list[Index]: ...
    def list_pk(self, conn, table) -> list[str]: ...
    def list_fk(self, conn, table) -> list[ForeignKey]: ...
    def explain(self, conn, sql) -> str: ...
    def version(self, conn) -> str: ...
```

**Matriz de drivers** (todos rodam **na máquina cliente**, que já os tem ou pode instalar):

| engine | driver Python | metadata source | cancel |
|---|---|---|---|
| oracle | `oracledb` (thin/thick) | `ALL_TAB_COLUMNS`, `ALL_INDEXES`, `ALL_CONSTRAINTS` | `connection.cancel()` (OCIBreak) |
| postgres | `psycopg` (v3) | `information_schema` / `pg_catalog` | `pg_cancel_backend` |
| sqlserver | `pyodbc` | `INFORMATION_SCHEMA`, `sys.*` | `SqlClient` cancel / nova conexão `KILL` |
| mysql | `mysqlclient`/`PyMySQL` | `information_schema` | `KILL QUERY` |
| sqlite | `sqlite3` (stdlib) | `PRAGMA table_info`, `sqlite_master` | `interrupt()` |

### 6.1 Registry

`ci_host/registry.py`:

```python
class ProviderRegistry:
    _providers: dict[str, type[DatabaseProvider]] = {}
    @classmethod
    def register(cls, provider): cls._providers[provider.engine] = provider
    @classmethod
    def get(cls, engine) -> DatabaseProvider:
        if engine not in cls._providers:
            raise DbError(f"engine sem provider: {engine}")
        return cls._providers[engine]()
```

Adicionar um banco novo = criar `providers/<x>.py`, implementar a ABC, registrar. Zero
mudança no protocolo/gateway/host. Drivers ausentes degradam graciosamente (o provider só é
importado quando uma conexão daquele engine é aberta — import lazy).

---

## 7. Descoberta automática de conexões

`ci_discovery/base.py`:

```python
@dataclass(frozen=True)
class ConnectionRef:
    id: str; engine: str; name: str
    dsn: dict          # host/port/service/file… específico do engine
    source: str        # "tnsnames" | "pg_service" | "odbc-dsn" | "manual" | …
    needs_secret: bool # se o usuário precisa fornecer senha no connect

class ConnectionScanner(ABC):
    engine: str
    @abstractmethod
    def scan(self) -> list[ConnectionRef]: ...
```

| engine | fontes varridas |
|---|---|
| oracle | `$TNS_ADMIN/tnsnames.ora`, `$ORACLE_HOME/network/admin/tnsnames.ora`, `sqlnet.ora`; cada entrada vira um `ConnectionRef` (parse de DSN sem expandir senha) |
| postgres | `~/.pg_service.conf` (cada `[serviço]`), `~/.pgpass` apenas para marcar `needs_secret=false` — **nunca** lê/transmite a senha |
| sqlserver | DSNs ODBC (`pyodbc.dataSources()`) + drivers (`pyodbc.drivers()`) |
| mysql | `~/.my.cnf`, `~/.mylogin.cnf` (ofuscado — só detecta presença), `/etc/mysql/` |
| sqlite | arquivos `.db`/`.sqlite` **cadastrados manualmente** (não varre disco inteiro) |

**Cadastro manual** sempre disponível: seção `conninfo.connections:` no `config.yaml` e via
GUI. Descoberta e cadastro manual se fundem num catálogo único, deduplicado por `id`.

**Credenciais nunca cruzam o canal.** A descoberta só identifica *qual* conexão existe. A
senha é resolvida **no cliente**, no momento do `connect`, a partir do cofre local (`.pgpass`,
Oracle Wallet, `mylogin.cnf`, Windows Credential Manager, ou prompt local). O servidor só
recebe o `session` id. Ver §8/§10.

---

## 8. Gerenciamento de sessões

Vivem **no `ProviderHost`** (lado cliente), porque a conexão DBAPI real mora lá.
`ci_host/session.py`:

```python
@dataclass
class Session:
    id: str                  # token aleatório (secrets.token_hex)
    engine: str
    connection_id: str       # qual ConnectionRef
    user: str                # identidade autenticada do agente (para ACL/auditoria)
    conn: object             # handle DBAPI vivo
    created_at: float
    last_activity: float
    timeout_s: int
    cursors: dict[str, CursorState]   # cursores de streaming abertos
    in_tx: bool
```

* **Reuso:** o `session` id volta ao agente no `CONNECT_OK`; chamadas seguintes o
  referenciam. A conexão DBAPI permanece aberta (pool quente).
* **Timeout / reaping:** uma task periódica fecha sessões inativas (`now - last_activity >
  timeout_s`), faz `rollback` de transação pendente e libera cursores. Toda mensagem
  atualiza `last_activity`.
* **Cancelamento remoto:** `CANCEL(req)` → `SessionManager` localiza a `Task` e chama
  `provider.cancel(conn)` (interrupt do driver) de outra thread; a `Task` termina com
  `ERROR(req,"cancelado")`.
* **Limites por sessão:** `max_rows`, `timeout_ms` por execução; teto global por usuário.

`SessionManager` indexa por `id` e por `user` (para enforcement de cota e desligamento em
massa). Limite de sessões simultâneas por usuário e total.

---

## 9. Streaming e backpressure

Resultados grandes **nunca** vão inteiros. O provider mantém o cursor DBAPI aberto na
sessão; o agente puxa `FETCH(n)` → `RESULT_CHUNK`. A `connInfo API` expõe:

```python
cur = conn.execute("select … from grande")
for row in cur:            # puxa chunks sob demanda (default fetch size, ex. 100/500)
    ...
rows = cur.fetchmany(200)  # explícito
cur.close()                # libera o cursor no host (CLOSE_CURSOR)
```

* **Backpressure:** como o agente só envia `FETCH` quando consome, o host não acumula
  resultado em memória além de um chunk + o buffer do driver. Opção de **prefetch** (pedir o
  próximo chunk enquanto processa o atual) configurável, com janela limitada (ex. 1 chunk
  adiantado) para não estourar memória.
* **Serialização:** JSON no MVP; **msgpack opcional** no Beta para linhas (menos overhead de
  parsing/tamanho). Tipos não-JSON (datas, `Decimal`, `bytes`, `LOB`) são normalizados para
  `{"$type":"datetime","v":"…"}` etc., para round-trip determinístico.
* **Teto de frame:** `MAX_FRAME_SIZE` (32 MB do core) limita o chunk; o fetch size é
  ajustado para caber. LOBs grandes podem ser chunked como no `FS_WRITE_CHUNK` do foshar.

---

## 10. Segurança

Camadas, da rede para o dado:

1. **Transporte:** reusa `zephyrlink.transport.security` — **HMAC desafio-resposta**
   (chave nunca trafega), **TLS opcional**, **allowlist de host** na aceitação. Mesma chave
   compartilhada do MouseBridge (seção `security:` do `config.yaml`).
2. **Autenticação do agente:** além do HMAC do canal, cada agente tem uma **identidade**
   (`user`) provada no handshake (token por agente). Vai para a ACL e a auditoria.
3. **Whitelist de conexões + permissões por usuário** (`ci_security/acl.py`): o catálogo
   devolvido no `AUTH_OK` é **filtrado** pelo que aquele `user` pode usar. `connect` a uma
   conexão fora da whitelist é recusado. Permissões granulares: `read-only`,
   `allow_write` (DML), `allow_ddl`, schemas permitidos.
4. **Credenciais:** **nenhuma senha em texto puro no fio.** Senha resolvida no cliente, do
   cofre local. O servidor só manuseia `session` ids. (Discutir: o agente pode precisar
   *fornecer* uma senha para conexões `needs_secret` sem cofre — nesse caso ela trafega
   **apenas sob TLS**, é usada uma vez para abrir a sessão e **não** é persistida no host.)
5. **Limites** (`ci_security/limits.py`): `max_rows` por consulta, `timeout_ms` por consulta,
   `rate_limit_per_min` por usuário (espelha `rate_limit_per_min` do foshar), nº máximo de
   sessões. Estouro → `ERROR` claro, não derruba a conexão.
6. **Cancelamento remoto** (§8) como controle de segurança contra runaway queries.
7. **Auditoria completa** (`ci_security/audit.py`, espelha `launcher/audit.py`):
   append-only JSON-lines, opt-in via `audit_file`. Registra: connect/disconnect, cada SQL
   (com hash + texto truncado), nº de linhas, duração, cancelamentos, recusas de ACL, erros.
   Falha de escrita nunca bloqueia a operação.

**Não-objetivo:** a connInfo **não** tenta impedir SQL "perigoso" por parsing — o agente
roda SQL intencionalmente. A contenção é por **ACL + permissões + limites + auditoria**, não
por blocklist de palavras. (Opcional futuro: modo read-only que rejeita não-`SELECT` via
flag do driver / transação read-only, mais robusto que regex.)

---

## 11. A connInfo API (lib do lado agente)

`ci_gateway/api.py` — fachada in-process que o agente importa. PEP 249-like, mas mínima:

```python
import conninfo

conn = conninfo.connect("oracle_prd")          # → CONNECT, devolve Connection
rows = conn.execute("select owner, table_name from dba_tables where owner='HUMASTER'")
cols = conn.describe("HUMASTER.CLIENTE")
idx  = conn.list_indexes("HUMASTER.CLIENTE")
plan = conn.explain("select * from humaster.cliente where id=:1", [42])
conn.close()
```

`connect()` abre (ou reusa) a conexão TCP com o `ProviderHost`, faz o handshake e devolve um
`Connection`. `Connection.execute()` envia `EXECUTE`, recebe `RESULT_META` e devolve um
`Cursor` iterável (streaming, §9). **A mesma API funciona para qualquer engine** — é o que o
OpenClaw (Oracle Advisor, PostgreSQL Advisor, Incident Analyzer, Inventário…) consome de
forma uniforme. Onde o agente fala assíncrono, há também `AsyncConnection`; o `api.py`
síncrono é um wrapper sobre o cliente assíncrono multiplexado.

---

## 12. Modelo de concorrência

* **Lado Host (cliente/banco):** um event loop asyncio aceita conexões e serve o laço de
  despacho (igual ao `_serve_loop` do foshar), **mas** cada operação de banco roda num
  `ThreadPoolExecutor` (`ci_host/executor.py`) porque os drivers DBAPI são bloqueantes.
  Tamanho do pool ≈ teto de consultas concorrentes; sessões além disso enfileiram. O loop
  nunca bloqueia, então `CANCEL`/controle sempre é atendido.
* **Lado Gateway (agente/servidor):** cliente assíncrono multiplexado (`mux.py`): envia com
  `req_id`, resolve `Future` na resposta. A API síncrona usa `asyncio.run`/loop dedicado por
  thread.
* **Escala ("milhares de execuções"):** não é milhares *simultâneas* num cliente — é
  throughput. O gargalo real é o **banco** e o **número de conexões DBAPI** que o cliente
  aguenta. Tratamos com: pool de sessões reusáveis, limite de concorrência por usuário,
  enfileiramento justo, e (Beta+) **múltiplos `ProviderHost`** atrás de um diretório
  (sharding por conexão/engine). Ver §14.

---

## 13. Configuração (exemplo)

```yaml
# reusada do MouseBridge — mesma chave para todas as skills
security:
  shared_key: "troque-me"
  use_tls: true
  tls_cert: cert.pem
  tls_key: key.pem
  allowed_hosts: ["192.168.1.*"]

conninfo:
  enabled: true
  port: 50514                     # conexão dedicada, ≠ input/foshar
  audit_file: ~/.conninfo/audit.jsonl
  session_timeout_s: 1800
  max_rows_default: 10000
  rate_limit_per_min: 600
  pool_size: 16                   # threads de execução de banco
  discovery: ["oracle","postgres","sqlserver","mysql"]   # quais scanners rodar
  connections:                    # cadastro manual (mescla com a descoberta)
    - id: oracle_prod
      engine: oracle
      name: "Oracle Produção"
      dsn: { host: db, port: 1521, service: PRD }
    - id: vendas_lite
      engine: sqlite
      name: "Vendas (SQLite)"
      dsn: { file: ~/dados/vendas.db }
  acl:                            # permissões por usuário do agente
    - user: oracle-advisor
      connections: ["oracle_prod"]
      mode: read-only
```

---

## 14. Plano de implementação por fases

### MVP (M1) — provar o conceito ponta a ponta
* `ci_protocol` (messages/channel/handshake) reusando o transporte do core.
* `ProviderHost` + `Gateway` com **multiplexação mínima** e `req_id`.
* Providers **SQLite** (stdlib, zero dependência, testável em CI) e **PostgreSQL**.
* `connect / execute / fetch (streaming) / disconnect` + `version`.
* Descoberta de PostgreSQL (`pg_service.conf`) + cadastro manual.
* Segurança: HMAC + allowlist + TLS (já prontos no core) + `max_rows` + auditoria.
* CLI: `python -m conninfo host` (lado banco) e um `shell` de teste (lado agente).
* Testes: SQLite ponta a ponta sem rede real (mesma abordagem dos testes do foshar).

**Critério de pronto:** o exemplo do pedido roda contra SQLite/Postgres sem cliente no
servidor.

### Beta (M2) — cobertura e robustez
* Providers **Oracle**, **SQL Server**, **MySQL** + descoberta de cada um (tnsnames, ODBC
  DSN, my.cnf).
* Metadata completa: `describe / list_tables / list_views / list_indexes / list_pk /
  list_fk / explain`.
* Transações: `begin / commit / rollback`.
* **Cancelamento remoto** real (per-driver).
* ACL por usuário + permissões (read-only/DML/DDL/schema).
* Streaming refinado: prefetch com janela, normalização de tipos, msgpack opcional.
* GUI de cadastro de conexões (espelha `foshar/gui`).
* `rate_limit_per_min` + cotas por usuário.

### Produção (M3) — escala e operação
* Pool de sessões/conexões com health-check e reaping robusto.
* Métricas/observabilidade (consultas/seg, latência, erros, sessões ativas).
* Múltiplos `ProviderHost` + diretório/roteamento por conexão (sharding/HA).
* Compressão de chunk, diff/colunar opcional para resultados grandes.
* Rotação de credenciais / integração com cofres (Vault, Windows Credential Manager).
* Hardening: fuzzing do protocolo, testes de carga, modo read-only forte por transação.

---

## 15. Análise crítica — gargalos, riscos e oportunidades

### Gargalos
1. **Drivers DBAPI são bloqueantes.** Sem o `ThreadPoolExecutor` (§12) uma consulta longa
   congela todas as sessões. É a decisão arquitetural mais importante e o que mais diverge do
   foshar (que é sequencial). Custo: o pool limita a concorrência real por cliente.
2. **Sequencialidade do padrão foshar não escala.** Reusar `request()` 1-a-1 inviabilizaria
   streaming + cancelamento. A multiplexação (`mux.py`) é obrigatória desde o MVP — é dívida
   cara se deixada para depois.
3. **Memória de resultados grandes.** Mitigado por streaming + backpressure + teto de frame,
   mas LOBs/BLOBs gigantes exigem chunking explícito (reusar a ideia de `FS_WRITE_CHUNK`).
4. **Serialização JSON** tem custo de CPU/tamanho para milhões de linhas — msgpack/compressão
   no Beta. JSON não representa nativamente `Decimal`/`datetime`/`bytes`: precisa do envelope
   tipado, ou há perda silenciosa de precisão (risco em dados financeiros).
5. **Um único `ProviderHost`** é ponto único de saturação (CPU do cliente, nº de conexões ao
   banco). "Milhares de execuções" é throughput agregado, não simultaneidade num cliente —
   resolver com cota + (M3) múltiplos hosts.

### Riscos
1. **Inversão de papéis (§2)** é a maior fonte provável de bug/confusão de implementação.
   Mitigação: nomes `ProviderHost`/`Gateway`, nunca "server/client" cru, e um teste de
   fumaça que valida a direção.
2. **Credenciais.** O caminho feliz (cofre local, senha nunca trafega) é seguro; o caminho
   "agente fornece senha" só é aceitável **sob TLS** e sem persistir. Sem TLS, HMAC autentica
   mas **não cifra** — senha/resultados trafegam em claro na LAN. **Exigir TLS quando houver
   `needs_secret` ou dados sensíveis.**
3. **Sessões órfãs / vazamento de conexões.** Agente cai sem `disconnect` → conexão DBAPI
   presa. Mitigação: reaping por timeout + fechar todas as sessões de uma conexão TCP que
   caiu + limite por usuário.
4. **ACL fraca = gateway vira backdoor.** Quem controla o agente roda SQL arbitrário nos
   bancos do cliente. A ACL + read-only + auditoria são a única contenção; precisam estar no
   MVP (ao menos whitelist + read-only), não adiadas.
5. **Disponibilidade de drivers no cliente.** Oracle thick precisa de Instant Client; ODBC
   precisa de driver instalado. Import lazy + erro claro ("driver X ausente") evita derrubar
   o host inteiro por um engine indisponível.
6. **Cancelamento é melhor-esforço.** `OCIBreak`/`pg_cancel`/`KILL` nem sempre interrompem na
   hora; o `timeout_ms` server-side é a rede de segurança.
7. **Injeção via metadata.** `describe("HUMASTER.CLIENTE")` que monta SQL com o nome da tabela
   por concatenação é injetável. Usar bind/`information_schema` parametrizado e validar
   identificadores.

### Oportunidades
1. **Camada universal real:** a `connInfo API` uniforme (§11) é exatamente o que o OpenClaw
   precisa — vale formalizar como contrato estável (versionado) cedo, para os Advisors
   dependerem dela sem acoplamento ao engine.
2. **Cache de metadata** (describe/list_*) no Gateway: muda pouco, é muito consultado por
   agentes de inventário — economiza ida ao banco.
3. **Read-only forte por transação** (Postgres `SET TRANSACTION READ ONLY`, Oracle
   `read only`) é mais seguro que blocklist e barato de adicionar.
4. **Plano de query estruturado** (`explain` → JSON) habilita Advisors sem cada um parsear
   texto.
5. **Reuso máximo do foshar:** framing, handshake, allowlist, padrão de auditoria, estrutura
   de config e CLI já existem e são testados — a connInfo é "foshar para SQL" no transporte,
   divergindo só na multiplexação e nos providers. Acelera o MVP e reduz superfície de bug.
6. **Test harness sem rede** (SQLite + sockets locais) como no foshar mantém CI rápido e
   determinístico.

### Recomendações antes de codar
* **Confirmar a direção da conexão** (§2) e fixá-la em teste.
* **Multiplexação no MVP**, não depois.
* **TLS obrigatório** sempre que houver credencial/dado sensível.
* **ACL + read-only + auditoria mínimas no MVP**, não só no Beta.
* **Começar por SQLite + PostgreSQL** (sem dependências exóticas) para validar ponta a ponta
  antes de Oracle/ODBC.

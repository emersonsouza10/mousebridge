# SPEC_Conninfo.md — MouseBridge Skill `connInfo`

## 1. Visão Geral

A skill `connInfo` tem como objetivo permitir que o **MouseBridge Server** utilize o **host cliente** como ponto real de conexão com bancos de dados.

O servidor não deve se conectar diretamente aos bancos.  
Toda conexão real deve ocorrer no cliente, aproveitando o ambiente local já funcional, como:

- VPN;
- rotas de rede;
- Oracle Client;
- drivers ODBC;
- certificados;
- `tnsnames.ora`;
- `pg_service.conf`;
- credenciais locais;
- ferramentas como DBeaver, SQL Developer, `psql`, `sqlcmd`, MySQL Client e similares.

A skill `connInfo` funcionará como uma camada de **execução remota controlada**, onde o servidor solicita ações e o cliente executa localmente, retornando os resultados pelo canal seguro do MouseBridge.

---

## 2. Problema

Em muitos ambientes, o servidor onde os agentes rodam não possui acesso direto aos bancos de dados.

Isso pode acontecer por diversos motivos:

- o banco só é acessível via VPN instalada no computador cliente;
- o cliente possui rotas específicas que o servidor não possui;
- o cliente possui Oracle Client, ODBC ou drivers instalados;
- o cliente possui certificados necessários para conexão;
- o cliente já possui configurações locais funcionais;
- o servidor não deve receber drivers ou clientes de banco por questões de segurança e manutenção.

Hoje, para que um agente no servidor analise um banco, seria necessário instalar drivers, configurar rede e replicar credenciais no servidor.

A proposta da `connInfo` evita isso.

O servidor apenas orquestra.  
O cliente executa a conexão.

---

## 3. Objetivo

Criar uma skill chamada `connInfo` no projeto MouseBridge para permitir que agentes no servidor acessem bancos de dados disponíveis apenas a partir do host cliente.

A skill deve permitir:

- descobrir conexões disponíveis no cliente;
- validar drivers instalados;
- listar conexões possíveis;
- abrir sessões de banco;
- executar SQL;
- retornar resultados;
- consultar metadata;
- controlar transações;
- limitar execução;
- auditar comandos;
- transmitir resultados em lotes;
- encerrar sessões;
- proteger credenciais;
- bloquear comandos perigosos por padrão.

---

## 4. Princípio Arquitetural

O princípio central da `connInfo` é:

> O servidor nunca conecta diretamente no banco.  
> Quem conecta no banco é sempre o host cliente.

Fluxo esperado:

```text
Agente / Server
    |
    | solicita conexão ou execução SQL
    v
MouseBridge Server
    |
    | mensagem segura pelo canal MouseBridge
    v
MouseBridge Client
    |
    | executa usando ambiente local do cliente
    v
Banco de Dados
```

O servidor atua como orquestrador.  
O cliente atua como executor de banco.

---

## 5. Nome da Skill

Nome oficial:

```text
connInfo
```

Diretório sugerido:

```text
mousebridge/skills/conninfo/
```

Arquivo de especificação:

```text
SPEC_Conninfo.md
```

---

## 6. Escopo

A skill `connInfo` deve cobrir inicialmente:

- Oracle;
- PostgreSQL;
- SQL Server;
- MySQL;
- SQLite.

A arquitetura deve ser extensível para suportar novos bancos no futuro.

---

## 7. Fora de Escopo Inicial

O MVP não deve tentar resolver todos os cenários avançados.

Fora do escopo inicial:

- interface gráfica para gestão de conexões;
- cofre corporativo completo de senhas;
- execução massiva de queries analíticas longas;
- replicação de dados;
- túnel TCP genérico;
- substituição de ferramentas como DBeaver ou SQL Developer;
- suporte completo a todos os recursos específicos de cada banco;
- execução DDL/DML liberada por padrão.

A primeira versão deve focar em validação do conceito com segurança.

---

## 8. Casos de Uso

### 8.1 Listar conexões disponíveis no cliente

O servidor solicita ao cliente quais conexões estão disponíveis.

Exemplo:

```json
{
  "skill": "connInfo",
  "action": "list_connections",
  "request_id": "req-001"
}
```

Resposta:

```json
{
  "request_id": "req-001",
  "status": "success",
  "payload": {
    "connections": [
      {
        "id": "oracle_homolog",
        "engine": "oracle",
        "alias": "HOMOL",
        "source": "tnsnames.ora",
        "host_visible_from": "client"
      }
    ]
  }
}
```

---

### 8.2 Abrir sessão

O servidor solicita abertura de sessão em uma conexão conhecida.

```json
{
  "skill": "connInfo",
  "action": "connect",
  "request_id": "req-002",
  "payload": {
    "connection_id": "oracle_homolog"
  }
}
```

Resposta:

```json
{
  "request_id": "req-002",
  "status": "success",
  "payload": {
    "session_id": "sess-abc123",
    "engine": "oracle",
    "connection_id": "oracle_homolog",
    "read_only": true
  }
}
```

---

### 8.3 Executar SQL

```json
{
  "skill": "connInfo",
  "action": "execute",
  "request_id": "req-003",
  "session_id": "sess-abc123",
  "payload": {
    "sql": "select * from dual",
    "params": {},
    "options": {
      "max_rows": 100,
      "timeout_seconds": 30,
      "read_only": true
    }
  }
}
```

Resposta:

```json
{
  "request_id": "req-003",
  "status": "success",
  "payload": {
    "columns": ["DUMMY"],
    "rows": [["X"]],
    "row_count": 1,
    "has_more": false
  }
}
```

---

### 8.4 Buscar próximo lote de linhas

```json
{
  "skill": "connInfo",
  "action": "fetch_next",
  "request_id": "req-004",
  "payload": {
    "cursor_id": "cur-001",
    "fetch_size": 500
  }
}
```

Resposta:

```json
{
  "request_id": "req-004",
  "status": "success",
  "payload": {
    "columns": ["OWNER", "TABLE_NAME"],
    "rows": [
      ["HUMASTER", "CLIENTE"],
      ["HUMASTER", "PEDIDO"]
    ],
    "row_count": 2,
    "has_more": true,
    "cursor_id": "cur-001"
  }
}
```

---

### 8.5 Encerrar sessão

```json
{
  "skill": "connInfo",
  "action": "disconnect",
  "request_id": "req-005",
  "session_id": "sess-abc123"
}
```

Resposta:

```json
{
  "request_id": "req-005",
  "status": "success",
  "payload": {
    "disconnected": true
  }
}
```

---

## 9. Requisitos Funcionais

### RF01 — Descobrir conexões

A skill deve descobrir conexões disponíveis no cliente.

Fontes possíveis:

- arquivos locais;
- variáveis de ambiente;
- DSNs ODBC;
- configurações manuais;
- ferramentas instaladas;
- drivers Python disponíveis.

---

### RF02 — Listar conexões

A skill deve retornar uma lista padronizada de conexões disponíveis.

Cada conexão deve conter pelo menos:

```text
id
engine
name
source
host_visible_from
read_only
```

---

### RF03 — Abrir conexão

A skill deve permitir abrir uma sessão usando uma conexão conhecida.

A conexão deve ser criada no cliente.

---

### RF04 — Executar SQL

A skill deve permitir executar SQL através da sessão aberta.

Por padrão, apenas comandos de leitura devem ser permitidos.

---

### RF05 — Retornar resultado em JSON

Os resultados devem ser retornados em formato JSON padronizado:

```text
columns
rows
row_count
has_more
cursor_id
```

---

### RF06 — Streaming / paginação

A skill não deve enviar grandes volumes em uma única resposta.

Deve suportar:

- `fetchmany`;
- `fetch_size`;
- `cursor_id`;
- `has_more`;
- `close_cursor`.

---

### RF07 — Metadata

A skill deve oferecer métodos padronizados para consultar metadata:

- schemas;
- tabelas;
- views;
- índices;
- constraints;
- primary keys;
- foreign keys;
- versão do banco;
- plano de execução quando suportado.

---

### RF08 — Transações

A skill deve suportar:

- `begin`;
- `commit`;
- `rollback`.

No modo somente leitura, transações de escrita devem ser bloqueadas.

---

### RF09 — Cancelamento remoto

A skill deve permitir cancelar uma execução em andamento.

---

### RF10 — Encerrar sessão

A skill deve permitir encerrar sessões manualmente.

Também deve expirar sessões inativas automaticamente.

---

## 10. Requisitos Não Funcionais

### RNF01 — Segurança por padrão

A configuração padrão deve ser restritiva.

A skill deve iniciar em modo somente leitura.

---

### RNF02 — Baixo acoplamento

A lógica de banco não deve ficar misturada com a lógica do MouseBridge.

A skill deve ser plugável e modular.

---

### RNF03 — Extensibilidade

Novos bancos devem poder ser adicionados criando novos providers.

---

### RNF04 — Observabilidade

A skill deve gerar logs estruturados e auditáveis.

---

### RNF05 — Controle de recursos

A skill deve limitar:

- tempo de execução;
- número de linhas;
- tamanho do payload;
- quantidade de sessões;
- quantidade de cursores abertos.

---

### RNF06 — Compatibilidade

A skill deve funcionar em clientes Windows inicialmente, mas a arquitetura deve permitir Linux e macOS futuramente.

---

## 11. Providers

A arquitetura deve ser baseada em providers.

Providers iniciais:

```text
OracleProvider
PostgresProvider
SqlServerProvider
MySQLProvider
SQLiteProvider
```

Todos devem implementar uma interface comum.

---

## 12. Interface Base dos Providers

Interface sugerida:

```python
class DatabaseProvider:
    def discover_connections(self): ...
    def connect(self, connection_id, credentials=None): ...
    def disconnect(self, session_id): ...
    def execute(self, session_id, sql, params=None, options=None): ...
    def fetch_next(self, cursor_id): ...
    def close_cursor(self, cursor_id): ...
    def cancel(self, session_id): ...
    def begin(self, session_id): ...
    def commit(self, session_id): ...
    def rollback(self, session_id): ...
    def describe_table(self, session_id, object_name): ...
    def list_schemas(self, session_id): ...
    def list_tables(self, session_id, schema=None): ...
    def list_views(self, session_id, schema=None): ...
    def list_indexes(self, session_id, table_name): ...
    def list_constraints(self, session_id, table_name): ...
    def list_pk(self, session_id, table_name): ...
    def list_fk(self, session_id, table_name): ...
    def explain(self, session_id, sql): ...
    def version(self, session_id): ...
```

A ideia é que o agente não precise conhecer o banco.  
Ele chama métodos padronizados, e o provider resolve a execução específica.

---

## 13. Descoberta Automática de Conexões

### 13.1 Oracle

Validar e localizar:

```text
TNS_ADMIN
ORACLE_HOME
tnsnames.ora
sqlnet.ora
instantclient
sqlplus
python-oracledb
cx_Oracle
```

A skill deve listar aliases disponíveis no `tnsnames.ora`.

Exemplo:

```json
{
  "id": "oracle_homolog",
  "engine": "oracle",
  "alias": "HOMOL",
  "source": "tnsnames.ora",
  "host_visible_from": "client"
}
```

---

### 13.2 PostgreSQL

Validar:

```text
pg_service.conf
.pgpass
psql
psycopg
psycopg2
```

---

### 13.3 SQL Server

Validar:

```text
ODBC Data Sources
ODBC Drivers
sqlcmd
pyodbc
trusted authentication
```

---

### 13.4 MySQL

Validar:

```text
mysql client
PyMySQL
mysql-connector-python
arquivos de configuração conhecidos
```

---

### 13.5 SQLite

Validar:

```text
arquivos .db
arquivos .sqlite
paths cadastrados manualmente
```

---

## 14. Cadastro Manual de Conexões

Além da descoberta automática, a skill deve permitir cadastro manual em arquivo local do cliente.

Exemplo:

```yaml
connections:
  oracle_homolog:
    engine: oracle
    mode: tns
    alias: HOMOL
    username_source: prompt
    password_source: secure_store
    read_only: true
    max_rows: 1000
    timeout_seconds: 60

  postgres_dev:
    engine: postgres
    host: 10.10.1.20
    port: 5432
    database: appdb
    username_source: prompt
    password_source: secure_store
    read_only: true
    max_rows: 1000
    timeout_seconds: 60
```

Esse arquivo deve ficar no cliente, não no servidor.

Local sugerido no Windows:

```text
%APPDATA%\MouseBridge\conninfo\connections.yaml
```

Local sugerido no Linux:

```text
~/.config/mousebridge/conninfo/connections.yaml
```

---

## 15. Políticas de Segurança

A skill deve permitir políticas por conexão.

Exemplo:

```yaml
policies:
  oracle_homolog:
    read_only: true
    allowed_schemas:
      - HUMASTER
      - ADM
    blocked_keywords:
      - DROP
      - TRUNCATE
      - DELETE
    max_rows: 1000
    timeout_seconds: 60

  postgres_dev:
    read_only: false
    require_confirmation_for_write: true
    max_rows: 5000
    timeout_seconds: 60
```

---

## 16. Modo Somente Leitura

O modo padrão deve ser `read_only: true`.

Comandos perigosos devem ser bloqueados por padrão:

```sql
DROP
TRUNCATE
DELETE
UPDATE
INSERT
ALTER
CREATE
GRANT
REVOKE
MERGE
```

Esses comandos só devem ser permitidos quando:

- a conexão estiver explicitamente liberada para escrita;
- a política permitir;
- a execução for auditada;
- opcionalmente houver confirmação manual.

---

## 17. Modelo de Sessões

A skill deve manter sessões no cliente.

Cada sessão deve conter:

```text
session_id
connection_id
engine
username
created_at
last_activity_at
timeout_seconds
read_only
status
active_cursor
transaction_state
```

Estados possíveis:

```text
OPEN
IDLE
EXECUTING
ERROR
CLOSED
EXPIRED
```

Sessões inativas devem expirar automaticamente.

---

## 18. Modelo de Cursores

Para streaming, a skill deve controlar cursores remotos no cliente.

Cada cursor deve conter:

```text
cursor_id
session_id
created_at
last_fetch_at
fetch_size
rows_sent
has_more
status
```

Estados possíveis:

```text
OPEN
EXHAUSTED
CLOSED
ERROR
EXPIRED
```

---

## 19. Modelo de Comunicação

Toda comunicação deve usar mensagens JSON trafegadas pelo canal do MouseBridge.

Envelope padrão:

```json
{
  "skill": "connInfo",
  "action": "execute",
  "request_id": "req-001",
  "session_id": "sess-abc",
  "payload": {}
}
```

Resposta de sucesso:

```json
{
  "request_id": "req-001",
  "status": "success",
  "payload": {}
}
```

Resposta de erro:

```json
{
  "request_id": "req-001",
  "status": "error",
  "error": {
    "code": "DB_EXECUTION_ERROR",
    "message": "Mensagem segura para exibição",
    "safe_detail": "Detalhe sanitizado"
  }
}
```

---

## 20. Tratamento de Erros

Erros devem ser normalizados.

Códigos sugeridos:

```text
CONNECTION_NOT_FOUND
CONNECTION_NOT_ALLOWED
DRIVER_NOT_FOUND
AUTHENTICATION_FAILED
SESSION_NOT_FOUND
SESSION_EXPIRED
SQL_BLOCKED_BY_POLICY
DB_EXECUTION_ERROR
QUERY_TIMEOUT
MAX_ROWS_EXCEEDED
CURSOR_NOT_FOUND
CANCEL_FAILED
INTERNAL_ERROR
```

Nenhum erro deve vazar senha, string de conexão completa ou informação sensível.

---

## 21. Auditoria

Toda execução deve gerar log estruturado.

Campos mínimos:

```text
timestamp
request_id
client_host
server_id
connection_id
engine
database_user
action
sql_hash
sql_preview
duration_ms
row_count
status
error_code
read_only
```

A SQL completa deve ser opcional, pois pode conter dados sensíveis.

Recomendação:

- armazenar `sql_hash`;
- armazenar apenas preview sanitizado;
- nunca registrar senha;
- nunca registrar token;
- nunca registrar connection string completa com credenciais.

---

## 22. Segurança Obrigatória

A skill deve implementar:

- autenticação entre server e client;
- criptografia do canal;
- allowlist de conexões;
- bloqueio de conexões não autorizadas;
- modo read-only por padrão;
- limite de linhas;
- timeout por consulta;
- timeout por sessão;
- auditoria local no cliente;
- auditoria no server;
- bloqueio opcional de DDL/DML;
- mascaramento de senhas;
- nenhuma senha em log;
- nenhuma senha em texto puro no transporte;
- cancelamento remoto de consulta;
- limitação de payload.

---

## 23. Estrutura Sugerida do Projeto

```text
mousebridge/
  skills/
    conninfo/
      __init__.py
      README.md
      SPEC_Conninfo.md
      protocol.py
      models.py
      client_handler.py
      server_api.py
      registry.py
      discovery.py
      sessions.py
      security.py
      audit.py
      policies.py
      streaming.py

      providers/
        __init__.py
        base.py
        oracle.py
        postgres.py
        sqlserver.py
        mysql.py
        sqlite.py

      tests/
        test_protocol.py
        test_sessions.py
        test_security.py
        test_oracle_provider.py
        test_postgres_provider.py
        test_sqlserver_provider.py
        test_sqlite_provider.py
```

---

## 24. MVP

O primeiro MVP deve validar o conceito com segurança e simplicidade.

### 24.1 Itens do MVP

O MVP deve incluir:

1. Estrutura da skill `connInfo`.
2. Protocolo básico:
   - `list_connections`;
   - `connect`;
   - `execute`;
   - `disconnect`.
3. `SQLiteProvider` para testes locais.
4. `OracleProvider` usando `python-oracledb`.
5. Sessão em memória no cliente.
6. Limite de linhas.
7. Timeout básico.
8. Bloqueio read-only simples.
9. Auditoria local.
10. Exemplos de uso.

---

### 24.2 Fluxo do MVP

```text
Server pede execução
Client recebe requisição
Client conecta no banco
Client executa SQL
Client devolve resultado
Server exibe resultado
```

---

### 24.3 Critérios de Aceite do MVP

O MVP será considerado pronto quando:

- o server conseguir listar conexões disponíveis no cliente;
- o server conseguir abrir uma sessão via cliente;
- o cliente conseguir executar uma query simples no banco;
- o resultado voltar para o server em JSON;
- a sessão puder ser encerrada;
- queries grandes forem limitadas;
- DML/DDL forem bloqueados por padrão;
- logs de auditoria forem gerados;
- nenhuma senha aparecer em log;
- o servidor não precisar de Oracle Client, ODBC, `psql` ou qualquer driver de banco;
- a conexão real ocorrer somente no host cliente.

---

## 25. Fases de Implementação

### Fase 1 — Discovery e Arquitetura

Analisar o projeto MouseBridge atual e identificar:

- como skills são carregadas;
- como mensagens são roteadas;
- como server e client se comunicam;
- como autenticação funciona hoje;
- onde encaixar a `connInfo`;
- quais padrões existentes devem ser respeitados.

---

### Fase 2 — Protocolo

Definir o contrato JSON da skill:

- `list_connections`;
- `connect`;
- `execute`;
- `fetch_next`;
- `cancel`;
- `commit`;
- `rollback`;
- `disconnect`;
- `describe_table`;
- `list_tables`;
- `explain`.

---

### Fase 3 — Providers

Criar interface base e providers iniciais:

- `SQLiteProvider` para teste;
- `OracleProvider` para uso real inicial;
- `PostgresProvider` depois;
- `SqlServerProvider` depois;
- `MySQLProvider` depois.

---

### Fase 4 — Segurança

Implementar:

- read-only default;
- allowlist;
- timeout;
- max rows;
- bloqueio DDL/DML;
- auditoria;
- mascaramento de credenciais.

---

### Fase 5 — Streaming

Implementar cursores remotos:

- `cursor_id`;
- `fetch_size`;
- `has_more`;
- `close_cursor`.

---

### Fase 6 — Integração com Agentes

Expor API para uso por agentes do OpenClaw:

```python
connInfo.list_connections()
connInfo.connect()
connInfo.execute()
connInfo.describe_table()
connInfo.explain()
```

---

### Fase 7 — Hardening

Adicionar:

- testes automatizados;
- tratamento de falhas;
- reconexão;
- cancelamento de query;
- logs estruturados;
- configuração por YAML;
- documentação.

---

## 26. Riscos e Mitigações

### 26.1 Vazamento de credenciais

Risco: senhas aparecerem em log, payload ou erro.

Mitigação:

- mascarar credenciais;
- nunca registrar senha;
- nunca trafegar senha em texto puro;
- usar secure store local;
- registrar apenas hashes e previews sanitizados.

---

### 26.2 Execução de SQL destrutivo

Risco: agentes executarem DDL/DML indevido.

Mitigação:

- read-only por padrão;
- bloqueio de comandos perigosos;
- allowlist por conexão;
- confirmação manual para escrita;
- auditoria obrigatória.

---

### 26.3 Query pesada travar banco

Risco: execução de consultas sem filtro ou de alto custo.

Mitigação:

- timeout;
- max rows;
- cancelamento remoto;
- explain opcional antes da execução;
- limites por conexão.

---

### 26.4 Transferência excessiva de dados

Risco: resultado grande saturar o canal MouseBridge.

Mitigação:

- streaming com `fetchmany`;
- limite de linhas;
- limite de payload;
- paginação;
- fechamento automático de cursores.

---

### 26.5 Sessão aberta indefinidamente

Risco: sessões inativas consumirem recursos no banco.

Mitigação:

- timeout de sessão;
- cleanup periódico;
- limite de sessões por conexão;
- disconnect explícito.

---

### 26.6 Diferença entre providers

Risco: cada banco ter comportamento diferente.

Mitigação:

- interface base comum;
- normalização de resposta;
- adapters específicos por banco;
- testes por provider.

---

### 26.7 Deadlock na comunicação

Risco: requisição ficar presa entre server e client.

Mitigação:

- timeout no protocolo;
- request_id obrigatório;
- status de execução;
- cancelamento remoto;
- logs correlacionados.

---

### 26.8 Exposição indireta indevida

Risco: o servidor ganhar acesso indireto a bancos sensíveis via cliente.

Mitigação:

- allowlist de conexões;
- políticas por conexão;
- autenticação forte entre server e client;
- aprovação explícita de conexões;
- logs de auditoria.

---

### 26.9 Logs contendo dados sensíveis

Risco: SQL ou erros conterem dados sensíveis.

Mitigação:

- sql_preview limitado;
- sql_hash;
- sanitização de mensagens;
- configuração para desligar SQL completa em logs.

---

## 27. Critérios de Qualidade

A implementação deve prezar por:

- modularidade;
- baixo acoplamento;
- segurança por padrão;
- logs auditáveis;
- facilidade de adicionar novos providers;
- testes unitários;
- clareza no protocolo;
- compatibilidade com o padrão atual do MouseBridge;
- preparação para integração com OpenClaw.

---

## 28. Integração com OpenClaw

A `connInfo` deve futuramente permitir que agentes do OpenClaw usem bancos de forma padronizada.

Agentes beneficiados:

- Oracle Advisor;
- PostgreSQL Advisor;
- SQL Server Advisor;
- Analise DB;
- Incident Analyzer;
- Inventário;
- agentes de troubleshooting.

Exemplo conceitual:

```python
conn = connInfo.connect("oracle_homolog")

tables = conn.list_tables(schema="HUMASTER")

indexes = conn.list_indexes("HUMASTER.CLIENTE")

plan = conn.explain("""
select *
from humaster.cliente
where id_cliente = :id
""")
```

---

## 29. Próximos Passos

1. Validar a arquitetura atual do MouseBridge.
2. Identificar o padrão de skills existente.
3. Criar diretório `mousebridge/skills/conninfo`.
4. Criar modelos de protocolo.
5. Criar `SQLiteProvider`.
6. Criar `OracleProvider`.
7. Implementar sessão em memória.
8. Implementar bloqueio read-only.
9. Implementar auditoria local.
10. Testar fluxo server → client → banco.
11. Documentar uso.
12. Evoluir para streaming e providers adicionais.

---

## 30. Prompt de Implementação para Claude/Kimi

Usar este trecho para orientar a implementação:

```text
Analise o projeto MouseBridge atual e implemente a skill connInfo respeitando esta SPEC.

Antes de escrever código, identifique:
- como as skills são carregadas;
- como as mensagens são roteadas;
- como o server envia comandos ao client;
- como o client responde ao server;
- como autenticação e criptografia funcionam hoje;
- onde a skill connInfo deve ser encaixada sem quebrar o padrão atual.

Depois implemente o MVP:
- estrutura da skill;
- protocolo list_connections/connect/execute/disconnect;
- SQLiteProvider para teste;
- OracleProvider usando python-oracledb;
- sessões em memória no cliente;
- bloqueio read-only;
- limite de linhas;
- timeout básico;
- auditoria local;
- testes básicos.

Não instale lógica de banco no server.
Toda conexão real com banco deve ocorrer no host client.
O server apenas orquestra.
```

---

## 31. Decisão Arquitetural Principal

A decisão mais importante desta SPEC é:

```text
connInfo não é um túnel genérico de rede.
connInfo não transforma o server em database client.
connInfo é uma skill de execução remota controlada,
onde o client executa as operações de banco localmente
e o server apenas solicita, controla e recebe os resultados.
```

Essa decisão reduz dependência de drivers no servidor, preserva o ambiente local do cliente e cria uma camada reutilizável para agentes do ecossistema MouseBridge/OpenClaw.


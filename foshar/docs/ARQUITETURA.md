# Foshar — Arquitetura

**Foshar** (Folder Share) dá a uma máquina acesso a um diretório que vive em **outra**
máquina, usando o mesmo canal de rede que o ZephyrLink já estabelece. O objetivo de
experiência é "trabalhar numa pasta remota como se fosse local": abrir o projeto no
VSCode, ler/gravar arquivos, navegar, criar/remover/renomear e ver as alterações
refletidas em menos de 1 s.

O ambiente proíbe drivers de filesystem, SMB, NFS, SSHFS e privilégios de administrador.
Foshar é **Python puro**, compatível com Windows e Linux, e não depende de nenhuma dessas
tecnologias.

> Este documento cobre os entregáveis 1–11 do PRD (arquitetura, diagramas, fluxos,
> sincronização, cache, integração com VSCode, roadmaps, estrutura, backlog e riscos).
> A POC (entregável 12) é uma etapa seguinte.

---

## 0. Decisão fundamental: reusar o canal do ZephyrLink

O PRD pede para avaliar JSON-RPC, WebSocket, TCP ou "o canal existente do MouseBridge".
A análise do código (`zephyrlink/transport/`) mostra que o canal existente **já resolve**
tudo que Foshar precisaria reconstruir:

| Necessidade do Foshar | Já existe em | O que é |
|---|---|---|
| Enquadramento de mensagens sobre TCP | `transport/framing.py` | header uint32 + payload, decoder incremental, teto de 32 MB |
| Stream tipado com envio serializado | `transport/stream.py` | `MessageStream` com lock de envio, `TCP_NODELAY`, `send_many` |
| Mensagens (de)serializáveis | `transport/messages.py` | `Message(type, data)` em JSON, `PROTOCOL_VERSION` versionado |
| Autenticação sem trafegar a chave | `transport/security.py` | desafio-resposta HMAC-SHA256 |
| Canal cifrado opcional | `transport/security.py` | TLS (cert auto-assinado aceito; autenticidade pelo HMAC) |
| Allowlist de hosts | `transport/security.py` | IP exato + curinga de sufixo (`192.168.1.*`) |
| Padrão pedido→resposta correlacionado | launcher (`LAUNCH_REQUEST/ACK/RESULT`) | req_id, anti-replay, freshness, rate-limit, auditoria |
| Transferência de arquivo em pedaços | `clipboard/transfer.py` + `FILE_OFFER/DATA/END` | manifesto + chunks base64 |
| Modelo "o dono do recurso autoriza" | launcher (`AppCatalog`, `validate_args`) | o **alvo** declara o que é permitido; o pedido só carrega um id/parâmetro validado |

**Decisão:** Foshar **reusa a biblioteca `zephyrlink.transport`** (framing, `MessageStream`,
`security`) como camada de canal. Não inventa JSON-RPC nem WebSocket: ganha auth, TLS e
allowlist de graça e mantém uma única pilha de rede para manter.

### 0.1 Mas em uma conexão TCP **separada**

O canal de input do ZephyrLink é sensível a latência: `transport/stream.py` desliga o
algoritmo de Nagle justamente porque ~40 ms de atraso já arruinariam a fluidez do cursor.
Uma transferência de arquivo de dezenas de MB na **mesma** conexão TCP causaria
*head-of-line blocking* — os pacotes de mouse/teclado ficariam presos atrás dos chunks de
arquivo, travando o cursor.

**Decisão:** Foshar abre sua **própria conexão TCP**, numa porta dedicada
(`foshar.port`, sugerido `50513`), reusando toda a pilha `zephyrlink.transport` e a
**mesma `shared_key`**. "Sobre o canal do MouseBridge" significa *mesma pilha, mesma
autenticação, mesma confiança* — mas um socket independente, para que arquivos nunca
bloqueiem o input. As duas conexões são irmãs, não multiplexadas.

### 0.2 Os papéis já se encaixam

No ZephyrLink, **server** = máquina com o input físico (o operador); **client** = a
máquina controlada, que *possui e autoriza* recursos. O PRD do Foshar divide igual:

```
PRD Foshar          ZephyrLink          Papel real
foshar-server   ↔   server (operador)   quem QUER acessar os arquivos / roda o VSCode
foshar-client   ↔   client (alvo)       quem TEM os arquivos / autoriza o acesso
```

O lado que **possui os arquivos é autoritativo** — exatamente como o launcher, em que o
cliente declara `apps`/`allowed_dirs` e valida tudo. Foshar herda esse modelo de
segurança: o dono da pasta declara os *shares* e valida cada caminho.

---

## 1. Componentes (entregável 1)

```
foshar_protocol/   Mensagens FS_* e (de)serialização; usa zephyrlink.transport como canal
foshar_security/   Sandbox de shares: resolução e contenção de caminhos, modo ro/rw,
                   anti-replay, rate-limit, auditoria (espelha launcher/validate.py)
foshar_client/     Lado DONO dos arquivos: executa operações de FS dentro do sandbox,
                   observa mudanças (watch) e responde a pedidos
foshar_server/     Lado REQUISITANTE: cliente RPC, orquestra clone/sync, expõe a pasta
                   espelhada para o VSCode
foshar_cache/      Espelho local + índice SQLite (path → hash/mtime/base) para diff
                   incremental
foshar_sync/       Motor de sincronização: manifesto por hash, debounce, resolução de
                   conflito, watch bidirecional
```

```
            MÁQUINA OPERADOR (foshar-server)                 MÁQUINA ALVO (foshar-client)
        ┌───────────────────────────────────┐           ┌───────────────────────────────────┐
        │  VSCode  ── abre pasta local ──┐   │           │   diretório real do projeto       │
        │                                ▼   │           │   C:\dev\projeto  /home/u/projeto  │
        │   foshar_cache/ (espelho)  ┌───────┤           ├───────┐                            │
        │   ~/.foshar/cache/projeto  │ index │           │ shares│  foshar_security (sandbox) │
        │        ▲        │          │ .db   │           │ ro/rw │     resolve + contém        │
        │        │        ▼          └───────┤           └───────┘            ▲                │
        │   ┌─────────────────┐              │           ┌──────────────────────────────────┐ │
        │   │  foshar_sync     │             │           │  foshar_client (executor de FS)   │ │
        │   │ manifesto/diff   │             │           │  list/stat/read/write/mkdir/...   │ │
        │   └────────┬─────────┘             │           │  + watch (notifica mudanças)      │ │
        │   ┌────────▼─────────┐             │           └─────────────────┬────────────────┘ │
        │   │ foshar_server    │             │           ┌─────────────────▼────────────────┐ │
        │   │ (cliente RPC)    │             │           │ foshar_protocol (handlers FS_*)   │ │
        │   └────────┬─────────┘             │           └─────────────────┬────────────────┘ │
        └────────────┼──────────────────────┘           └─────────────────┼──────────────────┘
                     │       zephyrlink.transport  (TCP dedicado, porta 50513)
                     │       framing + MessageStream + HMAC + TLS + allowlist
                     └───────────────────────────────────────────────────────┘
```

---

## 2. Diagrama de componentes e dependências (entregável 2)

```
foshar_server ─┐                         ┌─ foshar_client
               ├─► foshar_sync ──┐       │
foshar_cache ◄─┘                 │       │
   │ (SQLite)                    ▼       ▼
   └────────────►  foshar_protocol  ◄────┘
                          │
                          ├──► foshar_security   (sandbox de caminhos / permissões)
                          │
                          └──► zephyrlink.transport
                                  ├─ framing.py    (encode/decode de frames)
                                  ├─ stream.py     (MessageStream)
                                  └─ security.py   (HMAC, TLS, allowlist)
```

Regras de dependência:

* `foshar_protocol` só conhece `zephyrlink.transport` e `foshar_security`. Não importa
  `foshar_server`/`foshar_client` (evita ciclo).
* `foshar_security` não importa nada do Foshar além de tipos de config — é pura.
* Nada do Foshar é importado por `zephyrlink/*`: **Foshar é um módulo independente** que
  *consome* o transporte, sem modificar o ZephyrLink. (Se no futuro virar parte do
  OpenClaw, basta empacotar `foshar/` à parte.)

---

## 3. Protocolo e fluxo de comunicação (entregável 3)

### 3.1 Mensagens `FS_*`

Toda operação é uma `Message(type, data)` (mesmo envelope JSON do ZephyrLink), correlata
por `req_id` — como o launcher. O servidor envia um pedido; o cliente responde com
`FS_REPLY` carregando `{req_id, ok, error, ...}`.

| Tipo | Direção | `data` | RF |
|---|---|---|---|
| `FS_LIST` | server → client | `{share, path}` | RF001 |
| `FS_STAT` | server → client | `{share, path}` | RF001/2 |
| `FS_READ` | server → client | `{share, path, offset, length}` | RF002 |
| `FS_WRITE` | server → client | `{share, path, data_b64, mode, base_hash}` | RF003 |
| `FS_CREATE` | server → client | `{share, path}` | RF004 |
| `FS_DELETE` | server → client | `{share, path}` | RF005 |
| `FS_RENAME` | server → client | `{share, src, dst}` | RF006 |
| `FS_MKDIR` | server → client | `{share, path}` | RF007 |
| `FS_RMDIR` | server → client | `{share, path, recursive}` | RF008 |
| `FS_MANIFEST` | server → client | `{share, path}` → lista `{path, size, mtime, hash}` | RF010 |
| `FS_PULL` / `FS_CHUNK` / `FS_PULL_END` | client → server | manifesto + pedaços de arquivo grande | RF009 |
| `FS_PUSH` / `FS_CHUNK` / `FS_PUSH_END` | server → client | idem, sentido inverso | RF009 |
| `FS_WATCH` / `FS_UNWATCH` | server → client | `{share, path}` | RF010 |
| `FS_EVENT` | client → server | `{share, path, kind: created\|modified\|deleted\|renamed}` | RF010 |
| `FS_REPLY` | client → server | `{req_id, ok, error, ...}` | — |

`FS_READ`/`FS_WRITE` cobrem arquivos pequenos (≤ ~1 MB) inline com `data_b64`. Arquivos
grandes (RF009) usam o fluxo de pedaços `FS_PULL/FS_CHUNK/FS_PULL_END`, idêntico em espírito
ao `FILE_OFFER/FILE_DATA/FILE_END` já existente em `clipboard/transfer.py` — respeitando o
teto de frame de 32 MB (`MAX_FRAME_SIZE`) com chunks de, por exemplo, 1 MB.

Os tipos vão para um enum próprio do Foshar; **não** precisam ser adicionados ao
`MsgType` do ZephyrLink, porque a conexão é separada e o `Message` carrega `type` como
string. (Alternativa: estender `MsgType` se quisermos uma só tabela — mas manter separado
reforça a independência do módulo.)

### 3.2 Handshake (reuso integral)

Idêntico ao do ZephyrLink, porque é a mesma `security.py`:

```
client(dono)                         server(operador)
   │  AUTH_CHALLENGE {nonce}  ◄──────────┤
   │  AUTH_RESPONSE {HMAC(key,nonce)} ──►│
   │  AUTH_OK {shares disponíveis}  ◄────┤   ← cliente anuncia os shares (id, modo ro/rw)
```

No `AUTH_OK`, o **cliente** (dono dos arquivos) anuncia o catálogo de *shares* que publica
— espelhando como o launcher manda `LAUNCH_CATALOG`. O operador nunca escolhe um caminho
livre; ele opera dentro de um `share` declarado pela máquina-alvo.

### 3.3 Fluxos (sequências)

**Clonar/abrir um projeto (clone inicial — Opção B):**

```
server                                  client(dono)
  │ FS_MANIFEST {share, "."}        ───► │  varre o share, calcula hashes
  │                  ◄── FS_REPLY {entries:[{path,size,mtime,hash}...]}
  │ (diff contra o índice SQLite local)  │
  │ para cada arquivo ausente/divergente:│
  │ FS_PULL {share, path}           ───► │  abre o arquivo
  │                  ◄── FS_CHUNK ×N      │  (1 MB cada)
  │                  ◄── FS_PULL_END {hash}
  │ grava no cache, atualiza index.db    │
  ▼ VSCode abre ~/.foshar/cache/projeto  ▼
```

**Salvar um arquivo no VSCode (local → remoto):**

```
foshar_sync (watch local) detecta gravação em cache/projeto/app.py
  │ debounce ~300 ms (agrupa salvamentos em rajada)
  │ calcula hash novo; difere do base → mudou
  │ FS_WRITE {share, "app.py", data_b64, mode:"overwrite", base_hash}  ───► client
  │                                          client: valida sandbox, compara base_hash
  │                                          (se o remoto mudou → conflito, §4.3)
  │                              ◄── FS_REPLY {ok:true, hash:novo}
  ▼ index.db: base_hash ← novo                 reflete em <1 s
```

**Alteração no lado remoto (remoto → local):**

```
server: FS_WATCH {share, "."}  ───► client liga observador (watch/polling) no share
  ...alguém edita C:\dev\projeto\README.md na máquina-alvo...
  client: FS_EVENT {share, "README.md", kind:"modified"}  ───► server
  server: FS_PULL {share, "README.md"} ───► ◄── chunks ─── grava no cache
  ▼ VSCode recarrega o arquivo (mudou em disco)
```

---

## 4. Modelo de sincronização (entregável 4)

### 4.1 Estratégia: espelho local + diff por hash (Opção B do PRD)

O VSCode abre uma **pasta local** (`~/.foshar/cache/<projeto>`); ele nunca fala com o
Foshar diretamente. Tudo que o Foshar faz é manter essa pasta coerente com o original
remoto. Vantagens num ambiente corporativo restrito: zero extensão para instalar/assinar,
zero driver, funciona com **qualquer** editor e com ferramentas de linha de comando
(`grep`, `git`, etc.) sobre o cache.

### 4.2 Sincronização incremental (RF010)

* **Unidade de diff: o arquivo, identificado por hash** (`blake2b` da `hashlib`, mais
  rápido que sha256 e já no stdlib). Um arquivo só trafega se o hash diferir.
* **Manifesto:** `FS_MANIFEST` devolve `{path, size, mtime, hash}` de toda a subárvore. O
  servidor compara com o `index.db`; baixa só o que mudou; apaga no cache o que sumiu no
  remoto.
* **Watch nos dois lados:**
  * *Local → remoto:* `foshar_sync` observa o cache. Preferência: biblioteca `watchdog`
    (Python puro, sem driver); **fallback de polling** por `mtime` quando `watchdog` não
    estiver disponível (algumas políticas corporativas). Debounce de ~300 ms agrupa
    rajadas (o VSCode grava em múltiplas etapas).
  * *Remoto → local:* o cliente roda o mesmo observador sobre o share e emite `FS_EVENT`.
* **Arquivos grandes (RF009):** transferência por pedaços (`FS_PULL/FS_CHUNK`); em V2,
  diff **em nível de bloco** (rolling hash estilo rsync) para mandar só os blocos
  alterados de arquivos grandes. No MVP, arquivo inteiro por hash.

### 4.3 Conflitos

Cada arquivo no `index.db` guarda um `base_hash` (o hash da última versão sincronizada).
No `FS_WRITE`, o servidor envia `base_hash`; o cliente compara com o hash atual do arquivo
remoto:

* hashes batem → grava, devolve novo hash (caminho feliz).
* hash remoto ≠ `base_hash` → **os dois lados mudaram desde a base**: conflito.
  * **MVP:** *last-writer-wins* com aviso no log + auditoria; opcionalmente grava a versão
    perdedora como `arquivo.foshar-conflict-<host>`.
  * **V2:** três vias (base/local/remoto) e marca de conflito estilo Git para resolução
    no editor.

A escrita é **atômica**: grava em `arquivo.tmp` no mesmo diretório e faz `os.replace`
(atômico em Windows e Linux), evitando arquivo meio-escrito se a conexão cair.

---

## 5. Estratégia de cache e persistência (entregáveis 5 e "Persistência")

| Camada | Tecnologia | Papel |
|---|---|---|
| Conteúdo dos arquivos | Espelho em disco (`~/.foshar/cache/<projeto>`) | O VSCode trabalha aqui; sobrevive a reinício |
| Índice de sincronização | **SQLite** (`foshar_cache/index.db`) | `path → {size, mtime, hash, base_hash, remote_mtime}`; permite diff incremental sem reler tudo |
| Conteúdo quente | (não necessário na Opção B) | Os arquivos já estão em disco; não há cache em memória de conteúdo |

**Por que SQLite e não cache em memória:** o índice precisa sobreviver a reinício, senão
todo `git pull`/abertura re-baixaria o projeto inteiro. SQLite é stdlib (`sqlite3`), sem
servidor, sem dependência, e dá consultas de diff rápidas. Cache **em memória** só entra
como otimização opcional do manifesto durante uma sessão (evita reler hashes a cada
sync), descartável.

**Sincronização por hash** (pedido no PRD): sim — `blake2b` é a chave de igualdade entre
local e remoto, base do diff incremental e da detecção de conflito.

---

## 6. Integração com VSCode (entregável 6) — Opção B

Escolhida a **Opção B (workspace espelhado)** para o MVP:

```
VSCode  ──abre──►  ~/.foshar/cache/projeto   ◄──sync──►  foshar  ◄──TCP──►  alvo
(nada de especial; é uma pasta local)
```

* **Esforço:** o menor das três opções. Não há extensão para desenvolver, publicar,
  assinar ou aprovar na política corporativa de extensões; não há `FileSystemProvider`
  para implementar; não há driver.
* **Experiência:** "instantânea" para abrir/editar (é disco local); a latência só aparece
  na *propagação* da alteração (meta < 1 s), atendida pelo watch + debounce.
* **Robustez:** se o canal cair, o usuário continua editando offline no cache; ao
  reconectar, o sync reconcilia por hash.
* **Bônus opcional:** um `foshar open <projeto>` que dispara o clone/sync e abre
  `code ~/.foshar/cache/projeto` — e pode reusar o **launcher** do ZephyrLink para abrir o
  VSCode na máquina certa.

Comparativo das três opções do PRD:

| Opção | Esforço | Experiência | Risco corporativo | Veredito |
|---|---|---|---|---|
| **A** FileSystemProvider (`foshar://`) | Alto (extensão JS, publicação) | Mais integrada, "pasta remota viva" | Política de extensões pode bloquear | **V2** |
| **B** Espelho local + sync | **Baixo** | Ótima p/ editar; propagação < 1 s | Nenhum | **MVP** ✅ |
| **C** Híbrido (cache + on-demand) | Médio-alto | Próxima de A sem extensão | Complexidade de coerência | **V2** |

---

## 7. Roadmap MVP (entregável 7)

Meta do MVP: **abrir um projeto remoto no VSCode e editar com sync bidirecional sob hash**,
sobre o canal do ZephyrLink, com sandbox de segurança.

1. `foshar_protocol`: enum `FS_*`, envelope `Message`, conexão dedicada reusando
   `zephyrlink.transport` (handshake idêntico ao do launcher).
2. `foshar_security`: shares (`id`, `path`, `mode`), resolução/contenção de caminho
   (espelha `validate.py::_validate_path`), proteção contra escape por symlink,
   anti-replay + freshness + rate-limit, auditoria opt-in (jsonl).
3. `foshar_client`: handlers `FS_LIST/STAT/READ/WRITE/CREATE/DELETE/RENAME/MKDIR/RMDIR`,
   todos passando pelo sandbox; escrita atômica (`os.replace`).
4. `foshar_cache`: espelho em disco + `index.db` (SQLite) com hashes.
5. `foshar_sync`: clone por manifesto, diff por hash, push local→remoto com debounce.
6. Watch bidirecional: `FS_WATCH/FS_EVENT` + observador local (`watchdog` com fallback de
   polling).
7. CLI: `foshar serve` (lado dono), `foshar open <share>` (clona + abre VSCode), `foshar
   status`.
8. Testes: contenção de caminho, round-trip de operações em loopback, diff de manifesto,
   resolução de conflito LWW, transferência grande por pedaços.

## 8. Roadmap V2 (entregável 8)

* Diff em **nível de bloco** (rolling hash) para arquivos grandes.
* **Opção A** (extensão VSCode `FileSystemProvider`) e/ou **C** (híbrido on-demand) para
  quem precisa de acesso sob demanda sem clonar a árvore inteira.
* Resolução de conflito a três vias com marcas no editor.
* Compressão de conteúdo (`zlib`) e migração do transporte para **msgpack** (ver §9),
  removendo o overhead de base64.
* Múltiplos shares/projetos simultâneos e multi-cliente.
* Permissões mais finas (glob de exclusão tipo `.gitignore`, limites por tamanho/extensão).
* GUI (aba no Tkinter do ZephyrLink) listando shares, status de sync e conflitos.

---

## 9. Serialização e performance (seção "Performance" do PRD)

| Formato | Prós | Contras | Veredito |
|---|---|---|---|
| **JSON** | já usado, legível, stdlib, versionado | binário vira base64 (+33%) | **controle/metadados (MVP)** |
| **msgpack** | binário nativo, ~compacto, sem schema, lib pura | dependência extra | **conteúdo de arquivo (V2)** |
| **protobuf** | rápido, tipado | exige `.proto` + codegen; rígido | descartado (custo alto p/ LAN) |

**Recomendação:** no MVP, **controle e metadados em JSON** (reuso integral do `Message`),
e **conteúdo de arquivo em chunks base64** — provado pelo `clipboard/transfer.py`. Em V2,
migrar o transporte de conteúdo para **msgpack** (frames binários, sem base64) usando o
`PROTOCOL_VERSION` para negociar; protobuf não compensa o custo de schema/codegen numa rede
local. Compressão `zlib` opcional por chunk (ganha em texto/código; pular em binários já
comprimidos).

Metas de performance e como são atingidas:

* *Abrir arquivos instantaneamente* → Opção B: o arquivo já está no cache em disco.
* *Alteração refletida em < 1 s* → watch + debounce 300 ms + transferência incremental de
  um arquivo pequeno em LAN (≪ 1 s).
* *Transferência incremental* → manifesto por hash; só o que mudou trafega.
* *Compressão opcional* → `zlib` por chunk (V2).

---

## 10. Segurança (seção "Segurança" do PRD)

Em camadas, espelhando o launcher e o transporte:

* **Autenticação por token:** desafio-resposta HMAC-SHA256 (`security.sign_challenge`); a
  `shared_key` nunca trafega. Mesma chave do ZephyrLink.
* **Diretórios permitidos / sandbox:** o **dono** declara `shares` (cada um com `path` e
  `mode: ro|rw`). Todo caminho recebido é `Path(...).resolve()` e precisa estar **contido**
  no `path` do share (`is_relative_to`), exatamente como `validate.py::_validate_path`.
  Caminhos absolutos, `..` e symlinks que escapem do share são **recusados** (resolve o
  alvo e revalida a contenção; nega travessia de symlink para fora).
* **Permissões:** share `ro` recusa `FS_WRITE/CREATE/DELETE/RENAME/MKDIR/RMDIR`.
* **Criptografia do canal:** TLS opcional (`security.build_*_ssl_context`), idêntico ao
  ZephyrLink.
* **Allowlist de hosts:** `security.host_allowed` (IP exato + curinga de sufixo).
* **Anti-replay + freshness:** `req_id` único por pedido (deque de vistos) e janela de
  tempo, como o launcher recusa `replay`/`expirado`.
* **Rate-limit:** teto de operações por minuto por conexão (espelha `_allow_rate`).
* **Auditoria:** log `.jsonl` opt-in de cada operação (quem, o quê, decisão, hash),
  reusando o padrão de `launcher/audit.py`.
* **Sem privilégio elevado:** todas as operações de FS rodam sob o usuário do cliente;
  nada exige admin (requisito do PRD).

---

## 11. Estrutura de diretórios (entregável 9)

```
foshar/
├── foshar_protocol/
│   ├── __init__.py
│   ├── messages.py        # enum FsMsgType, helpers de (de)serialização
│   └── channel.py         # abre conexão dedicada via zephyrlink.transport + handshake
├── foshar_client/         # lado DONO dos arquivos
│   ├── __init__.py
│   ├── service.py         # loop de recepção + dispatch FS_*
│   ├── ops.py             # operações de FS (list/read/write/...), todas sandboxed
│   └── watcher.py         # observa shares, emite FS_EVENT (watchdog + fallback polling)
├── foshar_server/         # lado REQUISITANTE (operador / VSCode)
│   ├── __init__.py
│   ├── rpc.py             # cliente RPC: envia FS_*, casa FS_REPLY por req_id
│   └── workspace.py       # orquestra clone/abertura, integra VSCode (Opção B)
├── foshar_cache/
│   ├── __init__.py
│   ├── mirror.py          # espelho em disco, escrita atômica (os.replace)
│   └── index.py           # SQLite: path → hash/mtime/base_hash
├── foshar_sync/
│   ├── __init__.py
│   ├── manifest.py        # varredura + hash (blake2b), diff de manifesto
│   └── engine.py          # motor bidirecional, debounce, resolução de conflito
├── foshar_security/
│   ├── __init__.py
│   ├── shares.py          # config de shares (id, path, mode)
│   └── sandbox.py         # resolução/contenção de caminho, ro/rw, anti-replay, rate-limit
├── tests/
├── docs/
│   └── ARQUITETURA.md     # este documento
├── examples/
│   └── config.foshar.example.yaml
└── __main__.py            # CLI: foshar serve | open | status
```

Configuração (nova seção, carregada como o resto do YAML do ZephyrLink):

```yaml
foshar:
  enabled: true
  port: 50513
  cache_dir: "~/.foshar/cache"
  audit_file: null            # opt-in: .jsonl
  rate_limit_per_min: 600
  shares:                     # declarados pela máquina DONA dos arquivos
    - id: projeto
      path: "C:\\dev\\projeto"   # ou /home/u/projeto
      mode: rw                   # ro | rw
# security.shared_key / use_tls / allowed_hosts reusados do ZephyrLink
```

---

## 12. Backlog técnico priorizado (entregável 10)

| # | Item | Prioridade | Dependências |
|---|---|---|---|
| B1 | `foshar_security.sandbox`: resolução/contenção de caminho + testes | **P0** | — |
| B2 | `foshar_protocol`: enum FS_*, canal dedicado, handshake | **P0** | transport |
| B3 | `foshar_client.ops`: list/stat/read/write/mkdir/rename/delete (sandboxed, escrita atômica) | **P0** | B1, B2 |
| B4 | `foshar_cache.index` (SQLite) + `mirror` (disco) | **P0** | — |
| B5 | `foshar_sync.manifest`: varredura + hash + diff | **P0** | B4 |
| B6 | `foshar_server.rpc` + `workspace`: clone por manifesto, abre VSCode | **P0** | B2–B5 |
| B7 | Watch local→remoto (debounce) + `FS_WRITE` push | **P1** | B5, B6 |
| B8 | Watch remoto→local (`FS_WATCH/FS_EVENT`) | **P1** | B3, B6 |
| B9 | Transferência grande por pedaços (`FS_PULL/CHUNK`) | **P1** | B2 |
| B10 | Resolução de conflito LWW + `.foshar-conflict` + auditoria | **P1** | B7 |
| B11 | Anti-replay + freshness + rate-limit | **P1** | B2 |
| B12 | CLI `foshar serve|open|status` + config YAML | **P1** | B3, B6 |
| B13 | Diff em nível de bloco (rolling hash) | **P2** | B9 |
| B14 | msgpack + compressão zlib | **P2** | B2 |
| B15 | Extensão VSCode (Opção A) / híbrido (Opção C) | **P2** | B6 |
| B16 | GUI (aba Tkinter), multi-share, glob de exclusão | **P2** | B12 |

## 13. Riscos técnicos (entregável 11)

| Risco | Impacto | Mitigação |
|---|---|---|
| **Head-of-line blocking** travando o cursor se Foshar dividisse o socket de input | Alto | Conexão TCP **separada** (§0.1); arquivos nunca disputam com mouse/teclado |
| **Escape de sandbox** por `..`, caminho absoluto ou symlink | Alto | `resolve()` + `is_relative_to`; revalidar alvo de symlink; recusar fora do share (espelha `validate.py`) |
| **`watchdog` bloqueado/instável** em política corporativa | Médio | Fallback de **polling por mtime**; watch é otimização, não requisito de correção |
| **Conflito de escrita simultânea** (dois lados editam) | Médio | `base_hash` detecta divergência; LWW + cópia `.foshar-conflict`; três vias em V2 |
| **Overhead de base64** em arquivos grandes (+33%) | Médio | Chunks; compressão zlib e msgpack em V2; diff por hash evita reenvio |
| **Teto de frame de 32 MB** (`MAX_FRAME_SIZE`) | Médio | Chunking obrigatório acima de ~1 MB (`FS_PULL/CHUNK`) |
| **Latência de sync > 1 s** sob rajada de salvamentos | Médio | Debounce 300 ms + transferência incremental de 1 arquivo pequeno em LAN |
| **Corrupção por queda no meio da escrita** | Médio | Escrita atômica `arquivo.tmp` + `os.replace` |
| **Divergência de path Windows/Linux** (`\` vs `/`, case) | Médio | Normalizar para POSIX no protocolo; resolver para o SO no cliente; tratar case-insensitive no Windows |
| **Crescimento sem limite do cache** | Baixo | Espelha o remoto (apaga o que sumiu); limpeza por projeto no `foshar status/clean` |
| **Reabertura re-baixando tudo** se o índice se perder | Baixo | `index.db` persistente em SQLite; diff por hash no reconectar |

---

## Resumo das decisões

1. **Reusar `zephyrlink.transport`** (framing, `MessageStream`, HMAC, TLS, allowlist) — não
   reinventar JSON-RPC/WebSocket.
2. **Conexão TCP dedicada** (porta 50513), irmã da de input, para não bloquear o cursor.
3. **Dono dos arquivos é autoritativo** (modelo do launcher): declara *shares* e valida
   cada caminho no sandbox.
4. **VSCode via Opção B**: espelho local + sync por hash. Menor esforço, sem extensão/driver.
5. **JSON no controle + chunks base64 no conteúdo** (MVP); **msgpack + zlib + diff de bloco**
   (V2).
6. **SQLite** para o índice de sincronização; **blake2b** como chave de igualdade.
7. **Foshar é independente**: consome o transporte, não modifica o ZephyrLink.
</content>
</invoke>

# ZephyrLink

Ferramenta open source em Python para compartilhar **um único mouse e teclado entre dois
computadores** na mesma rede local, no estilo InputLeap/Barrier.

Quando o cursor atinge a borda configurada da tela principal, o controle é transferido
automaticamente para o computador secundário — mouse, teclado e (opcionalmente) área de
transferência passam a atuar na outra máquina. Ao voltar pela borda oposta, o controle
retorna.

```
┌──────────────────┐        TCP (eventos)        ┌──────────────────┐
│    PRINCIPAL     │ ──────────────────────────▶ │    SECUNDÁRIO    │
│  (server)        │ ◀── UDP (descoberta) ─────  │  (client)        │
│  mouse/teclado   │ ◀── LEAVE / clipboard ────  │  injeta eventos  │
└──────────────────┘                             └──────────────────┘
```

## Funcionalidades

- **Mouse**: detecção de borda (esquerda/direita/superior/inferior), movimento fluido por
  deltas relativos, suporte a múltiplos monitores (tela virtual) e a resoluções diferentes
  (mapeamento proporcional da posição de cruzamento).
- **Teclado**: encaminhamento completo de eventos, incluindo combinações
  (Ctrl+C/V/X/Z, Alt+Tab, Win+R, F1–F12). Na máquina principal o input é suprimido
  enquanto o controle está remoto.
- **Clipboard**: sincronização automática bidirecional (opcional).
- **Rede**: TCP para eventos, descoberta automática por broadcast UDP, reconexão
  automática, heartbeat para detecção de queda, IP manual como fallback.
- **Segurança**: autenticação desafio-resposta HMAC-SHA256 com chave compartilhada
  (a chave nunca trafega), TLS opcional, allowlist de hosts (com curinga `192.168.1.*`).
- **GUI** (Tkinter): status da conexão, IP local/remoto, computador ativo, posição
  relativa das telas e logs em tempo real.

## Requisitos

- Python **3.12+**
- Windows 11 (alvo principal; funciona também em Linux/macOS com as ressalvas do pynput)
- Dependências: `pynput`, `pyautogui`, `pyperclip`, `PyYAML`

## Instalação

```bash
git clone https://github.com/seu-usuario/zephyrlink.git
cd zephyrlink
python -m venv .venv
.venv\Scripts\activate        # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e .
```

## Uso rápido

**1. Na máquina principal** (a que tem o mouse/teclado físicos):

```bash
zephyrlink server --key minha-chave-secreta --edge right
```

`--edge right` indica que a tela do secundário está à direita da principal.

**2. Na máquina secundária:**

```bash
zephyrlink client --key minha-chave-secreta
```

O cliente encontra o servidor automaticamente via broadcast UDP. Se a descoberta falhar
(ex.: firewall bloqueando broadcast), informe o IP manualmente:

```bash
zephyrlink client --key minha-chave-secreta --host 192.168.1.50
```

**3. Pronto.** Empurre o cursor contra a borda configurada e ele aparece na outra
máquina; empurre de volta e ele retorna.

### Interface gráfica

```bash
zephyrlink gui
```

Escolha o papel (servidor/cliente), a borda e opcionalmente o IP manual, e clique em
**Iniciar**. A janela mostra conexão, IPs, computador ativo, o diagrama das telas e os
logs ao vivo.

### Arquivo de configuração

Tudo pode ser definido em YAML (veja [`config.yaml`](config.yaml) com todos os campos
comentados):

```bash
zephyrlink server -c config.yaml
```

Exemplo mínimo:

```yaml
role: server
layout:
  edge: right
security:
  shared_key: minha-chave-secreta
  allowed_hosts: ["192.168.1.*"]
```

Flags de linha de comando (`--key`, `--port`, `--edge`, `--host`) sobrepõem o YAML.

### Firewall (Windows 11)

Libere as portas no servidor (PowerShell como administrador):

```powershell
New-NetFirewallRule -DisplayName "ZephyrLink TCP" -Direction Inbound -Protocol TCP -LocalPort 50510 -Action Allow
New-NetFirewallRule -DisplayName "ZephyrLink UDP" -Direction Inbound -Protocol UDP -LocalPort 50511 -Action Allow
```

### TLS (opcional)

Gere um certificado autoassinado e aponte o servidor para ele:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout mb.key -out mb.crt -days 3650 -subj "/CN=zephyrlink"
```

```yaml
security:
  use_tls: true
  tls_cert: mb.crt
  tls_key: mb.key
```

No cliente, `use_tls: true` (e opcionalmente `tls_ca: mb.crt` para validar o servidor;
sem CA, o canal é cifrado e a autenticidade fica garantida pelo desafio HMAC).

## Arquitetura

```
zephyrlink/
├── server/      # Orquestra a máquina principal: captura, troca de controle, heartbeat
├── client/      # Máquina secundária: conexão/reconexão, injeção de eventos, retorno
├── transport/   # Framing TCP, mensagens JSON, stream tipado, autenticação/TLS
├── clipboard/   # Sincronização por polling com supressão de eco
├── keyboard/    # Captura supressiva, serialização de teclas, injeção
├── mouse/       # Geometria de tela, detecção de borda, captura (recentralização), injeção
├── discovery/   # Broadcast UDP: responder (servidor) e sonda (cliente)
├── gui/         # Tkinter; núcleo roda em thread com loop asyncio próprio
├── config/      # Dataclasses + carregamento/validação de YAML
└── tests/       # Testes de unidade e integração (sem necessidade de display)
```

### Decisões arquiteturais

**Servidor = máquina com o input físico.** O servidor captura os eventos e decide quando
transferir o controle; o cliente é um injetor passivo que só toma uma decisão: detectar
o retorno pela borda. Isso evita estado distribuído — só existe uma máquina "dona" do
input por vez, e o protocolo `ENTER`/`LEAVE` formaliza a posse.

**Movimento por deltas + truque de recentralização.** Enquanto o controle está remoto, o
cursor local é mantido no centro da tela: cada movimento gera um delta relativo ao centro,
o delta é enviado e o cursor volta ao centro. Assim o movimento remoto é ilimitado e fluido
(o cursor local nunca esbarra em borda), e funciona com qualquer combinação de resoluções.

**Posição de cruzamento como razão (0–1), não pixels.** Sair a 50% da altura da borda
direita de uma tela 1080p entra a 50% da altura da borda esquerda de uma tela 4K. O
mapeamento proporcional resolve resoluções diferentes sem tabela de conversão.

**Retorno detectado no cliente.** O cliente conhece a própria tela com precisão; quando
um movimento tenta ultrapassar a borda voltada para o servidor, ele envia `LEAVE` com a
razão da posição, e o servidor reposiciona o cursor local no ponto correspondente.

**asyncio + threads do pynput.** Rede, heartbeat, clipboard e descoberta são tarefas
asyncio num único loop. Os listeners do pynput rodam em threads próprias (exigência da
biblioteca); o handoff para o loop é feito exclusivamente com `call_soon_threadsafe` e
uma fila de eventos, mantendo a ordem dos eventos e o loop como único dono do socket.

**Framing com prefixo de tamanho + JSON.** TCP não preserva fronteiras de mensagem; um
header uint32 resolve. JSON (~50–120 bytes por evento) é folgado para rede local e
trivial de depurar; o protocolo é versionado para permitir migração futura a binário.

**Segurança em camadas independentes.** Allowlist de hosts (filtro barato na aceitação),
HMAC desafio-resposta (prova posse da chave sem transmiti-la) e TLS opcional (cifra o
canal). Cada camada funciona sem as outras.

**Tkinter sem toques de outras threads.** A GUI consome status e logs por filas
thread-safe drenadas via `after()`; o núcleo nunca chama widgets diretamente.

### Protocolo

| Mensagem | Direção | Conteúdo |
|---|---|---|
| `AUTH_CHALLENGE` / `AUTH_RESPONSE` / `AUTH_OK` | handshake | nonce / HMAC / tela do servidor |
| `SCREEN_INFO` | cliente → servidor | geometria da tela do cliente |
| `ENTER` | servidor → cliente | borda + razão: controle vai para o cliente |
| `MOUSE_MOVE` / `MOUSE_BUTTON` / `MOUSE_SCROLL` | servidor → cliente | deltas / botões / scroll |
| `KEY_EVENT` | servidor → cliente | tecla serializada + pressionada/solta |
| `LEAVE` | cliente → servidor | razão: controle volta ao servidor |
| `CLIPBOARD` | ambas | texto da área de transferência |
| `PING` / `PONG` | ambas | heartbeat |

## Testes

```bash
python -m unittest discover -s tests -v
```

A suíte (61 casos) cobre framing incremental, (de)serialização do protocolo,
autenticação HMAC, allowlist, carregamento/validação de configuração, detecção de borda
(incluindo multi-monitor com origem negativa e mapeamento entre resoluções), pacotes de
descoberta e um handshake de autenticação completo sobre TCP real em loopback. Os testes
não exigem display nem as bibliotecas de input instaladas.

## Limitações conhecidas

- Clipboard sincroniza apenas **texto** (limitação do pyperclip).
- No Windows, aplicações elevadas (executando como administrador) não recebem eventos
  injetados a menos que o ZephyrLink também rode elevado.
- `Ctrl+Alt+Del` e a tela de bloqueio não são capturáveis (restrição do sistema).
- Dois computadores por enquanto (1 servidor ↔ 1 cliente).

## Fases de desenvolvimento

| Fase | Escopo | Status |
|---|---|---|
| 1 | Compartilhamento básico de mouse (borda, deltas, retorno) | ✅ |
| 2 | Teclado (captura supressiva, combinações, serialização) | ✅ |
| 3 | Clipboard (polling, anti-eco, limite de tamanho) | ✅ |
| 4 | Descoberta automática (broadcast UDP) + reconexão + heartbeat | ✅ |
| 5 | Interface gráfica (Tkinter) | ✅ |
| 6 | Otimização e testes (fila com backpressure, suíte de testes) | ✅ |

## Licença

MIT

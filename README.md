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
- **Acesso remoto (tela + controle)**: ver e controlar a tela de um cliente numa janela,
  estilo RDP — mas **sem** usar a Área de Trabalho Remota do Windows, então funciona mesmo
  em máquinas com o RDP desabilitado. Consentimento no alvo, indicador visível e
  criptografia (ver [Acesso remoto tipo RDP](#acesso-remoto-tipo-rdp)).
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
- Dependências: `pynput`, `pyautogui`, `pyperclip`, `PyYAML`, `mss`, `Pillow`
  (as duas últimas só são usadas pelo acesso remoto de tela)

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
zephyrlink server --key minha-chave-secreta
```

O servidor não declara bordas — ele aprende a posição de cada cliente quando eles
conectam.

**2. Em cada máquina secundária**, informe qual borda do servidor ela ocupa com `--edge`:

```bash
zephyrlink client --key minha-chave-secreta --edge right
```

O cliente encontra o servidor automaticamente via broadcast UDP. Se a descoberta falhar
(ex.: firewall bloqueando broadcast), informe o IP manualmente:

```bash
zephyrlink client --key minha-chave-secreta --edge right --host 192.168.1.50
```

**3. Pronto.** Empurre o cursor contra a borda correspondente e ele aparece na outra
máquina; empurre de volta e ele retorna.

### Vários clientes (topologia estrela)

O servidor aceita **um cliente por borda**, permitindo até quatro máquinas secundárias ao
redor da principal. Cada cliente declara sua borda; ao cruzar aquela borda, o controle vai
para o cliente correspondente. Exemplo com a principal à esquerda, um cliente à direita e
outro abaixo:

```bash
# máquina principal
zephyrlink server --key K

# cliente à direita
zephyrlink client --key K --edge right --host 192.168.10.114

# cliente abaixo
zephyrlink client --key K --edge bottom --host 192.168.10.114
```

A área de transferência é sincronizada entre **todas** as máquinas conectadas:
**texto** em qualquer plataforma e **arquivos/pastas** (um ou vários, copiados
no Explorer) entre máquinas Windows. Os arquivos vão em pedaços pelo mesmo canal
e ficam prontos para colar no destino; o teto por transferência é
`clipboard.file_max_bytes`.

### Interface gráfica

```bash
zephyrlink gui
```

Escolha o papel (servidor/cliente), a borda e opcionalmente o IP manual, e clique em
**Iniciar**. A janela mostra conexão, IPs, computador ativo, o diagrama das telas e os
logs ao vivo. Ao clicar em **Iniciar**, o papel, o IP manual e a chave compartilhada
são gravados em `./config.yaml` e voltam **pré-preenchidos** na próxima abertura.

### Abertura remota de aplicações

O servidor pode pedir a um cliente que **abra uma aplicação** (Notepad, navegador,
calculadora, executáveis corporativos). O modelo é **fechado por construção**: quem
decide o que pode ser aberto é o **cliente** (a máquina-alvo), não o operador. O
servidor envia apenas um `id`; o comando real nunca trafega.

**Pela GUI (mais simples):** rode `zephyrlink gui` na máquina-alvo e clique em
**"Apps permitidos…"**. O editor lista os apps, permite adicionar/editar/remover e,
no cadastro, oferece o atalho **"Site"** (informe a URL e o navegador) ou
**"Aplicativo"** (escolha o `.exe`). Ao salvar, ele grava em `./config.yaml` e liga o
launcher. O cliente carrega `./config.yaml` automaticamente (sem precisar de `-c`), então
a mesma lista vale para o cliente headless via `pythonw`.

**Pelo arquivo:** ou edite o `config.yaml` **do cliente** direto (desligado por padrão):

```yaml
launcher:
  enabled: true
  rate_limit_per_min: 30           # teto de aberturas que este cliente aceita por minuto
  audit_file: null                 # opt-in: .jsonl com as decisões; null = desligado
  apps:
    - id: notepad
      label: Bloco de Notas
      command: ["notepad.exe"]
      platform: windows            # windows | linux | macos | omitido = qualquer
      sha256: "a1b2…"              # opcional: recusa abrir se o binário não bater
    - id: navegador
      label: Navegador (URL)
      command: ["xdg-open"]
      platform: linux
      accepts_args: true           # operador pode informar um parâmetro
      arg_kind: url                # url | path_in_dir | enum
      allowed_url_schemes: ["https"]
      allowed_url_hosts: ["*.empresa.com"]   # vazio = qualquer host
      require_confirm: true        # pede confirmação na máquina-alvo
```

Na GUI do servidor, o painel **Aplicações remotas** lista os apps que cada cliente
publicou, oferece um campo de **parâmetro** (habilitado só para apps que aceitam),
dispara a abertura e mostra o histórico com o estado de cada pedido
(Enviado → Recebido → Executando → Concluído / Falhou).

**Por linha de comando:** com o servidor já rodando (GUI ou headless), dispare a
abertura sem a GUI pelo subcomando `launch`, que fala com o servidor pelo canal de
controle local (`127.0.0.1`, autenticado pela mesma chave):

```bash
# identifica o cliente por IP ou por borda
zephyrlink launch --client 192.168.10.121 --app dashboard-rtc
zephyrlink launch --client bottom --app navegador --arg https://intranet.empresa.com
```

O `--app` aceita o **id** ou o **label** (ex.: `--app gid` ou `--app "GID (Caixa)"`).
Para ver o que cada cliente publicou, liste o catálogo:

```bash
zephyrlink apps                          # todos os clientes
zephyrlink apps --client 192.168.10.121  # só um cliente
```

O comando aguarda o resultado e sai com código 0 em sucesso (ou imprime o motivo da
falha) — pronto para usar em um `.bat`/atalho.

Garantias do modelo: o **parâmetro é validado no cliente** conforme o `arg_kind`
(esquema/host de URL, caminho contido em `allowed_dirs`, ou valor de uma lista
`enum_values`); apps com `require_confirm` exigem aprovação na própria máquina-alvo;
pedidos repetidos ou fora de uma janela de 30 s são descartados (anti-replay); e a
execução é sempre sem shell (`shell=False`) e sob o usuário do cliente — nunca elevada.

Endurecimento opcional: defina `sha256` por app para que o cliente **recuse abrir
se o binário no disco tiver sido trocado** (defesa contra substituição por malware),
e aponte `audit_file` para um caminho `.jsonl` para registrar cada decisão (aceita,
recusada, concluída, falha) com carimbo de tempo. Ambos são opt-in e ficam desligados
por padrão.

### Acesso remoto tipo RDP

Além de encaminhar mouse/teclado, o ZephyrLink pode **exibir a tela de um cliente numa
janela e controlá-la** — como um RDP/VNC. A diferença importante: ele **não usa a Área de
Trabalho Remota (RDP/Terminal Services) do Windows**. A tela é capturada pelo framebuffer
(`mss`) e o input é injetado em espaço de usuário (`pynput`), exatamente como TeamViewer/
AnyDesk. Por isso **funciona mesmo em máquinas com o RDP desabilitado**.

Papéis (reaproveitam os de sempre):

- **Operador** = `server` (tem o mouse/teclado; abre a janela e controla). Inicia a sessão.
- **Alvo** = `client` (é visto/controlado). Precisa habilitar e consentir.

**No alvo** (`config.yaml`):

```yaml
security:
  use_tls: true            # os frames expõem a tela — criptografia é exigida
  # ... tls_cert / tls_key (ver a seção TLS)
remote_desktop:
  enabled: true            # desligado por padrão; o alvo é quem autoriza
  require_consent: true    # pede confirmação antes de cada sessão
  fps: 12
  quality: 60              # JPEG; o operador pode ajustar em runtime
  scale: 1.0               # reduza (ex.: 0.5) para economizar banda
  # idle_timeout: 300      # encerra sozinho após N s ociosos (0 = desligado)
  # audit_file: rd.jsonl   # registra início/fim das sessões
```

**No operador**: abra a GUI em modo servidor (`zephyrlink gui`), conecte o cliente,
selecione-o em *Aplicações remotas* e clique **"Ver / controlar tela"**. O alvo mostra um
pedido de consentimento; ao aceitar, a janela exibe a tela remota e o mouse/teclado
passam a operá-la. Um controle deslizante ajusta a qualidade; "Desconectar" encerra.

Salvaguardas de segurança (o "de forma segura"):

- **Opt-in no alvo** (`enabled: false` por padrão) e **consentimento** por sessão.
- **Criptografia obrigatória**: sem `security.use_tls` a sessão é **recusada**, a menos que
  você assuma o risco com `remote_desktop.allow_insecure: true` (rede confiável).
- Reusa a **autenticação HMAC** e a **allowlist de hosts** do handshake normal.
- **Indicador visível** no alvo enquanto compartilha + **auditoria** opt-in.
- **Tecla de pânico** no alvo (**Ctrl+Alt+Esc**) encerra o compartilhamento na hora;
  `idle_timeout` e a queda da conexão também encerram.
- **Somente LAN** (descoberta/allowlist); não há relay/NAT pela internet.

> Use apenas em máquinas que você administra ou tem autorização para acessar.

No **macOS** o alvo precisa conceder, além de *Acessibilidade* (para injetar input),
a permissão de *Gravação de Tela* (Ajustes → Privacidade e Segurança → Gravação de Tela)
ao app/terminal — sem ela a captura devolve quadros em branco.

### Arquivo de configuração

Tudo pode ser definido em YAML. O repositório traz
[`config.example.yaml`](config.example.yaml) com todos os campos comentados — copie
para `config.yaml` e ajuste:

```bash
cp config.example.yaml config.yaml   # Windows: copy config.example.yaml config.yaml
zephyrlink server -c config.yaml
```

O `config.yaml` **não é versionado** (está no `.gitignore`): é o seu arquivo ao vivo,
que a GUI grava e o cliente/servidor carregam automaticamente. Assim, `git pull` nunca
sobrescreve a sua configuração.

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

### macOS — Spaces e Mission Control

No macOS é possível associar **botões do mouse** aos atalhos nativos de
navegação entre Mesas/Spaces. As ações rodam na máquina macOS por injeção de
teclado (a mesma biblioteca já usada, `pynput`) — **Windows e Linux não são
afetados** e continuam com o comportamento normal.

Ações disponíveis:

| Ação | Atalho enviado | Efeito |
|------|----------------|--------|
| `mac_space_left` | Control + ← | Space anterior |
| `mac_space_right` | Control + → | Próximo Space |
| `mac_mission_control` | Control + ↑ | Abre o Mission Control |
| `mac_space_1` … `mac_space_4` | Control + 1 … 4 | Vai direto para a Mesa 1–4 |

**Como associar aos botões (no `config.yaml` da máquina macOS):**

```yaml
mouse_actions:
  button8: mac_space_left     # botão lateral "voltar"
  button9: mac_space_right    # botão lateral "avançar"
  button10: mac_mission_control
```

Os nomes `button8`/`button9`/`button10` são os botões laterais capturados no
**servidor** (Windows/Linux) e encaminhados; no macOS eles disparam a ação em
vez de serem injetados como clique. Botões não mapeados seguem funcionando
normalmente. O `pynput` no macOS só reconhece os botões esquerdo/meio/direito,
então o gatilho dos botões laterais vem de um servidor Windows/Linux que os
possui.

**Requisitos e permissões:**

- **Acessibilidade** (obrigatória): Ajustes do Sistema → Privacidade e
  Segurança → **Acessibilidade** → adicione o app (o próprio ZephyrLink pede
  essa permissão ao abrir). Sem ela a injeção não surte efeito; o programa
  **não trava nem encerra** — registra um aviso e libera o Control.
- **Monitoramento de Entrada**: Ajustes do Sistema → Privacidade e Segurança →
  **Monitoramento de Entrada** — necessária para a captura/injeção de eventos
  do ZephyrLink em geral.
- **`Control + 1..4`**: precisam estar **habilitados** em Ajustes do Sistema →
  Teclado → Atalhos de Teclado → **Mission Control** (por padrão a Apple os
  deixa desligados). As setas (`Control + ←/→/↑`) já vêm habilitadas.

As teclas modificadoras são **sempre liberadas** ao final (inclusive se ocorrer
erro), então o Control nunca fica virtualmente preso.

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
| `SCREEN_INFO` | cliente → servidor | geometria da tela + borda do servidor que o cliente ocupa |
| `ENTER` | servidor → cliente | borda + razão: controle vai para o cliente |
| `MOUSE_MOVE` / `MOUSE_BUTTON` / `MOUSE_SCROLL` | servidor → cliente | deltas / botões / scroll |
| `KEY_EVENT` | servidor → cliente | tecla serializada + pressionada/solta |
| `LEAVE` | cliente → servidor | razão: controle volta ao servidor |
| `CLIPBOARD` | ambas | texto da área de transferência |
| `FILE_OFFER` / `FILE_DATA` / `FILE_END` | ambas | manifesto + pedaços (base64) + fim de uma transferência de arquivos |
| `RD_START` / `RD_ACCEPT` / `RD_REJECT` / `RD_STOP` | negociação | pedido do operador / consentimento do alvo / fim da sessão de tela |
| `VIDEO_CONFIG` | servidor → cliente | ajuste de fps/qualidade/escala em runtime |
| `VIDEO_FRAME` | cliente → servidor | frame de tela (**binário**: tag `0x01` + meta JSON + JPEG) |
| `INPUT_MOVE_ABS` | servidor → cliente | posição absoluta do cursor (razão `[0,1]` da tela remota) |
| `PING` / `PONG` | ambas | heartbeat |

## Testes

```bash
python -m unittest discover -s tests -v
```

A suíte cobre framing incremental (incluindo frames binários de vídeo),
(de)serialização do protocolo, autenticação HMAC, allowlist, carregamento/validação de
configuração, detecção de borda (incluindo multi-monitor com origem negativa e mapeamento
entre resoluções), pacotes de descoberta, um handshake de autenticação completo sobre TCP
real em loopback e, para o acesso remoto, o mapeamento de coordenadas razão↔absoluta, a
tradução de teclas do viewer e o gate de consentimento/TLS do alvo. Os testes não exigem
display nem as bibliotecas de input/captura instaladas.

## Limitações conhecidas

- Clipboard de **texto** funciona em qualquer plataforma; o de **arquivos/pastas**
  é Windows↔Windows (usa `CF_HDROP`). Cópias simultâneas de máquinas diferentes
  serializam (uma transferência ativa por vez).
- No Windows, aplicações elevadas (executando como administrador) não recebem eventos
  injetados a menos que o ZephyrLink também rode elevado.
- `Ctrl+Alt+Del` e a tela de bloqueio não são capturáveis (restrição do sistema).
- Topologia em estrela: a máquina principal no centro e um cliente por borda (até quatro
  secundárias). Não há modo cadeia (atravessar um cliente para chegar a outro).
- Acesso remoto de tela: usa Motion-JPEG (um JPEG por quadro), pensado para suporte em
  LAN — sem diff por tiles/codec de vídeo, sem áudio e sem relay pela internet. A janela
  de controle roda pela GUI (modo servidor); não há visualizador headless.

## Fases de desenvolvimento

| Fase | Escopo | Status |
|---|---|---|
| 1 | Compartilhamento básico de mouse (borda, deltas, retorno) | ✅ |
| 2 | Teclado (captura supressiva, combinações, serialização) | ✅ |
| 3 | Clipboard (polling, anti-eco, limite de tamanho) | ✅ |
| 4 | Descoberta automática (broadcast UDP) + reconexão + heartbeat | ✅ |
| 5 | Interface gráfica (Tkinter) | ✅ |
| 6 | Otimização e testes (fila com backpressure, suíte de testes) | ✅ |
| 7 | Acesso remoto tipo RDP (captura de tela, controle absoluto, consentimento) | ✅ |

## Licença

MIT

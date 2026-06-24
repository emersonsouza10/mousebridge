# Foshar — Folder Share

Acesso e edição de uma pasta que vive em **outra** máquina, sobre o canal que o ZephyrLink
já estabelece — Python puro, sem driver de filesystem, SMB, NFS, SSHFS ou privilégio de
administrador. Compatível com Windows e Linux.

**Foshar é um módulo independente**: consome `zephyrlink.transport` (framing, autenticação
HMAC, TLS, allowlist) numa conexão TCP **própria**, sem modificar o ZephyrLink.

* O lado que **possui os arquivos** (`foshar-client`) é autoritativo: declara *shares* e
  valida cada caminho num sandbox — mesmo modelo do launcher.
* O lado que **acessa** (`foshar-server`) mantém um **espelho local** que o VSCode abre
  como pasta comum; o Foshar sincroniza por hash (Opção B).

## Status

**POC mínima (B1→B6) implementada e testada** — zero dependência nova (usa só o que o
ZephyrLink já traz: `PyYAML`, `sqlite3`/`blake2b`/`asyncio` do stdlib).

Pronto: sandbox de caminhos, protocolo `FS_*` + handshake HMAC em conexão dedicada,
operações `list/stat/read/write/create/delete/rename/mkdir/rmdir`, cache (espelho +
índice SQLite), manifesto/diff por hash, e clone incremental que abre no VSCode.

Pendente (próximas fases): watch bidirecional + push automático (B7/B8), transferência
de arquivos grandes por pedaços (B9), resolução de conflito (B10), msgpack/compressão.

```bash
# na máquina DONA dos arquivos (publica os shares do config.yaml)
python3 -m foshar serve -c config.yaml

# na máquina que vai EDITAR (clona o share e abre no VSCode)
python3 -m foshar open --host 192.168.1.50 --share projeto

python3 -m foshar gui             # cadastra as pastas compartilhadas (formulário)
python3 -m foshar status          # lista os shares configurados
```

Configuração de exemplo: [`examples/config.foshar.example.yaml`](examples/config.foshar.example.yaml).
Testes: `python3 -m unittest discover -s foshar/tests -t .`

➡️ Desenho completo (componentes, protocolo, sync, cache, segurança, roadmap, backlog,
riscos): [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).
</content>

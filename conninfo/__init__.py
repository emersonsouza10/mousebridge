"""connInfo — execução remota controlada de banco sobre o canal do MouseBridge.

O servidor (agente) nunca conecta no banco: ele pede, e a máquina cliente
(``ProviderHost``) executa localmente com os drivers/credenciais dela, devolvendo
apenas resultados em JSON. Ver ``docs/ARQUITETURA.md``.

Fachada pública para o agente:

    import conninfo
    conn = conninfo.connect("vendas_lite", host="10.0.0.5", key="...")
    for row in conn.execute("select * from clientes"):
        ...
    conn.close()
"""

from __future__ import annotations

from conninfo.ci_gateway.api import Connection, Cursor, connect, list_connections

__all__ = ["Connection", "Cursor", "connect", "list_connections"]

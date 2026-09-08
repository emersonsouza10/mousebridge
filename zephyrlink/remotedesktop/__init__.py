"""Acesso remoto tipo RDP (tela + controle) sobre o protocolo ZephyrLink.

Não usa o RDP/Terminal Services do SO: captura o framebuffer (mss) e injeta
input (pynput) em espaço de usuário, então funciona mesmo em máquinas com a
"Área de Trabalho Remota" desabilitada. Desenhado para uso consentido em
máquinas administradas: opt-in no alvo, criptografia recomendada/obrigatória,
consentimento e indicador visível.

Papéis: o SERVIDOR (operador) inicia a sessão e exibe/controla; o CLIENTE
(alvo) consente, captura a própria tela e injeta o input recebido.
"""

from __future__ import annotations

from zephyrlink.remotedesktop.coords import abs_to_ratio, clamp01, ratio_to_abs

__all__ = ["abs_to_ratio", "clamp01", "ratio_to_abs"]

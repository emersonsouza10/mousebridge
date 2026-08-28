#!/bin/bash
# Roda o ZephyrLink em PRIMEIRO PLANO (anexado ao Terminal).
#
# Importante no macOS: NÃO use 'nohup ... &'. Desanexar o processo do Terminal
# faz o macOS não aplicar a permissão de Acessibilidade/Monitoramento de Entrada
# concedida ao Terminal — e aí a captura do mouse/teclado não funciona
# ("This process is not trusted!"). Rodando anexado, o processo herda a
# permissão do Terminal.
cd /Users/emersonsouza10/source/mousebridge
source .venv/bin/activate
python -m zephyrlink gui

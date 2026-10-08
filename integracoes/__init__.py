"""Conectores somente leitura com os sistemas da Latitudes (V1: RD Station CRM
e Envision). Regras em docs/regras-integracao-clientes.md.

Cada conector só faz as chamadas de uma lista fechada; a camada HTTP comum
(integracoes/http.py) ainda recusa qualquer escrita. Novos sistemas (V2)
entram como novos conectores com a mesma interface (integracoes/base.py).
"""

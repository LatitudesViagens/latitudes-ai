"""Verificação das integrações com RD Station CRM e Envision (somente leitura).

Executado SOMENTE pela Isabelle, com as credenciais reais do
latitudes_agent/.env. Usa os mesmos conectores da ÁGORA, com as mesmas travas
de leitura. Nunca imprime credenciais.

Uso (PowerShell, na pasta do projeto):
  .venv\\Scripts\\python.exe scripts\\verificar_integracoes.py --nivel 1
  .venv\\Scripts\\python.exe scripts\\verificar_integracoes.py --nivel 2 --limite 3
  .venv\\Scripts\\python.exe scripts\\verificar_integracoes.py --nivel 3 --limite 50
  .venv\\Scripts\\python.exe scripts\\verificar_integracoes.py --nivel 4 --email cliente@exemplo.com

Níveis:
  1  Conexão com cada sistema: "conectado" ou o erro. No Envision, testa o
     login com usuário e senha (com e sem a chave) e a chave sozinha, e diz
     qual forma funcionou.
  2  Formato dos dados: SÓ os nomes e os tipos dos campos, nunca os valores.
     Mostra também os nomes dos campos personalizados do RD.
  3  Funis do RD e nomes de produtos do Envision com a classificação de cada
     um, destacando os não mapeados/não classificados.
  4  Perfil de um cliente pelo e-mail, para comparar com as telas.
  5  Descobre quais campos do Records/Query filtram por tipo de registro e
     por passageiro. Usa um e-mail de passageiro dos próprios registros
     recentes (ou --email), sem mostrá-lo. Só contagens.
  6  Formato do externalId e dos documentos dos registros recentes (tamanho,
     se parece CPF ou e-mail), sem mostrar valores.
"""

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import truststore  # noqa: E402

# O Kaspersky das máquinas da Latitudes intercepta HTTPS (ver CLAUDE.md).
truststore.inject_into_ssl()

from integracoes import config  # noqa: E402
from integracoes.envision import Envision  # noqa: E402
from integracoes.erros import ChamadaBloqueadaError, IntegracaoError  # noqa: E402
from integracoes.rd_crm import RDCRM  # noqa: E402
from services.perfil_cliente import (  # noqa: E402
    BuscadorPerfil,
    carregar_mapeamentos,
    classificar_funil,
    classificar_produto,
    normalizar_texto,
    resumo_para_ia,
)

_CPF = re.compile(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$")


def titulo(texto: str) -> None:
    print(f"\n=== {texto} ===")


def falha(sistema: str, erro: Exception) -> None:
    if isinstance(erro, (IntegracaoError, ChamadaBloqueadaError)):
        print(f"  {sistema}: ERRO — {erro}")
    else:
        # Só o tipo: a mensagem de bibliotecas pode conter a URL.
        print(f"  {sistema}: ERRO inesperado ({type(erro).__name__})")


# --- Estrutura sem valores ----------------------------------------------------------
def _tipo(valor) -> str:
    if valor is None:
        return "vazio"
    if isinstance(valor, bool):
        return "verdadeiro/falso"
    if isinstance(valor, (int, float)):
        return "número"
    if isinstance(valor, str):
        return "texto"
    if isinstance(valor, list):
        return "lista"
    if isinstance(valor, dict):
        return "objeto"
    return type(valor).__name__


def _dica(valor) -> str:
    """Indícios úteis sem mostrar o valor."""
    if not isinstance(valor, str) or not valor.strip():
        return ""
    if _CPF.match(valor.strip()):
        return "  (parece CPF)"
    classificacao = classificar_produto(valor, carregar_mapeamentos())
    if classificacao.tipo != "Não classificado" and classificacao.mes:
        return "  (parece nome de produto TIPO DESTINO MÊS ANO)"
    return ""


def estrutura(valores: list, prefixo: str = "", linhas: dict | None = None, profundidade: int = 0) -> dict:
    """Une a estrutura de vários exemplos: {caminho: {tipos}} — sem valores."""
    linhas = {} if linhas is None else linhas

    if profundidade > 6:
        return linhas

    for valor in valores:
        if isinstance(valor, dict):
            for chave, filho in valor.items():
                caminho = f"{prefixo}.{chave}" if prefixo else str(chave)
                registro = linhas.setdefault(caminho, {"tipos": set(), "dicas": set()})
                registro["tipos"].add(_tipo(filho))
                dica = _dica(filho)
                if dica:
                    registro["dicas"].add(dica)
                if isinstance(filho, (dict, list)):
                    estrutura([filho], caminho, linhas, profundidade + 1)
        elif isinstance(valor, list):
            itens = [item for item in valor[:5] if isinstance(item, (dict, list))]
            if itens:
                estrutura(itens, f"{prefixo}[]", linhas, profundidade + 1)
            for item in valor[:5]:
                if not isinstance(item, (dict, list)):
                    registro = linhas.setdefault(f"{prefixo}[]", {"tipos": set(), "dicas": set()})
                    registro["tipos"].add(_tipo(item))
                    dica = _dica(item)
                    if dica:
                        registro["dicas"].add(dica)

    return linhas


def imprimir_estrutura(valores: list) -> None:
    for caminho, info in sorted(estrutura(valores).items()):
        dicas = "".join(sorted(info["dicas"]))
        print(f"    {caminho}: {' | '.join(sorted(info['tipos']))}{dicas}")


# --- Níveis --------------------------------------------------------------------------
def nivel_1() -> None:
    titulo("Nível 1 — conexão")

    try:
        RDCRM().testar_conexao()
        print("  RD Station CRM: conectado")
    except Exception as erro:
        falha("RD Station CRM", erro)

    print(
        "  Envision: endereço "
        + ("configurado" if config.envision_base_url() else "AUSENTE (ENVISION_BASE_URL)")
        + "; chave "
        + ("configurada" if config.envision_api_key() else "AUSENTE (ENVISION_API_KEY)")
        + "; usuário/senha "
        + ("configurados" if config.envision_usuario() and config.envision_senha() else "AUSENTES (ENVISION_USUARIO, ENVISION_SENHA)")
    )

    for formato in config.ENVISION_FORMATOS_CHAVE:
        try:
            Envision(formato_chave=formato).testar_conexao()
        except Exception as erro:
            falha(f"Envision (chave {formato})", erro)
            continue

        print(f"  Envision: conectado (formato da chave: {formato})")
        if formato != config.envision_formato_chave():
            print(f"    -> acrescente ENVISION_AUTH_FORMATO={formato} no .env e no Azure")
        return


def nivel_2(limite: int) -> None:
    titulo("Nível 2 — formato dos dados (só nomes e tipos dos campos)")

    try:
        rd = RDCRM()
        contatos = rd.amostra_contatos(limite)
        print(f"\n  RD Station CRM — contato ({len(contatos)} exemplo(s)):")
        imprimir_estrutura(contatos)

        for entidade, nome in (("contact", "contato"), ("deal", "negociação")):
            campos = rd.listar_campos_personalizados(entidade)
            print(f"\n  RD Station CRM — campos personalizados de {nome}:")
            for campo in campos:
                print(f"    id {campo.get('id')}: \"{campo.get('label')}\" ({campo.get('type')})")
            if not campos:
                print("    (nenhum)")

        negociacao_ids = [d.get("_id") or d.get("id") for c in contatos for d in c.get("deals") or []][:limite]
        negociacoes = [rd.obter_negociacao(i) for i in negociacao_ids if i]
        print(f"\n  RD Station CRM — negociação ({len(negociacoes)} exemplo(s)):")
        imprimir_estrutura(negociacoes)
    except Exception as erro:
        falha("RD Station CRM", erro)

    try:
        envision = Envision()
        # Formato de consulta indicado pelo Envision por e-mail. Uma página só,
        # com as ordens criadas nos últimos 7 dias (cada consulta pode ter custo).
        fim = datetime.now()
        inicio = fim - timedelta(days=7)
        criteria = (
            f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && "
            f"Created <= '{fim:%Y-%m-%d %H:%M:%S}'"
        )
        registros = envision.consultar_registros(criteria, maximo_paginas=1)
        print(f"\n  Envision — Records/Query, ordens criadas nos últimos 7 dias ({len(registros)} na 1ª página; estrutura de {min(len(registros), limite)}):")
        imprimir_estrutura(registros[:limite])
        tipos = sorted({str(r.get("type")) for r in registros})
        print(f"    tipos de registro encontrados: {', '.join(tipos) or '(nenhum)'}")
    except Exception as erro:
        falha("Envision", erro)


def nivel_3(limite: int) -> None:
    titulo("Nível 3 — classificação de funis e produtos")
    mapeamentos = carregar_mapeamentos()
    mapeados = {normalizar_texto(nome) for nome in [*mapeamentos["rd_funis"], *mapeamentos.get("rd_funis_ocultos", [])]}
    ocultos = {normalizar_texto(nome) for nome in mapeamentos.get("rd_funis_ocultos", [])}

    try:
        print("\n  Funis do RD Station CRM:")
        for funil in RDCRM().listar_funis():
            nome = funil.get("name")
            tipo = "não aparece no perfil" if normalizar_texto(nome) in ocultos else classificar_funil(nome, mapeamentos)
            alerta = "" if normalizar_texto(nome) in mapeados else "   <-- NÃO MAPEADO (vira Outros)"
            print(f"    {nome} -> {tipo}{alerta}")
    except Exception as erro:
        falha("RD Station CRM", erro)

    campo_produto = mapeamentos["envision_campos"]["campo_produto"]

    try:
        envision = Envision()
        # Últimos 30 dias, no máximo 2 páginas (cada consulta pode ter custo).
        fim = datetime.now()
        inicio = fim - timedelta(days=30)
        registros = envision.consultar_registros(
            f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'",
            maximo_paginas=2,
        )[:limite]

        # Por tipo de registro: só contagens e nomes de status (sem dados de
        # clientes), para saber qual tipo é a venda (viagem).
        print(f"\n  Envision — tipos de registro ({len(registros)} registro(s), últimos 30 dias):")
        por_tipo: dict[str, list[dict]] = {}
        for registro in registros:
            por_tipo.setdefault(str(registro.get("type")), []).append(registro)

        for tipo, itens in sorted(por_tipo.items()):
            status = sorted({str((r.get("status") or {}).get("name")) for r in itens})
            com_produto = sum(1 for r in itens if classificar_produto(r.get(campo_produto), mapeamentos).tipo != "Não classificado")
            com_valor = sum(1 for r in itens if (r.get("totalValue") or {}).get("value"))
            com_destino = sum(1 for r in itens if (r.get("destination") or {}).get("name"))
            com_viajantes = sum(1 for r in itens if r.get("travellers"))
            com_pai = sum(1 for r in itens if r.get("parentRecordId"))
            print(
                f"    {tipo}: {len(itens)} registro(s); com nome de produto: {com_produto}; "
                f"com valor: {com_valor}; com destino: {com_destino}; com viajantes: {com_viajantes}; "
                f"ligados a outro registro (parentRecordId): {com_pai}"
            )
            print(f"      status: {', '.join(status)}")

        contagem: dict[str, int] = {}

        for registro in registros:
            chave = (str(registro.get(campo_produto) or "(vazio)"), str(registro.get("type")))
            contagem[chave] = contagem.get(chave, 0) + 1

        print(f"\n  Produtos do Envision (campo '{campo_produto}'):")
        print("  Atenção: no FIT o nome do produto traz o nome do cliente; não compartilhe esta lista.")
        nao_classificados = 0

        for (produto, tipo_registro), quantidade in sorted(contagem.items()):
            c = classificar_produto(produto, mapeamentos)
            alerta = ""
            if c.tipo == "Não classificado":
                nao_classificados += 1
                alerta = "   <-- NÃO CLASSIFICADO"
            elif not c.mes:
                alerta = "   <-- sem MÊS ANO no fim"
            print(f"    [{quantidade}x] {produto}  [{tipo_registro}] -> {c.tipo}{alerta}")

        print(f"\n  Não classificados: {nao_classificados} de {len(contagem)} produto(s) diferentes.")
    except Exception as erro:
        falha("Envision", erro)


def nivel_4(email: str, historico_completo: bool) -> None:
    titulo("Nível 4 — perfil do cliente")
    print("  (consulta de verificação feita fora da ÁGORA: não fica no registro de consultas)\n")
    conectores = {}

    for nome, fabrica in (("rd", RDCRM), ("envision", Envision)):
        try:
            conectores[nome] = fabrica()
        except Exception as erro:
            conectores[nome] = None
            falha(fabrica.nome if hasattr(fabrica, "nome") else nome, erro)

    resultado = BuscadorPerfil(
        rd=conectores["rd"],
        envision=conectores["envision"],
    ).buscar(
        texto=email,
        historico_completo=historico_completo,
    )
    print(resumo_para_ia(resultado))


# Campos candidatos para filtrar por passageiro no criteria do Records/Query,
# derivados dos campos devolvidos pelo Envision (nível 2). O Envision não
# documenta os nomes; este teste descobre qual funciona.
CANDIDATOS_FILTRO_PASSAGEIRO = (
    "PassengerEmail",
    "PassengersList.PassengerEmail",
    "Passenger",
    "TravellerEmail",
    "Travellers.Email",
    "Email",
    "ContactInformations.Email",
)
_EMAIL_SEGURO = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


CANDIDATOS_FILTRO_TIPO = ("Type", "RecordType", "TypeName", "RecordTypeName")


def _emails_dos_passageiros(registro: dict) -> set[str]:
    emails = set()

    for passageiro in registro.get("passengersList") or []:
        email = str(passageiro.get("PassengerEmail") or "").strip().lower()

        if _EMAIL_SEGURO.match(email):
            emails.add(email)

    return emails


# Nomes vistos na busca do próprio site do Envision (07/10/2026):
# Filter "Person.FullName(Person):<nome>;" — pessoas pelo nome completo.
CANDIDATOS_FILTRO_NOME = (
    "Person.FullName = '{nome}'",
    "FullName = '{nome}'",
    "Type = 'Person' && FullName = '{nome}'",
    "Person.FullName.Contains('{nome}')",
    "FullName.Contains('{nome}')",
)
_NOME_SEGURO = re.compile(r"^[A-Za-zÀ-ÿ .-]{3,80}$")


def _contexto_da_conta(registros: list[dict]) -> dict | None:
    """travelAgencyId e systemAccountId, tirados dos próprios registros."""
    for registro in registros:
        agencia = registro.get("travelAgencyId")
        conta = registro.get("systemAccountId")

        if agencia and conta:
            return {"travelAgencyId": agencia, "systemAccountId": conta}

    return None


def nivel_5_cpf(cpf: str) -> None:
    titulo("Nível 5 — filtro no formato do programador (externalId)")
    automatico = cpf.strip().lower() == "auto"
    cpf = re.sub(r"\D", "", cpf)

    if not automatico and len(cpf) != 11:
        print("  CPF inválido (precisa ter 11 dígitos).")
        return

    print("  4 consultas (1 página cada). Só contagens; o CPF não é mostrado.\n")

    try:
        envision = Envision()
    except Exception as erro:
        falha("Envision", erro)
        return

    def consultar(criteria: str, info: dict | None) -> list[dict] | None:
        try:
            return envision.consultar_registros(criteria, maximo_paginas=1, info_adicional=info)
        except Exception as erro:
            motivo = str(erro) if isinstance(erro, (IntegracaoError, ChamadaBloqueadaError)) else type(erro).__name__
            print(f"    erro ({motivo})")
            return None

    def resumo(registros: list[dict]) -> str:
        tipos: dict[str, int] = {}
        for registro in registros:
            tipo = str(registro.get("type"))
            tipos[tipo] = tipos.get(tipo, 0) + 1
        return ", ".join(f"{tipo}: {n}" for tipo, n in sorted(tipos.items())) or "-"

    fim = datetime.now()
    inicio = fim - timedelta(days=30)
    janela = f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'"
    base = consultar(janela, {}) or []
    info_env = config.envision_contexto()
    info = info_env or _contexto_da_conta(base)
    origem_info = "do .env (programador)" if info_env else ("dos registros" if info else "NÃO encontradas")
    print(f"  Controle (últimos 30 dias): {len(base)} registro(s); agência/conta {origem_info}")

    if automatico:
        # Controle positivo: o externalId de um cadastro Person que existe
        # com certeza (vindo da consulta acima). Nunca é mostrado.
        cpfs = [
            re.sub(r"\D", "", str(r.get("externalId") or ""))
            for r in base
            if str(r.get("type")) == "Person"
        ]
        cpfs = [c for c in cpfs if len(c) == 11]

        if not cpfs:
            print("  Nenhum cadastro Person com CPF nos últimos 30 dias.")
            return

        cpf = cpfs[0]
        print("  Usando o CPF de um cadastro Person recente (controle positivo).")

    cpf_pontuado = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"

    for rotulo, criteria in (
        ("externalId só números (com additionalInfo)", f'externalId = "{cpf}"'),
        ("externalId com pontuação (com additionalInfo)", f'externalId = "{cpf_pontuado}"'),
        ("externalId com pontuação (sem additionalInfo)", f'externalId = "{cpf_pontuado}"'),
    ):
        usar_info = info if "com additionalInfo" in rotulo else {}
        registros = consultar(criteria, usar_info)

        if registros is None:
            continue

        extra = ""
        if "externalId" in rotulo and registros:
            associados = sum(len(r.get("associations") or []) for r in registros)
            tipos_associados = sorted({str(a.get("recordType")) for r in registros for a in r.get("associations") or []})
            viajante = sum(
                1 for r in registros
                if any(re.sub(r"\D", "", str(t.get("externalId") or "")) == cpf for t in r.get("travellers") or [])
            )
            extra = f"; associações: {associados} ({', '.join(tipos_associados) or '-'}); com esse CPF como viajante: {viajante}"

        print(f"  {rotulo}: {len(registros)} registro(s) — tipos {resumo(registros)}{extra}")


def _formato_valor(valor) -> str:
    """Descreve o formato de um valor sem mostrá-lo."""
    if valor in (None, ""):
        return "vazio"

    texto = str(valor).strip()
    digitos = re.sub(r"\D", "", texto)

    if _EMAIL_SEGURO.match(texto.lower()):
        return "parece e-mail"
    if _CPF.match(texto) and len(digitos) == 11:
        return "parece CPF (só números)" if texto.isdigit() else "parece CPF (com pontuação)"
    if texto.isdigit():
        return f"número com {len(texto)} dígitos"
    if re.fullmatch(r"[0-9a-fA-F-]{20,}", texto):
        return f"código hexadecimal com {len(texto)} caracteres"
    return f"texto com {len(texto)} caracteres"


def nivel_6() -> None:
    titulo("Nível 6 — formato do externalId (sem mostrar valores)")
    print("  1 consulta (últimos 30 dias, 1 página).\n")

    try:
        envision = Envision()
        fim = datetime.now()
        inicio = fim - timedelta(days=30)
        registros = envision.consultar_registros(
            f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'",
            maximo_paginas=1,
        )
    except Exception as erro:
        falha("Envision", erro)
        return

    contagem: dict[tuple[str, str, str], int] = {}

    for registro in registros:
        tipo = str(registro.get("type"))
        chave = (tipo, "externalId do registro", _formato_valor(registro.get("externalId")))
        contagem[chave] = contagem.get(chave, 0) + 1

        for viajante in registro.get("travellers") or []:
            chave = (tipo, "travellers[].externalId", _formato_valor(viajante.get("externalId")))
            contagem[chave] = contagem.get(chave, 0) + 1

            for documento in viajante.get("documents") or []:
                chave = (tipo, f"documento {str(documento.get('type'))[:20]}", _formato_valor(documento.get("value")))
                contagem[chave] = contagem.get(chave, 0) + 1

    for (tipo, campo, formato), quantidade in sorted(contagem.items()):
        print(f"  {tipo} — {campo}: {formato} ({quantidade}x)")


_DOZE_DIGITOS = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_EMAIL_TEXTO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _mascarar(texto: str) -> str:
    """Esconde CPFs e e-mails de um texto (mensagens do Envision)."""
    return _EMAIL_TEXTO.sub("***@***", _DOZE_DIGITOS.sub("***********", texto))


class _TransporteComRegistro(httpx.HTTPTransport):
    """Imprime cada requisição e resposta com os segredos escondidos."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        print(f"\n  >>> {request.method} {request.url.scheme}://{request.url.host}{request.url.path}")

        for nome, valor in request.headers.items():
            if nome.lower() == "authorization":
                valor = valor.split(" ")[0] + " ***" if " " in valor else "***"
            if nome.lower() in {"authorization", "content-type", "accept"}:
                print(f"      {nome}: {valor}")

        corpo = request.read().decode("utf-8", errors="replace")

        if request.url.path == "/token":
            campos = dict(item.split("=", 1) for item in corpo.split("&") if "=" in item)
            campos = {k: ("***" if k in {"password", "username", "client_id"} else v) for k, v in campos.items()}
            print(f"      corpo (form): {campos}")
        elif corpo:
            print(f"      corpo (json): {_mascarar(corpo)}")

        resposta = super().handle_request(request)
        resposta.read()
        print(f"  <<< {resposta.status_code}")

        try:
            dados = json.loads(resposta.content)
        except ValueError:
            return resposta

        if isinstance(dados, dict):
            resumo = {
                chave: dados.get(chave)
                for chave in ("successful", "errors", "messages", "warnings", "pagingPivotId")
                if chave in dados
            }
            if "records" in dados:
                resumo["records"] = f"{len(dados.get('records') or [])} registro(s)"
            if "access_token" in dados:
                resumo["access_token"] = "***"
                resumo["expires_in"] = dados.get("expires_in")
            if "session" in dados:
                resumo["session"] = "recebida" if dados.get("session") else "vazia"
            print(f"      resposta: {_mascarar(json.dumps(resumo, ensure_ascii=False))}")

        return resposta


def nivel_7() -> None:
    titulo("Nível 7 — requisições enviadas ao Envision (para o programador)")
    print("  Senha, usuário, token, CPFs e e-mails aparecem como ***.")

    try:
        envision = Envision(transporte=_TransporteComRegistro())
        fim = datetime.now()
        inicio = fim - timedelta(days=30)
        base = envision.consultar_registros(
            f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'",
            maximo_paginas=1,
        )
        cpfs = [
            re.sub(r"\D", "", str(r.get("externalId") or ""))
            for r in base
            if str(r.get("type")) == "Person"
        ]
        cpfs = [c for c in cpfs if len(c) == 11]

        if not cpfs:
            print("\n  Nenhum cadastro Person com CPF nos últimos 30 dias.")
            return

        print("\n  --- busca pelo externalId de um cadastro Person que aparece acima ---")
        envision.consultar_registros(f'externalId = "{cpfs[0]}"', maximo_paginas=1)
    except Exception as erro:
        falha("Envision", erro)


def nivel_8(nome: str) -> None:
    titulo("Nível 8 — abordagem do programador (resumo das ordens e busca de viajantes)")
    nome = " ".join(nome.split())

    if not _NOME_SEGURO.match(nome):
        print("  Nome inválido (use só letras e espaços).")
        return

    print("  2 consultas + login/sessão. Só contagens; o nome não é mostrado.\n")

    try:
        envision = Envision()
    except Exception as erro:
        falha("Envision", erro)
        return

    procurado = normalizar_texto(nome).split()

    def bate(texto) -> bool:
        palavras = set(normalizar_texto(texto).split())
        return bool(procurado) and all(p in palavras for p in procurado)

    try:
        resumos = envision.resumo_ordens()
        com_nome = 0
        for item in resumos:
            passageiros = item.get("passengers") or []
            nomes = [p if isinstance(p, str) else str((p or {}).get("fullName") or (p or {}).get("name") or "") for p in passageiros]
            if any(bate(n) for n in nomes) or bate(item.get("corporateCustomerName") or ""):
                com_nome += 1
        print(f"  Resumo das ordens (GetServiceOrderSummaries): {len(resumos)} ordem(ns) no total; {com_nome} com esse nome")
    except Exception as erro:
        falha("Resumo das ordens", erro)

    try:
        viajantes = envision.buscar_viajantes(nome)
        com_nome = sum(1 for v in viajantes if bate(v.get("name") or v.get("fullName") or ""))
        print(f"  Busca de viajantes (QueryTravellers): {len(viajantes)} viajante(s); {com_nome} com esse nome")
    except Exception as erro:
        falha("Busca de viajantes", erro)


def nivel_9() -> None:
    titulo("Nível 9 — cadastros duplicados e variações do filtro externalId")
    print("  Só contagens; nenhum CPF é mostrado.\n")

    try:
        envision = Envision()
    except Exception as erro:
        falha("Envision", erro)
        return

    def consultar(criteria: str, paginas: int = 1) -> list[dict] | None:
        try:
            return envision.consultar_registros(criteria, maximo_paginas=paginas)
        except Exception as erro:
            motivo = str(erro) if isinstance(erro, (IntegracaoError, ChamadaBloqueadaError)) else type(erro).__name__
            print(f"    erro ({motivo})")
            return None

    # 1. Cadastros Person dos últimos 90 dias (até 5 páginas) e CPFs repetidos.
    fim = datetime.now()
    inicio = fim - timedelta(days=90)
    registros = consultar(
        f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'",
        paginas=5,
    ) or []
    cpfs = [
        re.sub(r"\D", "", str(r.get("externalId") or ""))
        for r in registros
        if str(r.get("type")) == "Person"
    ]
    cpfs = [c for c in cpfs if len(c) == 11]
    contagem: dict[str, int] = {}
    for c in cpfs:
        contagem[c] = contagem.get(c, 0) + 1
    repetidos = sum(1 for n in contagem.values() if n > 1)
    print(f"  Cadastros Person com CPF (últimos 90 dias, até 5 páginas): {len(cpfs)}")
    print(f"  CPFs que aparecem em mais de um cadastro: {repetidos}")

    if not cpfs:
        return

    # 2. Variações do filtro com um CPF que com certeza existe.
    cpf = cpfs[0]
    print("\n  Busca do mesmo CPF (de um cadastro que existe):")
    for rotulo, criteria in (
        ("externalId com aspas simples", f"externalId = '{cpf}'"),
        ("ExternalId com aspas duplas", f'ExternalId = "{cpf}"'),
        ("ExternalId com aspas simples", f"ExternalId = '{cpf}'"),
    ):
        achados = consultar(criteria)
        if achados is None:
            continue
        tipos = sorted({str(r.get("type")) for r in achados})
        print(f"    {rotulo}: {len(achados)} registro(s), tipos {', '.join(tipos) or '-'}" + ("  <-- FUNCIONA" if achados else ""))


def nivel_10(cpf: str | None) -> None:
    titulo("Nível 10 — do cadastro (Person) até as viagens (ServiceOrder)")
    print("  Só contagens e nomes de status; nenhum dado de cliente é mostrado.\n")

    try:
        envision = Envision()
    except Exception as erro:
        falha("Envision", erro)
        return

    try:
        if cpf:
            cpf = re.sub(r"\D", "", cpf)
            if len(cpf) != 11:
                print("  CPF inválido (precisa ter 11 dígitos).")
                return
        else:
            fim = datetime.now()
            inicio = fim - timedelta(days=90)
            base = envision.consultar_registros(
                f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'",
                maximo_paginas=5,
            )
            cpfs = [re.sub(r"\D", "", str(r.get("externalId") or "")) for r in base if str(r.get("type")) == "Person"]
            cpfs = [c for c in cpfs if len(c) == 11]
            if not cpfs:
                print("  Nenhum cadastro Person com CPF nos últimos 90 dias.")
                return
            cpf = cpfs[0]
            print("  Usando o CPF de um cadastro Person recente.")

        pessoas = envision.consultar_registros(f'ExternalId = "{cpf}"', maximo_paginas=1)
        print(f"  Cadastros encontrados pelo ExternalId: {len(pessoas)}")

        if not pessoas:
            return

        associacoes = [a for p in pessoas for a in (p.get("associations") or [])]
        por_tipo: dict[str, list] = {}
        for a in associacoes:
            por_tipo.setdefault(str(a.get("recordType")), []).append(a)
        print(f"  Associações do cadastro: {len(associacoes)}")
        for tipo, itens in sorted(por_tipo.items()):
            ligacoes = sorted({str(a.get("type")) for a in itens})
            print(f"    {tipo}: {len(itens)} (tipo de ligação: {', '.join(ligacoes)})")

        ordens = [a for a in associacoes if str(a.get("recordType")) == "ServiceOrder" and a.get("recordAssociatedId")]
        for associacao in ordens[:3]:
            registro = envision.obter_registro(int(associacao["recordAssociatedId"]))
            filhos = registro.get("children") or []
            tipos_filhos: dict[str, int] = {}
            for filho in filhos:
                tipo = str(filho.get("type"))
                tipos_filhos[tipo] = tipos_filhos.get(tipo, 0) + 1
            print(
                "\n    Ordem de serviço: "
                f"status {((registro.get('status') or {}).get('name'))}; "
                f"valor {'sim' if (registro.get('totalValue') or {}).get('value') else 'não'}; "
                f"viajantes {len(registro.get('travellers') or [])}; "
                f"data de início {'sim' if registro.get('startDate') else 'não'}; "
                f"filhos {', '.join(f'{t}: {n}' for t, n in sorted(tipos_filhos.items())) or 'nenhum'}"
            )
            produto = next((f for f in filhos if str(f.get("type")) == "ItineraryBook"), None)
            if produto is not None:
                c = classificar_produto(produto.get("summary") or produto.get("name"), carregar_mapeamentos())
                print(f"      produto do ItineraryBook: tipo {c.tipo}, mês/ano {'sim' if c.mes else 'não'}")
            destinos = sum(1 for f in filhos if (f.get("destination") or {}).get("name"))
            print(f"      serviços com destino: {destinos}")

            # Datas de cada parte (a data da venda saiu igual a hoje no nível 4).
            def data_texto(valor) -> str:
                if not isinstance(valor, dict) or not valor.get("year"):
                    return "vazia"
                texto = f"{int(valor.get('day') or 0):02d}/{int(valor.get('month') or 0):02d}/{valor['year']}"
                return texto + (" (= hoje)" if texto == f"{datetime.now():%d/%m/%Y}" else "")

            print(f"      datas da venda: início {data_texto(registro.get('startDate'))}; fim {data_texto(registro.get('endDate'))}; criada {data_texto(registro.get('createdTime'))}")
            if produto is not None:
                print(f"      datas do ItineraryBook: início {data_texto(produto.get('startDate'))}; fim {data_texto(produto.get('endDate'))}")
            inicios = sorted(
                (
                    (int(f["startDate"]["year"]), int(f["startDate"].get("month") or 0), int(f["startDate"].get("day") or 0))
                    for f in filhos
                    if str(f.get("type")) == "ServiceBook" and isinstance(f.get("startDate"), dict) and f["startDate"].get("year")
                )
            )
            if inicios:
                menor, maior = inicios[0], inicios[-1]
                print(f"      início dos serviços: de {menor[2]:02d}/{menor[1]:02d}/{menor[0]} a {maior[2]:02d}/{maior[1]:02d}/{maior[0]} ({len(inicios)} com data)")
            else:
                print("      início dos serviços: nenhum com data")
    except Exception as erro:
        falha("Envision", erro)


CANDIDATOS_NOME_VIAJANTE = (
    "TravellerName",
    "PassengerName",
    "Passenger",
    "Traveller",
    "TravellerFullName",
    "FullName",
)
CANDIDATOS_DOCUMENTO_VIAJANTE = (
    "TravellerDocument",
    "DocumentNumber",
    "Document",
    "Cpf",
    "TravellerCpf",
    "PassengerDocument",
)


def nivel_11() -> None:
    titulo("Nível 11 — campo do filtro para achar vendas pelo viajante (controle positivo)")
    print("  Usa o nome e o CPF de um viajante de uma venda recente (sem mostrar).\n")

    try:
        envision = Envision()
        fim = datetime.now()
        inicio = fim - timedelta(days=30)
        base = envision.consultar_registros(
            f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'",
            maximo_paginas=3,
        )
    except Exception as erro:
        falha("Envision", erro)
        return

    alvo = None
    for registro in base:
        if str(registro.get("type")) not in {"ServiceOrder", "ServiceBook"}:
            continue
        for viajante in registro.get("travellers") or []:
            nome = str(viajante.get("fullName") or "").strip()
            cpf = next(
                (str(d.get("value") or "") for d in viajante.get("documents") or [] if "CPF" in normalizar_texto(d.get("type"))),
                "",
            )
            if nome and len(re.sub(r"\D", "", cpf)) == 11:
                alvo = (registro, nome, cpf.strip())
                break
        if alvo:
            break

    if not alvo:
        print("  Nenhuma venda recente com viajante e CPF para usar como controle.")
        return

    registro, nome, cpf = alvo
    print(f"  Venda de controle: tipo {registro.get('type')}")

    def testar(rotulo: str, criteria: str, procurado: str) -> None:
        try:
            achados = envision.consultar_registros(criteria, maximo_paginas=1)
        except Exception as erro:
            motivo = str(erro) if isinstance(erro, (IntegracaoError, ChamadaBloqueadaError)) else type(erro).__name__
            print(f"    {rotulo}: erro ({motivo})")
            return
        com_valor = sum(1 for r in achados if procurado in normalizar_texto(json.dumps(r, ensure_ascii=False)))
        mesma = any(r.get("id") == registro.get("id") for r in achados)
        if achados and com_valor == len(achados):
            situacao = f"FUNCIONA — {len(achados)} registro(s)" + (", inclui a venda de controle" if mesma else "")
        elif achados:
            situacao = f"ignorado — {len(achados)} registro(s), só {com_valor} com o valor"
        else:
            situacao = "0 registros"
        print(f"    {rotulo}: {situacao}")

    print("\n  Pelo nome do viajante:")
    for campo in CANDIDATOS_NOME_VIAJANTE:
        testar(campo, f"{campo} = '{nome}'", normalizar_texto(nome))

    print("\n  Pelo CPF do viajante (no formato em que está gravado):")
    for campo in CANDIDATOS_DOCUMENTO_VIAJANTE:
        testar(campo, f"{campo} = '{cpf}'", normalizar_texto(cpf))


def nivel_12(email: str) -> None:
    titulo("Nível 12 — onde estão as negociações do cliente no RD")
    print("  Só contagens e nomes de funis; nenhum dado de cliente é mostrado.\n")

    try:
        rd = RDCRM()
    except Exception as erro:
        falha("RD Station CRM", erro)
        return

    campo_cpf = carregar_mapeamentos()["rd_campos"].get("contato_cpf_custom_field_id")

    def cpf_do_contato(contato: dict) -> str:
        for campo in contato.get("contact_custom_fields") or []:
            if campo.get("custom_field_id") == campo_cpf:
                return re.sub(r"\D", "", str(campo.get("value") or ""))
        return ""

    def ids_negociacoes(contato: dict) -> list[str]:
        return [str(d.get("_id") or d.get("id")) for d in contato.get("deals") or [] if d.get("_id") or d.get("id")]

    def funis(ids: list[str]) -> str:
        nomes: dict[str, int] = {}
        for negociacao_id in ids[:30]:
            try:
                funil = str((rd.obter_negociacao(negociacao_id).get("deal_pipeline") or {}).get("name") or "?")
            except Exception:
                funil = "erro ao ler"
            nomes[funil] = nomes.get(funil, 0) + 1
        return ", ".join(f"{nome}: {n}" for nome, n in sorted(nomes.items())) or "nenhuma"

    try:
        # Hipótese 1: a busca traz a lista de negociações incompleta.
        contatos = rd.buscar_contatos(email=email)
        print(f"  Contatos com esse e-mail: {len(contatos)}")
        if not contatos:
            return

        principal = contatos[0]
        cpf = cpf_do_contato(principal)
        na_busca = ids_negociacoes(principal)
        ficha = rd.obter_contato(str(principal.get("id") or principal.get("_id")))
        na_ficha = ids_negociacoes(ficha)
        print(f"  Negociações na busca: {len(na_busca)}; na ficha do contato: {len(na_ficha)}")
        print(f"    funis: {funis(sorted(set(na_busca) | set(na_ficha)))}")

        # Hipótese 2: a venda está em outro contato da mesma pessoa.
        nome = str(principal.get("name") or "")
        if not nome.strip():
            return
        mesmos = [
            c for c in rd.buscar_contatos(nome=nome)
            if str(c.get("id") or c.get("_id")) != str(principal.get("id") or principal.get("_id"))
            and normalizar_texto(c.get("name")) == normalizar_texto(nome)
        ]
        print(f"\n  Outros contatos com o mesmo nome: {len(mesmos)}")
        def emails(contato: dict) -> set[str]:
            return {str(e.get("email") or "").strip().lower() for e in contato.get("emails") or [] if e.get("email")}

        def telefones(contato: dict) -> set[str]:
            # Só os 8 últimos dígitos (ignora DDI, DDD e o 9 extra).
            return {re.sub(r"\D", "", str(t.get("phone") or ""))[-8:] for t in contato.get("phones") or [] if t.get("phone")} - {""}

        def compara(a: set, b: set) -> str:
            return "sem dado" if not a or not b else ("sim" if a & b else "não")

        for contato in mesmos:
            outro_cpf = cpf_do_contato(contato)
            mesmo_cpf = "sim" if cpf and outro_cpf == cpf else ("sem CPF" if not outro_cpf else "não")
            ids = ids_negociacoes(contato)
            print(
                f"    contato: mesmo CPF {mesmo_cpf}; mesmo e-mail {compara(emails(principal), emails(contato))}; "
                f"mesmo telefone {compara(telefones(principal), telefones(contato))}; negociações {len(ids)} ({funis(ids)})"
            )
    except Exception as erro:
        falha("RD Station CRM", erro)


def nivel_5_nome(nome: str) -> None:
    titulo("Nível 5 — filtro por nome (campos vistos no site do Envision)")
    nome = " ".join(nome.split())

    if not _NOME_SEGURO.match(nome):
        print("  Nome inválido (use só letras e espaços).")
        return

    print(f"  {len(CANDIDATOS_FILTRO_NOME)} consultas (1 página cada). Só contagens; nenhum dado é mostrado.\n")

    try:
        envision = Envision()
    except Exception as erro:
        falha("Envision", erro)
        return

    procurado = normalizar_texto(nome)

    for modelo in CANDIDATOS_FILTRO_NOME:
        criteria = modelo.format(nome=nome)
        rotulo = modelo.replace("{nome}", "NOME")

        try:
            registros = envision.consultar_registros(criteria, maximo_paginas=1)
        except Exception as erro:
            motivo = str(erro) if isinstance(erro, (IntegracaoError, ChamadaBloqueadaError)) else type(erro).__name__
            print(f"  {rotulo}: erro ({motivo})")
            continue

        com_nome = sum(1 for r in registros if procurado in normalizar_texto(json.dumps(r, ensure_ascii=False)))
        tipos = sorted({str(r.get("type")) for r in registros})

        if not registros:
            situacao = "0 registros"
        elif com_nome == len(registros):
            situacao = f"FUNCIONA — {len(registros)} registro(s), todos com esse nome; tipos {', '.join(tipos)}"
        else:
            situacao = f"ignorado — {len(registros)} registro(s), só {com_nome} com esse nome"

        print(f"  {rotulo}: {situacao}")


def nivel_5(email: str | None) -> None:
    titulo("Nível 5 — descobrir os filtros do Envision")
    print("  Só contagens; nenhum dado de cliente é mostrado (nem o e-mail usado no teste).\n")

    try:
        envision = Envision()
    except Exception as erro:
        falha("Envision", erro)
        return

    fim = datetime.now()
    inicio = fim - timedelta(days=30)
    janela = f"Created >= '{inicio:%Y-%m-%d %H:%M:%S}' && Created <= '{fim:%Y-%m-%d %H:%M:%S}'"

    def consultar(criteria: str) -> list[dict] | None:
        try:
            return envision.consultar_registros(criteria, maximo_paginas=1)
        except Exception as erro:
            motivo = str(erro) if isinstance(erro, (IntegracaoError, ChamadaBloqueadaError)) else type(erro).__name__
            print(f"    erro ({motivo})")
            return None

    # 1. Controle: registros dos últimos 30 dias (filtro por data, que funciona).
    base = consultar(janela) or []
    tipos_base = sorted({str(r.get("type")) for r in base})
    print(f"  Controle — últimos 30 dias: {len(base)} registro(s), tipos {', '.join(tipos_base) or '-'}")

    # 2. Filtro de serviço citado no e-mail do Envision.
    issue = consultar(f"({janela}) && ServiceTypeName = 'Issue'")
    if issue is not None:
        tipos = sorted({str(r.get("type")) for r in issue})
        print(f"  ServiceTypeName = 'Issue': {len(issue)} registro(s), tipos {', '.join(tipos) or '-'}")

    # 3. Nome do campo de tipo de registro.
    for campo in CANDIDATOS_FILTRO_TIPO:
        registros = consultar(f"({janela}) && {campo} = 'ServiceOrder'")
        if registros is None:
            continue
        tipos = sorted({str(r.get("type")) for r in registros})
        funciona = bool(registros) and tipos == ["ServiceOrder"]
        print(f"  {campo} = 'ServiceOrder': {len(registros)} registro(s), tipos {', '.join(tipos) or '-'}" + ("  <-- FUNCIONA" if funciona else ""))

    # 4. Campo de passageiro, com um e-mail real tirado dos próprios registros
    #    (ou o informado), sem mostrar o e-mail.
    if email:
        email = email.strip().lower()
        if not _EMAIL_SEGURO.match(email):
            print("  E-mail inválido.")
            return
    else:
        contagem: dict[str, int] = {}
        for registro in base:
            for item in _emails_dos_passageiros(registro):
                contagem[item] = contagem.get(item, 0) + 1
        if not contagem:
            print("  Nenhum e-mail de passageiro nos registros dos últimos 30 dias; rode com --email de um passageiro.")
            return
        # O que aparece em mais registros: controle mais confiável.
        email = sorted(contagem, key=lambda item: (-contagem[item], item))[0]

    esperado = sum(1 for r in base if email in _emails_dos_passageiros(r))
    print(f"\n  Teste de passageiro (e-mail aparece em {esperado} registro(s) do controle):")

    for campo in CANDIDATOS_FILTRO_PASSAGEIRO:
        registros = consultar(f"{campo} = '{email}'")
        if registros is None:
            continue
        com_email = sum(1 for r in registros if email in json.dumps(r, ensure_ascii=False).lower())
        if not registros:
            situacao = "0 registros"
        elif com_email == len(registros):
            situacao = f"FUNCIONA — {len(registros)} registro(s), todos desse passageiro"
        else:
            situacao = f"ignorado — {len(registros)} registro(s), só {com_email} desse passageiro"
        print(f"  {campo}: {situacao}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Verifica as integrações (somente leitura).")
    parser.add_argument("--nivel", type=int, choices=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], required=True)
    parser.add_argument("--limite", type=int, default=3, help="quantidade de registros nos níveis 2 e 3")
    parser.add_argument("--email", help="e-mail do cliente (níveis 4 e 5)")
    parser.add_argument("--historico-completo", action="store_true", help="nível 4: todo o histórico")
    parser.add_argument("--nome", help="nível 5: nome completo de um passageiro, como está no Envision")
    parser.add_argument("--cpf", help="nível 5: CPF de um passageiro (não é mostrado) ou auto")
    args = parser.parse_args(argv)
    limite = max(1, min(args.limite, 500))

    if args.nivel == 1:
        nivel_1()
    elif args.nivel == 2:
        nivel_2(limite)
    elif args.nivel == 3:
        nivel_3(limite)
    elif args.nivel == 6:
        nivel_6()
    elif args.nivel == 7:
        nivel_7()
    elif args.nivel == 9:
        nivel_9()
    elif args.nivel == 10:
        nivel_10(args.cpf)
    elif args.nivel == 11:
        nivel_11()
    elif args.nivel == 12:
        if not args.email:
            parser.error("o nível 12 precisa de --email")
        nivel_12(args.email.strip())
    elif args.nivel == 8:
        if not args.nome:
            parser.error("o nível 8 precisa de --nome")
        nivel_8(args.nome)
    elif args.nivel == 4:
        if not args.email:
            parser.error("o nível 4 precisa de --email")
        nivel_4(args.email, args.historico_completo)
    elif args.cpf:
        nivel_5_cpf(args.cpf)
    elif args.nome:
        nivel_5_nome(args.nome)
    else:
        nivel_5(args.email)


if __name__ == "__main__":
    main()

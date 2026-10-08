"""Perfil do cliente a partir do RD Station CRM e do Envision (camada 2).

Segue docs/regras-integracao-clientes.md. Todas as contas são feitas aqui,
nunca pela IA. O resultado é montado por INCLUSÃO: só os campos da lista
permitida (regra 6) são copiados para o perfil; CPF, documentos, endereço,
telefone, nascimento, pagamentos e anotações nunca saem desta camada. O CPF
é usado só para cruzar os sistemas, dentro das estruturas internas.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from integracoes.erros import IntegracaoError

MAPEAMENTOS_FILE = Path(__file__).resolve().parents[1] / "integracoes" / "mapeamentos.json"

RD = "RD Station CRM"
ENVISION = "Envision"
NAO_INFORMADO = "não informado"
MES_DA_VIAGEM = "mês da viagem"
MAXIMO_NEGOCIACOES = 30


def carregar_mapeamentos() -> dict:
    return json.loads(MAPEAMENTOS_FILE.read_text(encoding="utf-8"))


# --- Normalização ---------------------------------------------------------------
def normalizar_texto(texto: str | None) -> str:
    """Maiúsculas, sem acentos e com espaços simples."""
    sem_acento = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.upper().split())


def normalizar_cpf(cpf: str | None) -> str | None:
    digitos = re.sub(r"\D", "", str(cpf or ""))
    return digitos if len(digitos) == 11 else None


def normalizar_email(email: str | None) -> str | None:
    valor = "".join(str(email or "").split()).lower()
    return valor if "@" in valor else None


def _data(valor) -> date | None:
    """DateValues do Envision ({year, month, day}) ou texto ISO do RD."""
    if isinstance(valor, dict):
        try:
            return date(int(valor["year"]), int(valor.get("month") or 1), int(valor.get("day") or 1))
        except (KeyError, TypeError, ValueError):
            return None

    if isinstance(valor, str) and valor:
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00")[:19]).date()
        except ValueError:
            try:
                return date.fromisoformat(valor[:10])
            except ValueError:
                return None

    return None


def _um_ano_antes(dia: date) -> date:
    try:
        return dia.replace(year=dia.year - 1)
    except ValueError:  # 29/02
        return dia.replace(year=dia.year - 1, day=28)


# --- Classificação ------------------------------------------------------------------
@dataclass(frozen=True)
class ClassificacaoProduto:
    tipo: str
    mes: str | None
    ano: str | None
    destino: str | None


def classificar_produto(
    nome_produto: str | None,
    mapeamentos: dict,
) -> ClassificacaoProduto:
    """Regra 4 (Envision): tipo pela 1ª palavra; mês e ano pelas 2 últimas."""
    palavras = normalizar_texto(nome_produto).split()

    if not palavras:
        return ClassificacaoProduto("Não classificado", None, None, None)

    tipos = {normalizar_texto(k): v for k, v in mapeamentos["envision_tipos_por_primeira_palavra"].items()}
    tipo = tipos.get(palavras[0], "Não classificado")
    meses = [normalizar_texto(m) for m in mapeamentos["meses"]]
    mes = ano = None
    fim = len(palavras)

    if len(palavras) >= 3 and palavras[-2] in meses and re.fullmatch(r"\d{2}", palavras[-1]):
        mes, ano = palavras[-2], palavras[-1]
        fim = len(palavras) - 2

    destino = None

    # Destino pelo nome só como reserva e nunca no FIT (o nome traz o cliente).
    if tipo not in {"FIT", "Não classificado"} and fim > 1:
        destino = " ".join(palavras[1:fim]) or None

    return ClassificacaoProduto(tipo, mes, ano, destino)


def classificar_funil(
    nome_funil: str | None,
    mapeamentos: dict,
) -> str:
    """Regra 4 (RD): tipo pelo funil; funil não listado = Outros."""
    tabela = {normalizar_texto(k): v for k, v in mapeamentos["rd_funis"].items()}
    return tabela.get(normalizar_texto(nome_funil), mapeamentos["rd_funil_nao_listado"])


def funil_oculto(
    nome_funil: str | None,
    mapeamentos: dict,
) -> bool:
    """Funis que não são negociação de venda (ex.: Formulários) ficam fora do perfil."""
    return normalizar_texto(nome_funil) in {normalizar_texto(f) for f in mapeamentos.get("rd_funis_ocultos") or []}


# --- Estruturas do perfil (só campos permitidos) ------------------------------------
@dataclass
class Viagem:
    produto: str
    tipo: str
    destino: str
    data: date | None
    data_origem: str  # MES_DA_VIAGEM, "data da venda" ou não informado
    valor: float | None
    moeda: str | None
    pessoas: int | None
    categoria: str  # Single, Double ou não informado
    cancelada: bool


@dataclass
class Negociacao:
    funil: str
    tipo: str
    etapa: str
    status: str  # aberta, ganha ou perdida
    responsavel: str
    criada_em: date | None
    fechada_em: date | None
    previsao: date | None
    destino: str
    valor: float | None


@dataclass
class ResumoMoeda:
    moeda: str
    viagens: int
    total: float

    @property
    def ticket_medio(self) -> float:
        return self.total / self.viagens


@dataclass
class PerfilCliente:
    nome: str
    email: str | None
    encontrado_em: list[str]
    nao_encontrado_em: list[str]
    periodo_inicio: date | None  # None = histórico completo
    periodo_fim: date
    viagens: list[Viagem]  # do período, sem canceladas
    viagens_futuras: list[Viagem]
    viagens_sem_data: list[Viagem]
    canceladas: int
    sem_valor: int
    resumo_por_moeda: list[ResumoMoeda]
    single: int
    double: int
    pessoas_nao_informadas: int
    nao_classificadas: int
    negociacoes: list[Negociacao]
    conflitos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    # Motivo quando o Envision não pôde ser consultado (sem CPF, sem cadastro
    # do formulário ou erro): as viagens ficam "não consultadas", não zeradas.
    viagens_indisponiveis: str | None = None


@dataclass
class Candidato:
    chave: str  # identificador opaco para a escolha (nunca contém CPF)
    nome: str
    email: str | None
    sistemas: list[str]
    possivel_duplicado: bool = False  # mesmo nome e telefone de outro da lista


@dataclass
class ResultadoBusca:
    situacao: str  # perfil, candidatos, nao_encontrado ou erro
    perfil: PerfilCliente | None = None
    candidatos: list[Candidato] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


# --- Conversão dos registros (cópia SÓ do permitido) ---------------------------------
def _campo(registro: dict, caminho: str | None):
    """Valor de um campo, aceitando caminhos como "destination.name"."""
    if not caminho:
        return None

    valor = registro

    for parte in caminho.split("."):
        if not isinstance(valor, dict):
            return None
        valor = valor.get(parte)

    return valor


def viagem_de_registro(
    registro: dict,
    mapeamentos: dict,
) -> Viagem:
    campos = mapeamentos["envision_campos"]
    filhos = [f for f in registro.get("children") or [] if isinstance(f, dict)]
    # Na venda (ServiceOrder), o nome do produto fica no filho ItineraryBook
    # (nível 10 do script, 08/10/2026).
    itinerario = next((f for f in filhos if str(f.get("type")) == "ItineraryBook"), None)
    produto = ""

    if itinerario is not None:
        produto = str(itinerario.get("summary") or itinerario.get("name") or "").strip()

    produto = produto or str(_campo(registro, campos["campo_produto"]) or "").strip()
    classificacao = classificar_produto(produto, mapeamentos)

    # Destino: do itinerário, da venda ou o mais comum entre os serviços.
    destino_estruturado = (
        (_campo(itinerario, campos.get("campo_destino")) if itinerario else None)
        or _campo(registro, campos.get("campo_destino"))
    )

    if not destino_estruturado:
        contagem: dict[str, int] = {}

        for filho in filhos:
            nome = _campo(filho, campos.get("campo_destino"))
            if nome:
                contagem[str(nome)] = contagem.get(str(nome), 0) + 1

        destino_estruturado = max(contagem, key=contagem.get) if contagem else None

    destino = str(destino_estruturado or classificacao.destino or NAO_INFORMADO)

    # O Envision não guarda a data da viagem: startDate/endDate vêm com o dia
    # da consulta (nível 10, 08/10/2026). A data da viagem sai do mês/ano do
    # nome do produto ("GRP JAPAO LF MAI 27"); sem eles, a data da venda.
    data_viagem = None

    if classificacao.mes and classificacao.ano:
        meses = [normalizar_texto(m) for m in mapeamentos["meses"]]
        data_viagem = date(2000 + int(classificacao.ano), meses.index(classificacao.mes) + 1, 1)

    data_venda = _data(registro.get("createdTime"))
    data, origem = (
        (data_viagem, MES_DA_VIAGEM) if data_viagem
        else (data_venda, "data da venda") if data_venda
        else (None, NAO_INFORMADO)
    )

    total = registro.get("totalValue") or {}
    valor = total.get("value")
    viajantes = registro.get("travellers")
    pessoas = len(viajantes) if isinstance(viajantes, list) and viajantes else None
    status = normalizar_texto((registro.get("status") or {}).get("name") or (registro.get("status") or {}).get("code"))
    cancelados = {normalizar_texto(s) for s in mapeamentos["envision_status_cancelado"]}

    return Viagem(
        produto=produto or NAO_INFORMADO,
        tipo=classificacao.tipo,
        destino=destino,
        data=data,
        data_origem=origem,
        valor=float(valor) if isinstance(valor, (int, float)) else None,
        moeda=(total.get("currencyCode") or None),
        pessoas=pessoas,
        categoria=NAO_INFORMADO if pessoas is None else ("Single" if pessoas == 1 else "Double"),
        cancelada=status in cancelados,
    )


def negociacao_de_deal(
    deal: dict,
    mapeamentos: dict,
) -> Negociacao:
    funil = str((deal.get("deal_pipeline") or {}).get("name") or NAO_INFORMADO)
    win = deal.get("win")
    status = "ganha" if win is True else "perdida" if win is False else "aberta"
    destino = None
    campo_destino = mapeamentos["rd_campos"].get("negociacao_destino_custom_field_id")

    if campo_destino:
        for campo in deal.get("deal_custom_fields") or []:
            if campo.get("custom_field_id") == campo_destino and campo.get("value"):
                destino = str(campo["value"])

    valor = deal.get("amount_total")

    return Negociacao(
        funil=funil,
        tipo=classificar_funil(funil, mapeamentos),
        etapa=str((deal.get("deal_stage") or {}).get("name") or NAO_INFORMADO),
        status=status,
        responsavel=str((deal.get("user") or {}).get("name") or NAO_INFORMADO),
        criada_em=_data(deal.get("created_at")),
        fechada_em=_data(deal.get("closed_at")),
        previsao=_data(deal.get("prediction_date")),
        destino=destino or NAO_INFORMADO,
        valor=float(valor) if isinstance(valor, (int, float)) else None,
    )


# --- Contas (regra 3) ---------------------------------------------------------------
def calcular_viagens(
    viagens: list[Viagem],
    hoje: date,
    historico_completo: bool,
) -> dict:
    inicio = None if historico_completo else _um_ano_antes(hoje)
    no_periodo, futuras, sem_data = [], [], []
    canceladas = 0

    for viagem in viagens:
        if viagem.cancelada:
            canceladas += 1
            continue

        if viagem.data is None:
            sem_data.append(viagem)
        elif viagem.data > hoje:
            futuras.append(viagem)
        elif inicio is None or viagem.data >= inicio:
            no_periodo.append(viagem)

    por_moeda: dict[str, ResumoMoeda] = {}
    sem_valor = 0

    for viagem in no_periodo:
        if viagem.valor is None or not viagem.moeda:
            sem_valor += 1
            continue

        resumo = por_moeda.setdefault(viagem.moeda, ResumoMoeda(viagem.moeda, 0, 0.0))
        resumo.viagens += 1
        resumo.total += viagem.valor

    return {
        "inicio": inicio,
        "no_periodo": sorted(no_periodo, key=lambda v: v.data, reverse=True),
        "futuras": sorted(futuras, key=lambda v: v.data),
        "sem_data": sem_data,
        "canceladas": canceladas,
        "sem_valor": sem_valor,
        "por_moeda": sorted(por_moeda.values(), key=lambda r: r.moeda),
        "single": sum(1 for v in no_periodo if v.categoria == "Single"),
        "double": sum(1 for v in no_periodo if v.categoria == "Double"),
        "pessoas_nao_informadas": sum(1 for v in no_periodo if v.categoria == NAO_INFORMADO),
        "nao_classificadas": sum(1 for v in no_periodo if v.tipo == "Não classificado"),
    }


# --- Identificação (regra 1) --------------------------------------------------------
@dataclass
class _PessoaRD:
    contato_id: str
    nome: str
    email: str | None
    cpf: str | None
    negociacao_ids: list[str]
    # Só para o aviso de possível duplicidade; nunca sai desta camada.
    telefones: frozenset[str] = frozenset()


def _telefones(contato: dict) -> frozenset[str]:
    """Últimos 8 dígitos de cada telefone (ignora DDI, DDD e o 9 extra)."""
    finais = {re.sub(r"\D", "", str(t.get("phone") or ""))[-8:] for t in contato.get("phones") or []}
    return frozenset(f for f in finais if len(f) == 8)


def _possivel_duplicado(a: "_PessoaRD", b: "_PessoaRD") -> bool:
    """Mesmo nome exato e mesmo telefone. O e-mail é a única chave que não se
    repete (Isabelle, 08/10/2026): aqui é só aviso, nunca junta os cadastros."""
    return (
        a.contato_id != b.contato_id
        and normalizar_texto(a.nome) == normalizar_texto(b.nome)
        and bool(a.telefones & b.telefones)
    )


@dataclass
class _PessoaEnvision:
    nome: str | None
    registros: list[dict]


SEM_CPF_NO_RD = f"Sem CPF no cadastro do {RD}: as viagens do {ENVISION} não foram consultadas."
SEM_CADASTRO_ENVISION = (
    f"Cliente sem cadastro do formulário de viajantes no {ENVISION}: "
    "as viagens não podem ser consultadas pela ÁGORA."
)
ENVISION_FORA = f"{ENVISION} não configurado na ÁGORA: as viagens não foram consultadas."


def _tokens_batem(busca: str, nome: str) -> bool:
    tokens = normalizar_texto(busca).split()
    nome_tokens = normalizar_texto(nome).split()
    return bool(tokens) and all(token in nome_tokens for token in tokens)


def _cpf_do_viajante(viajante: dict) -> str | None:
    for documento in viajante.get("documents") or []:
        tipo = normalizar_texto(documento.get("type") or documento.get("documentType"))

        if "CPF" in tipo:
            return normalizar_cpf(documento.get("number") or documento.get("value"))

    return None


def _chave_opaca(prefixo: str, valor: str) -> str:
    return f"{prefixo}-" + hashlib.sha256(valor.encode()).hexdigest()[:10]


class BuscadorPerfil:
    """Acha o cliente no RD (nome ou e-mail) e, pelo CPF do RD, as viagens no
    Envision (cadastro do formulário de viajantes → vendas)."""

    def __init__(
        self,
        rd=None,
        envision=None,
        mapeamentos: dict | None = None,
        hoje: date | None = None,
    ) -> None:
        self.rd = rd
        self.envision = envision
        self.mapeamentos = mapeamentos or carregar_mapeamentos()
        self.hoje = hoje or date.today()

    # RD
    def _pessoas_rd(self, busca: str, avisos: list[str]) -> list[_PessoaRD] | None:
        """Contatos do RD; None se o RD não pôde ser consultado."""
        if self.rd is None:
            avisos.append(f"{RD}: integração não configurada.")
            return None

        email = normalizar_email(busca)

        try:
            contatos = self.rd.buscar_contatos(email=email) if email else self.rd.buscar_contatos(nome=busca)
        except IntegracaoError as erro:
            avisos.append(str(erro))
            return None

        campo_cpf = self.mapeamentos["rd_campos"].get("contato_cpf_custom_field_id")
        pessoas = []

        for contato in contatos:
            nome = str(contato.get("name") or "")
            emails = [normalizar_email(e.get("email")) for e in contato.get("emails") or []]
            emails = [e for e in emails if e]

            if email and email not in emails:
                continue

            if not email and not _tokens_batem(busca, nome):
                continue

            cpf = None

            if campo_cpf:
                for campo in contato.get("contact_custom_fields") or []:
                    if campo.get("custom_field_id") == campo_cpf:
                        cpf = normalizar_cpf(campo.get("value"))

            pessoas.append(
                _PessoaRD(
                    contato_id=str(contato.get("id") or contato.get("_id")),
                    nome=nome,
                    email=emails[0] if emails else None,
                    cpf=cpf,
                    negociacao_ids=[str(d.get("_id") or d.get("id")) for d in contato.get("deals") or [] if d.get("_id") or d.get("id")],
                    telefones=_telefones(contato),
                )
            )

        return pessoas

    def _duplicados_de(self, pessoa: _PessoaRD, encontrados: list[_PessoaRD], por_email: bool) -> list[_PessoaRD]:
        """Outros contatos com o mesmo nome e telefone. Na busca por e-mail,
        procura pelo nome (o duplicado costuma ter outro e-mail)."""
        if por_email:
            encontrados = self._pessoas_rd(pessoa.nome, []) or []

        return [outro for outro in encontrados if _possivel_duplicado(pessoa, outro)]

    # Envision
    def _viagens_envision(self, pessoa: _PessoaRD) -> tuple[_PessoaEnvision | None, str | None]:
        """Vendas do cliente no Envision pelo CPF do RD. Devolve (vendas,
        motivo); o motivo explica por que as viagens não foram consultadas."""
        if self.envision is None:
            return None, ENVISION_FORA

        if not pessoa.cpf:
            return None, SEM_CPF_NO_RD

        try:
            tem_cadastro, registros = self.envision.vendas_por_cpf(pessoa.cpf)
        except IntegracaoError as erro:
            return None, f"{erro} As viagens não foram consultadas."

        if not tem_cadastro:
            return None, SEM_CADASTRO_ENVISION

        nome = next(
            (
                str(viajante.get("fullName") or "")
                for registro in registros
                for viajante in registro.get("travellers") or []
                if _cpf_do_viajante(viajante) == pessoa.cpf and viajante.get("fullName")
            ),
            None,
        )
        return _PessoaEnvision(nome=nome, registros=registros), None

    def buscar(
        self,
        texto: str,
        historico_completo: bool = False,
        escolha: str | None = None,
    ) -> ResultadoBusca:
        avisos: list[str] = []
        pessoas = self._pessoas_rd(texto, avisos)

        if pessoas is None:
            return ResultadoBusca(situacao="erro", avisos=avisos)

        encontrados = list(pessoas)

        if escolha:
            pessoas = [p for p in pessoas if _chave_opaca("rd", p.contato_id) == escolha]

        if not pessoas:
            return ResultadoBusca(situacao="nao_encontrado", avisos=avisos)

        if len(pessoas) > 1:
            # Nunca escolher sozinha (regra 1). O Envision só é consultado
            # depois da escolha (cada consulta tem custo).
            return ResultadoBusca(
                situacao="candidatos",
                candidatos=[
                    Candidato(
                        chave=_chave_opaca("rd", p.contato_id),
                        nome=p.nome,
                        email=p.email,
                        sistemas=[RD],
                        possivel_duplicado=any(_possivel_duplicado(p, outro) for outro in pessoas),
                    )
                    for p in pessoas
                ],
                avisos=avisos,
            )

        for outro in self._duplicados_de(pessoas[0], encontrados, por_email=bool(normalizar_email(texto))):
            avisos.append(
                f"Possível cadastro duplicado no {RD}: outro contato com o mesmo nome e telefone "
                f"({outro.email or 'sem e-mail'}; {len(outro.negociacao_ids)} negociação(ões)). "
                "As negociações dele não entraram neste perfil; para vê-las, consulte pelo e-mail dele"
                + (" ou pelo nome, escolhendo o outro contato." if not outro.email else ".")
            )

        return ResultadoBusca(
            situacao="perfil",
            perfil=self._montar_perfil(pessoas[0], historico_completo, avisos),
            avisos=avisos,
        )

    def _montar_perfil(self, rd: _PessoaRD, historico_completo: bool, avisos: list[str]) -> PerfilCliente:
        conflitos: list[str] = []
        negociacoes: list[Negociacao] = []

        for negociacao_id in rd.negociacao_ids[:MAXIMO_NEGOCIACOES]:
            try:
                negociacao = negociacao_de_deal(self.rd.obter_negociacao(negociacao_id), self.mapeamentos)
            except IntegracaoError as erro:
                avisos.append(str(erro))
                break

            if not funil_oculto(negociacao.funil, self.mapeamentos):
                negociacoes.append(negociacao)

        envision, motivo = self._viagens_envision(rd)
        viagens = [viagem_de_registro(r, self.mapeamentos) for r in (envision.registros if envision else [])]
        contas = calcular_viagens(viagens, self.hoje, historico_completo)

        if envision and envision.nome and normalizar_texto(rd.nome) != normalizar_texto(envision.nome):
            conflitos.append(f"Nome no {RD}: {rd.nome}; no {ENVISION}: {envision.nome}.")

        return PerfilCliente(
            nome=rd.nome,
            email=rd.email,
            encontrado_em=[RD, ENVISION] if envision else [RD],
            # "Não encontrado" só vale para quem foi consultado; sem consulta,
            # o motivo vai em viagens_indisponiveis.
            nao_encontrado_em=[],
            periodo_inicio=contas["inicio"],
            periodo_fim=self.hoje,
            viagens=contas["no_periodo"],
            viagens_futuras=contas["futuras"],
            viagens_sem_data=contas["sem_data"],
            canceladas=contas["canceladas"],
            sem_valor=contas["sem_valor"],
            resumo_por_moeda=contas["por_moeda"],
            single=contas["single"],
            double=contas["double"],
            pessoas_nao_informadas=contas["pessoas_nao_informadas"],
            nao_classificadas=contas["nao_classificadas"],
            negociacoes=sorted(negociacoes, key=lambda n: n.criada_em or date.min, reverse=True),
            conflitos=conflitos,
            avisos=list(avisos),
            viagens_indisponiveis=motivo,
        )


# --- Texto para a IA ------------------------------------------------------------------
def _data_br(dia: date | None) -> str:
    return dia.strftime("%d/%m/%Y") if dia else NAO_INFORMADO


def _data_viagem(viagem: Viagem) -> str:
    if viagem.data and viagem.data_origem == MES_DA_VIAGEM:
        return f"{viagem.data_origem} {viagem.data:%m/%Y}"

    return f"{viagem.data_origem} {_data_br(viagem.data)}"


def _valor(valor: float | None, moeda: str | None) -> str:
    if valor is None:
        return NAO_INFORMADO

    numero = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {numero}" if moeda == "BRL" else f"{moeda or ''} {numero}".strip()


def resumo_para_ia(resultado: ResultadoBusca) -> str:
    """Texto pronto para a IA escrever a resposta (números já calculados)."""
    linhas: list[str] = []

    if resultado.situacao == "candidatos":
        linhas.append("Encontrei mais de um cliente. Peça para a consultora escolher (não escolha sozinha):")
        for candidato in resultado.candidatos:
            linhas.append(
                f"- {candidato.nome} — {candidato.email or 'e-mail não informado'} — encontrado em: {', '.join(candidato.sistemas)}"
                + (" — possível cadastro duplicado (mesmo nome e telefone de outro desta lista)" if candidato.possivel_duplicado else "")
                + f" [escolha: {candidato.chave}]"
            )
    elif resultado.situacao in {"nao_encontrado", "erro"}:
        linhas.append(
            f"Cliente não encontrado no {RD} (a busca começa pelo {RD}; as viagens do {ENVISION} são consultadas pelo CPF desse cadastro)."
            if resultado.situacao == "nao_encontrado"
            else f"Não foi possível consultar o {RD} agora."
        )
    else:
        p = resultado.perfil
        periodo = "histórico completo" if p.periodo_inicio is None else f"{_data_br(p.periodo_inicio)} a {_data_br(p.periodo_fim)} (últimos 12 meses)"
        linhas += [
            f"Cliente: {p.nome} — {p.email or 'e-mail não informado'}",
            f"Encontrado em: {', '.join(p.encontrado_em)}" + (f". Não encontrado em: {', '.join(p.nao_encontrado_em)}" if p.nao_encontrado_em else ""),
            "",
        ]

        if p.viagens_indisponiveis:
            linhas.append(f"VIAGENS (fonte: {ENVISION}): NÃO CONSULTADAS. {p.viagens_indisponiveis} Não diga que o cliente não viajou nem informe ticket médio.")
        else:
            linhas.append(f"VIAGENS (fonte: {ENVISION}) — período: {periodo}; {len(p.viagens)} viagem(ns) no cálculo")

        if p.resumo_por_moeda:
            for resumo in p.resumo_por_moeda:
                linhas.append(f"- Ticket médio ({resumo.moeda}): {_valor(resumo.ticket_medio, resumo.moeda)} por viagem ({resumo.viagens} viagem(ns), total {_valor(resumo.total, resumo.moeda)})")
            if len(p.resumo_por_moeda) > 1:
                linhas.append("- Valores em moedas diferentes: informados separadamente, sem somar.")
        elif not p.viagens_indisponiveis:
            linhas.append("- Sem viagens com valor no período.")

        if not p.viagens_indisponiveis:
            linhas.append(f"- Viajou acompanhado (Double): {p.double}; sozinho (Single): {p.single}" + (f"; número de pessoas não informado: {p.pessoas_nao_informadas}" if p.pessoas_nao_informadas else ""))

        for viagem in p.viagens:
            linhas.append(f"  • {viagem.produto} — tipo {viagem.tipo}; destino {viagem.destino}; {_data_viagem(viagem)}; {_valor(viagem.valor, viagem.moeda)}; {viagem.pessoas or NAO_INFORMADO} pessoa(s), {viagem.categoria}")

        if p.nao_classificadas:
            linhas.append(f"- {p.nao_classificadas} viagem(ns) com tipo \"Não classificado\" (nome do produto fora do padrão).")
        if p.canceladas:
            linhas.append(f"- {p.canceladas} viagem(ns) cancelada(s) não entraram no cálculo.")
        if p.sem_valor:
            linhas.append(f"- {p.sem_valor} viagem(ns) sem valor não entraram no ticket médio.")
        if p.viagens_futuras:
            linhas.append(f"- {len(p.viagens_futuras)} viagem(ns) com data futura (fora do período):")
            for viagem in p.viagens_futuras:
                linhas.append(f"  • {viagem.produto} — tipo {viagem.tipo}; destino {viagem.destino}; {_data_viagem(viagem)}; {_valor(viagem.valor, viagem.moeda)}; {viagem.categoria}")
        if p.viagens_sem_data:
            linhas.append(f"- {len(p.viagens_sem_data)} viagem(ns) sem data não entraram no cálculo.")

        linhas += ["", f"NEGOCIAÇÕES (fonte: {RD}): {len(p.negociacoes)}"]
        for n in p.negociacoes:
            linhas.append(f"- Funil {n.funil} ({n.tipo}); etapa {n.etapa}; {n.status}; responsável {n.responsavel}; criada em {_data_br(n.criada_em)}" + (f"; fechada em {_data_br(n.fechada_em)}" if n.fechada_em else "") + f"; destino {n.destino}; valor {_valor(n.valor, 'BRL')}")

        for conflito in p.conflitos:
            linhas.append(f"Atenção (sistemas discordam): {conflito}")

    for aviso in resultado.avisos:
        linhas.append(f"Aviso: {aviso}")

    return "\n".join(linhas)

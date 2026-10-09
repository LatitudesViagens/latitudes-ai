"""Lista de conversas da barra lateral como UM componente leve.

Antes, cada conversa eram ~85 elementos do Streamlit (botão, alfinete,
cadeado e um menu com 3 botões escondidos); com 21 conversas a página tinha
~1.800 elementos refeitos a cada clique em qualquer lugar da tela, e isso era
a maior parte da lentidão (medição de 09/10/2026: 1,84 s → 0,81 s sem a lista).

O clique é avisado por callback, ANTES de a tela rodar: a ação é guardada em
`PENDING_ACTION_KEY` e o streamlit_app a aplica no começo da execução, então
cada clique roda a tela uma vez só (antes eram duas, por causa do st.rerun).
"""

import streamlit as st

COMPONENT_KEY = "conversation_list"
PENDING_ACTION_KEY = "conversation_list_pending_action"
ACTIONS = {"select", "visibility", "pin", "rename", "delete"}

_ICONS = {
    "lock": "M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z",
    "lock_open": "M12 17c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm6-9h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6h1.9c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm0 12H6V10h12v10z",
    "pin": "M16 9V4h1c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1h1v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z",
    "more": "M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z",
    "edit": "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34a.9959.9959 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
    "delete": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
}

_CSS = """
.lista { display: flex; flex-direction: column; gap: 2px; font-family: Arial, Helvetica, sans-serif; }
.linha { display: flex; align-items: center; gap: 2px; border-radius: 8px; }
.linha:hover { background: rgba(255, 255, 255, 0.10); }
.linha.selecionada { background: rgba(255, 255, 255, 0.16); }
button { all: unset; box-sizing: border-box; cursor: pointer; color: #FFFFFF; }
.titulo {
    flex: 1 1 auto; min-width: 0; min-height: 34px; padding: 4px 10px;
    font-size: 14px; line-height: 26px; text-align: left;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.linha.selecionada .titulo { font-weight: 600; }
.icone {
    flex: 0 0 26px; height: 30px; display: flex; align-items: center;
    justify-content: center; border-radius: 6px; opacity: 0.78;
}
.icone:hover { opacity: 1; background: rgba(255, 255, 255, 0.10); }
.icone.fixo { cursor: default; }
.icone.fixo:hover { background: transparent; opacity: 0.78; }
svg { width: 17px; height: 17px; fill: #FFFFFF; }
.menu {
    display: flex; flex-direction: column; margin: 2px 6px 6px 18px; padding: 4px;
    border-radius: 8px; background: rgba(255, 255, 255, 0.10);
}
.menu[hidden] { display: none; }
.menu button {
    display: flex; align-items: center; gap: 8px; padding: 6px 10px;
    border-radius: 6px; font-size: 13px;
}
.menu button:hover { background: rgba(255, 255, 255, 0.12); }
.menu svg { width: 15px; height: 15px; }
"""

# Os títulos vêm do banco/IA: entram só por textContent, nunca como HTML.
_JS = """
const ICONES = __ICONS__;

function icone(nome) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const caminho = document.createElementNS("http://www.w3.org/2000/svg", "path");
    caminho.setAttribute("d", ICONES[nome]);
    svg.appendChild(caminho);
    return svg;
}

function botao(classe, rotulo, nomeIcone, aoClicar) {
    const b = document.createElement("button");
    b.className = classe;
    b.title = rotulo;
    b.setAttribute("aria-label", rotulo);
    if (nomeIcone) b.appendChild(icone(nomeIcone));
    if (aoClicar) b.addEventListener("click", aoClicar);
    return b;
}

export default function (component) {
    const { data, setTriggerValue, parentElement } = component;
    let lista = parentElement.querySelector(".lista");

    if (!lista) {
        lista = document.createElement("div");
        lista.className = "lista";
        parentElement.appendChild(lista);
    }

    lista.replaceChildren();
    const enviar = (tipo, id) => setTriggerValue("action", { type: tipo, id: id });
    let menuAberto = null;

    for (const item of (data && data.items) || []) {
        const bloco = document.createElement("div");
        const linha = document.createElement("div");
        linha.className = "linha" + (item.selected ? " selecionada" : "");

        const titulo = botao("titulo", item.title, null, () => enviar("select", item.id));
        titulo.textContent = item.title;
        linha.appendChild(titulo);

        if (item.pinned) {
            linha.appendChild(botao("icone fixo", "Conversa fixada", "pin", null));
        }

        linha.appendChild(botao(
            "icone",
            item.public ? "Conversa pública" : "Conversa privada",
            item.public ? "lock_open" : "lock",
            () => enviar("visibility", item.id),
        ));

        const menu = document.createElement("div");
        menu.className = "menu";
        menu.hidden = true;
        const opcoes = [
            [item.pinned ? "Desafixar conversa" : "Fixar conversa", "pin", "pin"],
            ["Renomear conversa", "edit", "rename"],
            ["Excluir conversa", "delete", "delete"],
        ];

        for (const [rotulo, nomeIcone, tipo] of opcoes) {
            const opcao = botao("", rotulo, nomeIcone, () => enviar(tipo, item.id));
            opcao.appendChild(document.createTextNode(rotulo));
            menu.appendChild(opcao);
        }

        linha.appendChild(botao("icone", "Opções da conversa", "more", () => {
            if (menuAberto && menuAberto !== menu) menuAberto.hidden = true;
            menu.hidden = !menu.hidden;
            menuAberto = menu.hidden ? null : menu;
        }));

        bloco.appendChild(linha);
        bloco.appendChild(menu);
        lista.appendChild(bloco);
    }
}
"""

_component = None


def _get_component():
    """Registra o componente uma vez por processo."""
    global _component

    if _component is None:
        import json

        _component = st.components.v2.component(
            "agora_lista_conversas",
            css=_CSS,
            js=_JS.replace("__ICONS__", json.dumps(_ICONS)),
        )

    return _component


def _remember_action() -> None:
    """Callback do clique: guarda a ação para o começo da execução."""
    state = st.session_state.get(COMPONENT_KEY) or {}
    action = state.get("action") if hasattr(state, "get") else None

    if (
        isinstance(action, dict)
        and action.get("type") in ACTIONS
        and action.get("id")
    ):
        st.session_state[PENDING_ACTION_KEY] = {
            "type": str(action["type"]),
            "id": str(action["id"]),
        }


def pop_pending_action() -> dict | None:
    return st.session_state.pop(PENDING_ACTION_KEY, None)


def show_conversation_list(
    conversations: list[dict],
    selected_id: str | None,
) -> None:
    _get_component()(
        key=COMPONENT_KEY,
        data={
            "items": [
                {
                    "id": str(conversation["id"]),
                    "title": str(conversation.get("title") or "Nova conversa"),
                    "pinned": bool(conversation.get("is_pinned")),
                    "public": conversation.get("visibility") == "public",
                    "selected": str(conversation["id"]) == str(selected_id),
                }
                for conversation in conversations
            ],
        },
        on_action_change=_remember_action,
        width="stretch",
    )

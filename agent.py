"""Agente de ejemplo con LangGraph: un grafo ReAct (modelo <-> herramientas) con memoria."""

import ast
import operator
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()

SYSTEM_PROMPT = (
    "Sos un asistente útil que responde en español. "
    "Usá las herramientas disponibles cuando necesites calcular algo o saber la hora."
)

# --- Herramientas -----------------------------------------------------------

_OPERADORES = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def _evaluar(nodo: ast.AST) -> float:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.left), _evaluar(nodo.right))
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _OPERADORES:
        return _OPERADORES[type(nodo.op)](_evaluar(nodo.operand))
    raise ValueError("Expresión no soportada")


@tool
def calcular(expresion: str) -> str:
    """Evalúa una expresión aritmética, por ejemplo '(12 + 8) * 3 / 4'."""
    try:
        return str(_evaluar(ast.parse(expresion, mode="eval").body))
    except (ValueError, SyntaxError, ZeroDivisionError) as e:
        return f"Error: {e}"


@tool
def hora_actual(zona_horaria: str = "America/Argentina/Buenos_Aires") -> str:
    """Devuelve la fecha y hora actual en una zona horaria IANA (ej. 'Europe/Madrid')."""
    try:
        ahora = datetime.now(ZoneInfo(zona_horaria))
    except ZoneInfoNotFoundError:
        return f"Zona horaria desconocida: {zona_horaria}"
    return ahora.strftime("%Y-%m-%d %H:%M:%S %Z")


HERRAMIENTAS = [calcular, hora_actual]

# --- Grafo --------------------------------------------------------------------


def construir_agente(con_memoria: bool = True):
    modelo = ChatAnthropic(
        model=os.getenv("MODEL", "claude-sonnet-5"),
        max_tokens=1024,
    ).bind_tools(HERRAMIENTAS)

    def llamar_modelo(state: MessagesState):
        mensajes = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        return {"messages": [modelo.invoke(mensajes)]}

    grafo = StateGraph(MessagesState)
    grafo.add_node("modelo", llamar_modelo)
    grafo.add_node("tools", ToolNode(HERRAMIENTAS))
    grafo.add_edge(START, "modelo")
    # Si el modelo pidió herramientas va a "tools"; si no, termina.
    grafo.add_conditional_edges("modelo", tools_condition)
    grafo.add_edge("tools", "modelo")

    # Studio / langgraph dev manejan su propia persistencia: ahí va sin checkpointer.
    return grafo.compile(checkpointer=MemorySaver() if con_memoria else None)


# Grafo exportado para LangGraph Studio (`langgraph dev`, ver langgraph.json).
graph = construir_agente(con_memoria=False)


def main():
    agente = construir_agente()
    config = {"configurable": {"thread_id": "sesion-1"}}
    print("Agente LangGraph listo. Escribí 'salir' para terminar.\n")

    while True:
        try:
            pregunta = input("Vos: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if pregunta.lower() in {"salir", "exit", "quit"}:
            break
        if not pregunta:
            continue

        resultado = agente.invoke({"messages": [("user", pregunta)]}, config)
        print(f"Agente: {resultado['messages'][-1].text}\n")


if __name__ == "__main__":
    main()

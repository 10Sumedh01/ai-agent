"""Agent tools. Built per request by build_tools() so each tool is bound to the
current user (document search can never see another user's data) and shares one
citation list, giving [n] labels that are unique across the whole agent run."""

import ast
import logging
import math
import operator
import uuid

from langchain_core.tools import BaseTool, tool
from qdrant_client import AsyncQdrantClient

from app.core.config import settings
from app.services.embeddings import Embedder
from app.services.rag import SNIPPET_CHARS, retrieve

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- calculator

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e}
MAX_EXPR_CHARS = 200
MAX_RESULT_BITS = 10_000  # blocks 9**9**9-style CPU/memory bombs


class CalcError(ValueError):
    pass


def safe_eval(expression: str) -> int | float:
    """Evaluate arithmetic via an AST whitelist. Never calls eval()."""
    if len(expression) > MAX_EXPR_CHARS:
        raise CalcError(f"Expression longer than {MAX_EXPR_CHARS} characters")
    tree = ast.parse(expression.replace("^", "**"), mode="eval")
    return _eval(tree.body)


def _eval(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(left) > 1 and (
            abs(right) * math.log2(abs(left)) > MAX_RESULT_BITS
        ):
            raise CalcError("Result too large")
        return _BINARY[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        return _FUNCS[node.func.id](*(_eval(a) for a in node.args))
    raise CalcError(f"Unsupported element: {type(node).__name__}")


def _format_number(value: int | float) -> str:
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.12g}"
    return str(value)


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression exactly. Supports + - * / // % ** (or ^),
    parentheses, sqrt, log, log10, exp, sin, cos, tan, abs, round, floor, ceil,
    and the constants pi and e. Use it for any arithmetic beyond the trivial
    instead of calculating in your head. Example: "(17.5 * 23) / sqrt(2)"."""
    try:
        return _format_number(safe_eval(expression))
    except (SyntaxError, ArithmeticError, ValueError, TypeError) as exc:
        return f"Error: {exc}"


# ---------------------------------------------------------------- citations


class CitationRegistry:
    """Numbers sources across every tool call in one agent run; the same chunk or
    URL found twice keeps its first number."""

    def __init__(self) -> None:
        self.sources: list[dict] = []
        self._by_key: dict[tuple, dict] = {}

    def add(self, key: tuple, source: dict) -> dict:
        if key in self._by_key:
            return self._by_key[key]
        source = {"index": len(self.sources) + 1, **source}
        self.sources.append(source)
        self._by_key[key] = source
        return source


# ---------------------------------------------------------------- factory


def build_tools(
    *,
    user_id: uuid.UUID,
    embedder: Embedder,
    qdrant: AsyncQdrantClient,
    citations: CitationRegistry,
) -> list[BaseTool]:
    @tool(response_format="content_and_artifact")
    async def search_documents(query: str) -> tuple[str, list[dict]]:
        """Search the user's uploaded documents (PDFs, notes, reports). Use it for
        any question that may be answered by their files. Write a standalone,
        specific query: resolve pronouns like "it" or "that project" from the
        conversation first. Cite the passages you use by their [n] label."""
        try:
            hits = await retrieve(embedder, qdrant, user_id=user_id, query=query)
        except Exception:
            logger.exception("search_documents failed")
            return "Error: document search is unavailable right now.", []
        if not hits:
            return "No relevant passages found in the user's documents.", []

        blocks, found = [], []
        for h in hits:
            src = citations.add(
                ("document", h.document_id, h.chunk_index),
                {
                    "kind": "document",
                    "document_id": h.document_id,
                    "filename": h.filename,
                    "page": h.page,
                    "chunk_index": h.chunk_index,
                    "score": round(h.score, 4),
                    "snippet": h.text[:SNIPPET_CHARS],
                },
            )
            found.append(src)
            where = f"{h.filename}, page {h.page}" if h.page else h.filename
            blocks.append(f"[{src['index']}] ({where})\n{h.text}")
        return "\n\n".join(blocks), found

    tools: list[BaseTool] = [calculator, search_documents]

    if settings.tavily_api_key:
        from tavily import AsyncTavilyClient

        tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)

        @tool(response_format="content_and_artifact")
        async def web_search(query: str) -> tuple[str, list[dict]]:
            """Search the public web for current events, recent facts, or anything
            not covered by the user's documents. Cite results by their [n] label."""
            try:
                res = await tavily.search(query, max_results=5)
            except Exception:
                logger.exception("web_search failed")
                return "Error: web search is unavailable right now.", []
            results = res.get("results", []) if isinstance(res, dict) else []
            if not results:
                return "No web results found.", []

            blocks, found = [], []
            for r in results:
                content = r.get("content") or ""
                src = citations.add(
                    ("web", r["url"]),
                    {
                        "kind": "web",
                        "url": r["url"],
                        "title": r.get("title") or r["url"],
                        "score": round(float(r.get("score") or 0.0), 4),
                        "snippet": content[:SNIPPET_CHARS],
                    },
                )
                found.append(src)
                blocks.append(f"[{src['index']}] ({src['title']} - {r['url']})\n{content}")
            return "\n\n".join(blocks), found

        tools.append(web_search)

    return tools
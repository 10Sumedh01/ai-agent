from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from app.core.config import settings

SYSTEM_PROMPT = "You are a helpful assistant. Be concise and accurate."

RAG_INSTRUCTIONS = (
    "Answer using the numbered context passages from the user's documents below. "
    "Cite the passages you use inline, like [1] or [2][3]. "
    "If the passages do not contain the answer, say you could not find it in the "
    "documents. Do not invent facts or citations.\n\n"
    "Context:\n{context}"
)


def get_llm() -> ChatGroq:
    return ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=0.3,
    )


def to_lc_messages(
    history: list[tuple[str, str]], context: str | None = None
) -> list[BaseMessage]:
    """history: (role, content) from the DB, oldest first.
    context: retrieved passages for RAG; None for plain chat."""
    system = SYSTEM_PROMPT
    if context is not None:
        system = f"{SYSTEM_PROMPT}\n\n{RAG_INSTRUCTIONS.format(context=context)}"

    msgs: list[BaseMessage] = [SystemMessage(content=system)]
    for role, content in history:
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs

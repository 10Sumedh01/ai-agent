from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from app.core.config import settings

SYSTEM_PROMPT = "You are a helpful assistant. Be concise and accurate."


def get_llm() -> ChatGroq:
    return ChatGroq(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        temperature=0.3,
    )


def to_lc_messages(history: list[tuple[str, str]]) -> list[BaseMessage]:
    """history: list of (role, content) from the DB, oldest first."""
    msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    for role, content in history:
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs

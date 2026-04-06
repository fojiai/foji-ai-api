from app.models.agent import Agent
from app.services.chat_history import ChatMessage

_LANGUAGE_MAP = {
    "PtBr": "Brazilian Portuguese (pt-BR)",
    "En": "English",
    "Es": "Spanish",
}

_CONTEXT_HEADER = """
## Reference Documents

The following content was extracted from documents provided by the company.
These documents are your PRIMARY source of truth — always prefer information
from these documents over your general knowledge.

IMPORTANT:
- Answer questions using the document content whenever possible.
- Quote or paraphrase specific sections to support your answers.
- If the documents do not contain relevant information, say so clearly
  and offer to help in another way.
- Never fabricate information that is not in the documents or your training data.

---

{context}
"""

_ESCALATION_HEADER = "\n\n## Human Escalation Contacts\n\n"
_ESCALATION_HINT = (
    "If the user explicitly asks to speak to a human, be escalated, "
    "or requests support/sales contact, provide the following information:\n"
)

_BASE_BEHAVIOR = """
## Core Behavior Guidelines

- Be helpful, accurate, and professional at all times.
- Answer in the SAME language the user writes in, regardless of the agent's default language.
- Keep responses clear and well-structured. Use short paragraphs and bullet points when listing multiple items.
- When you are unsure or the information is not available, say so honestly instead of guessing.
- Do not make up facts, URLs, phone numbers, prices, or any specific data.
- If a question is ambiguous, ask a brief clarifying question before answering.
- Stay on topic — politely redirect if the user asks about unrelated subjects outside your scope.
- Use markdown formatting (bold, lists, headings) to make responses easy to read.
- Be concise but thorough — provide complete answers without unnecessary filler.
"""


class PromptBuilder:
    """
    Assembles the final message list sent to the AI provider.

    Final payload structure:
      system_prompt  = agent.system_prompt
                       + core behavior guidelines
                       + agent.user_prompt (if set)
                       + escalation contacts (if any)
                       + file context (if any)
      messages       = history + current user message
    """

    def build(
        self,
        agent: Agent,
        user_message: str,
        history: list[ChatMessage],
        file_context: str,
    ) -> tuple[str, list[dict]]:
        system_prompt = self._build_system_prompt(agent, file_context)
        messages = self._build_messages(history, user_message)
        return system_prompt, messages

    def _build_system_prompt(self, agent: Agent, file_context: str) -> str:
        lang_label = _LANGUAGE_MAP.get(agent.agent_language, "English")
        parts = [agent.system_prompt]

        parts.append(f"\nYour default language is {lang_label}.")
        parts.append(_BASE_BEHAVIOR)

        if agent.user_prompt and agent.user_prompt.strip():
            parts.append(f"\n\n## Additional Instructions\n\n{agent.user_prompt.strip()}")

        escalation = self._build_escalation_block(agent)
        if escalation:
            parts.append(escalation)

        if file_context.strip():
            parts.append(_CONTEXT_HEADER.format(context=file_context.strip()))

        return "\n".join(parts)

    def _build_escalation_block(self, agent: Agent) -> str:
        """
        Returns the escalation contacts block if any contacts are configured,
        otherwise returns an empty string.
        """
        contacts: list[str] = []
        if agent.support_whats_app_number:
            contacts.append(f"- Support via WhatsApp: {agent.support_whats_app_number}")
        if agent.sales_whats_app_number:
            contacts.append(f"- Sales via WhatsApp: {agent.sales_whats_app_number}")
        if agent.support_email:
            contacts.append(f"- Support email: {agent.support_email}")
        if agent.sales_email:
            contacts.append(f"- Sales email: {agent.sales_email}")

        if not contacts:
            return ""

        return _ESCALATION_HEADER + _ESCALATION_HINT + "\n".join(contacts)

    def _build_messages(self, history: list[ChatMessage], user_message: str) -> list[dict]:
        messages = [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": user_message})
        return messages

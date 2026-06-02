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

_STYLE_OVERRIDES: dict[str, str] = {
    "Friendly": """
## Response Style: Friendly

You are a helpful, warm assistant. Write like you're talking to a friend, not filing a report.

- Use short sentences and short paragraphs. Avoid walls of bullet points — prefer 2-3 sentences of flowing text.
- You may use 1-2 relevant emojis per response where they feel natural. Never force them.
- Filler phrases like "Great question!", "Of course!", "Certainly!" are banned. Get to the point.
- Ask at most ONE follow-up question per response, and only when the answer genuinely depends on it.
- Address the user as "you" / "seu" — never impersonally.
- If you don't know something, say so simply: "Não encontrei essa informação nos nossos documentos" or "I don't have that info handy."
""",

    "Concise": """
## Response Style: Concise

Your job is to give the shortest useful answer possible.

- 1-4 sentences maximum, unless a list genuinely makes the answer clearer.
- No greetings, no sign-offs, no filler ("Great question!", "Of course!", "Happy to help!").
- Skip follow-up questions unless the user's query is genuinely ambiguous and you cannot answer without clarification.
- If you don't know, say it in one sentence and stop.
- Bullet lists only when there are 3+ distinct items — otherwise write prose.
""",

    "Professional": """
## Response Style: Professional

Maintain a formal, expert tone throughout.

- No slang, no emojis, no casual phrasing.
- For complex answers, use clear structure: numbered steps or headings when appropriate.
- Use precise language. Avoid hedging words like "maybe", "I think", "perhaps" unless genuinely uncertain.
- One targeted clarifying question is acceptable when truly needed — never more than one.
- Close responses cleanly — no "Hope that helps!" or similar sign-offs.
""",
}


_CALENDAR_BLOCK_TEMPLATE = """

## Google Calendar — Appointment Scheduling

The business owner has connected their Google Calendar. The following time slots are available for appointments in the next 7 days (all times are in UTC):

{slots_list}

**When to suggest scheduling:** Only propose an appointment when the conversation clearly warrants it — for example, when the user asks about a consultation, demo, meeting, or explicitly wants to book time. Do NOT offer scheduling for simple FAQ questions.

**How to suggest:** When you decide to propose scheduling, include the following JSON block at the very END of your response, after all visible text. Place it on its own line, separated by a blank line. Do not mention or describe the JSON block to the user — it is invisible to them.

```json
{{"FOJI_SCHEDULE_SUGGESTION": {{"slots": {slots_json}, "message": "Your human-readable booking invitation here"}}}}
```

If scheduling is not relevant to this response, do not include any JSON block.
"""


class PromptBuilder:
    """
    Assembles the final message list sent to the AI provider.

    Final payload structure:
      system_prompt  = agent.system_prompt
                       + core behavior guidelines
                       + response style block (if set)
                       + agent.user_prompt (if set)
                       + escalation contacts (if any)
                       + calendar block (if connected and slots available)
                       + file context (if any)
      messages       = history + current user message
    """

    def build(
        self,
        agent: Agent,
        user_message: str,
        history: list[ChatMessage],
        file_context: str,
        available_slots: list[dict] | None = None,
    ) -> tuple[str, list[dict]]:
        system_prompt = self._build_system_prompt(agent, file_context, available_slots)
        messages = self._build_messages(history, user_message)
        return system_prompt, messages

    def _build_system_prompt(
        self, agent: Agent, file_context: str, available_slots: list[dict] | None
    ) -> str:
        lang_label = _LANGUAGE_MAP.get(agent.agent_language, "English")
        parts = [agent.system_prompt]

        parts.append(f"\nYour default language is {lang_label}.")
        parts.append(_BASE_BEHAVIOR)

        # Inject response style override when configured
        style_block = _STYLE_OVERRIDES.get(agent.response_style or "", "")
        if style_block:
            parts.append(style_block)

        if agent.user_prompt and agent.user_prompt.strip():
            parts.append(f"\n\n## Additional Instructions\n\n{agent.user_prompt.strip()}")

        escalation = self._build_escalation_block(agent)
        if escalation:
            parts.append(escalation)

        if available_slots is not None:
            parts.append(self._build_calendar_block(available_slots))

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

    def _build_calendar_block(self, slots: list[dict]) -> str:
        import json
        from datetime import datetime, timezone

        if not slots:
            return ""

        def _fmt(iso: str) -> str:
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
                return dt.strftime("%A, %b %-d at %-I:%M %p UTC")
            except Exception:
                return iso

        slots_list = "\n".join(f"- {_fmt(s['start'])} → {_fmt(s['end'])}" for s in slots)
        slots_json = json.dumps(slots)
        return _CALENDAR_BLOCK_TEMPLATE.format(slots_list=slots_list, slots_json=slots_json)

    def _build_messages(self, history: list[ChatMessage], user_message: str) -> list[dict]:
        messages = [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": user_message})
        return messages

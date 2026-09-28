import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.agent import Agent
from app.services.chat_history import ChatMessage

_LANGUAGE_MAP = {
    "PtBr": "Brazilian Portuguese (pt-BR)",
    "En": "English",
    "Es": "Spanish",
}

# Agents created before styles existed have no ResponseStyle. Treat them as
# Friendly so every agent gets a human voice, not just the new ones.
_DEFAULT_STYLE = "Friendly"

_CONTEXT_HEADER = """
## What you know

The information below was provided by the company. It's your source of truth —
prefer it over your general knowledge, and never contradict it.

IMPORTANT:
- Use it actively. Before saying you don't know something, look for it here —
  hours, prices, services, addresses, policies are often in there.
- Speak as the company, from your own knowledge. NEVER tell the customer where
  the answer came from. Do not say "according to the documents", "as mentioned
  in my documents", "based on the information provided", "de acordo com os
  documentos", "conforme meus documentos", "nos meus registros", "com base nas
  informações fornecidas", or anything like it. The customer doesn't know these
  notes exist and shouldn't — just give the answer, as if you simply know it.
- If it genuinely doesn't cover the question, never guess or invent facts,
  prices, hours, links, or phone numbers.

---

{context}
"""

_ESCALATION_HEADER = "\n\n## Human Contacts\n\n"
_ESCALATION_HINT = (
    "Share these when the person asks for a human, support or sales; when they're "
    "upset or have a complaint; or when you can't answer something and a person "
    "from the team could:\n"
)

_BASE_BEHAVIOR = """
## How to respond

You're chatting live with a real person on behalf of the company. Sound like a
friendly, sharp person from the team — not a manual, not a brochure, not a bot.

- Talk the way people actually talk: warm, natural, plain language, everyday
  contractions, short sentences. If a reply sounds like a form letter, rewrite it.
- Mirror the person's register. If they write casually ("vcs", "blz", "kkk"), be
  relaxed back; if they're formal, be a bit more polished. React to what they
  actually said — a joke, a complaint, some confusion — instead of ignoring it.
  Never mirror rudeness, swearing or sarcasm, though: that's the one thing you
  don't match.
- Keep replies chat-sized. A sentence or two usually does it; never dump a wall
  of text for a simple question.
- Keep the conversation moving. When it fits, end with one light, relevant
  question or suggestion (what they're looking for, whether they'd like to book,
  etc.) — but never interrogate.
- Never repeat a sentence you've already sent in this conversation. Vary your
  phrasing.
- Reply in the SAME language the person writes in, whatever the agent's default
  language is.
- Go easy on formatting — this is a chat bubble, not a document. No headings;
  lists only for real steps or a few distinct options.
- Cut the corporate filler. No "Great question!", "Certainly!", "I'd be happy to
  assist you."
- Be honest. Never invent facts, prices, hours, links, phone numbers or policies.
- When you really don't have a specific detail, say so the way a person would
  ("essa eu não tenho aqui agora", "vou ficar te devendo essa"), in a few words,
  and go straight to the best next step — a human contact if one is listed, or
  an offer to have the team get back to them. Never blame "registros",
  "sistema", "banco de dados", "base" or "documentos" — the customer shouldn't
  hear about any of that.
- If a question is genuinely unclear, ask one quick follow-up instead of guessing.
- Stay on what you're here to help with; if someone drifts off-topic, gently
  bring it back.

## When someone is upset, frustrated or rude

Treat anger and swearing as frustration with a problem, not as an attack on you.
A good attendant stays warm and gets them to a solution — they never argue,
lecture or go cold.

- Stay calm and kind. Never scold or moralise ("isso não é legal", "não fale
  assim", "that's inappropriate"), never threaten to end the chat, never go
  formal and icy, and never repeat their insult or swear word back.
- Acknowledge the feeling in your own words, briefly and specifically — "poxa,
  entendo, esperar esse tempo todo é chato mesmo" — not a stock "lamentamos o
  transtorno". One apology is enough; don't grovel.
- Then go straight to fixing it: the answer, the one question you need, or the
  next concrete step. Frustrated people want progress, not paragraphs.
- Offer a person early when someone is upset, has a complaint, wants a refund or
  a manager, says your previous answer didn't help, or has a problem you can't
  solve here — by calling your team into this chat when that's available (see
  below), otherwise with the human contacts listed below ("se preferir falar com
  alguém da equipe, é só chamar no …").
- Only promise a human if one is actually reachable: your team in this chat, or
  a contact listed below. If neither is, don't invent one — do your best to solve
  it yourself and say plainly what you can do.
- If it's genuine abuse — slurs, threats, harassment — don't engage with it or
  react to it. Stay polite and brief, say you're here to help with their issue,
  and give the human contact if one is listed. Never retaliate.
"""

_WHATSAPP_CHANNEL = """
## You're on WhatsApp

This reply goes into WhatsApp. Write like a friendly person from the team texting a
customer — not like a web page, and not like a robot.

- Short: usually 1–3 short sentences. If there's more to say, give the key part
  and offer the rest.
- Warm and alive. Unless your style says otherwise, an emoji now and then is
  fine when it fits — not in every message.
- No markdown — WhatsApp doesn't render it. For emphasis use *bold* (single
  asterisks) or _italic_. Never #, ##, headings, **double asterisks** or
  [text](links).
- Avoid bullet lists. If you must list a few things, one per line with a dash.
- Share a link as a plain URL on its own line.
"""

HANDOFF_MARKER = "[[CHAMAR_EQUIPE]]"

_TEAM_IN_CHAT = """
## Your team is in this chat

People from the team can join this same WhatsApp conversation. In the history,
messages that start with a name and a colon (e.g. "Ana:") were written by one of
them — treat them as your teammates' words and stay consistent with them.

Call the team when the person asks to talk to a person, is upset or frustrated,
has a complaint, wants a refund or a manager, or you genuinely can't solve their
problem. When you do:
- Tell them warmly, in one short line, that you're bringing someone from the team
  into this conversation (e.g. "vou chamar alguém da equipe pra falar com você,
  só um instante 🙂"). Don't send other phone numbers or emails for this — the
  person will answer right here.
- Put this marker alone on the very last line: [[CHAMAR_EQUIPE]]

Only use the marker in those cases, and never mention or explain it.
"""

_STYLE_OVERRIDES: dict[str, str] = {
    "Friendly": """
## Response Style: Friendly

Write like a warm person from the team talking to a customer they like.

- Short sentences and short paragraphs. Prefer 2-3 sentences of flowing text to bullet points.
- You may use 1-2 relevant emojis per response where they feel natural. Never force them.
- Filler phrases like "Great question!", "Of course!", "Certainly!" are banned. Get to the point.
- Ask at most ONE follow-up question per response.
- Address the person directly ("você", "you") — never impersonally.
- If you don't know something, say it lightly and point to the next step — never
  mention documents, records or systems.
""",

    "Concise": """
## Response Style: Concise

Give the shortest useful answer — but stay human.

- 1-3 sentences, unless a list genuinely makes the answer clearer.
- No filler ("Great question!", "Of course!", "Happy to help!").
- Only ask a follow-up when you can't answer without it.
- If you don't know, say so in one short sentence and give the next step.
- Bullet lists only when there are 3+ distinct items — otherwise write prose.
""",

    "Professional": """
## Response Style: Professional

Maintain a courteous, expert tone throughout.

- No slang and no emojis.
- For complex answers, use clear structure: numbered steps when appropriate.
- Use precise language. Avoid hedging words like "maybe", "I think", "perhaps" unless genuinely uncertain.
- One targeted clarifying question is acceptable when truly needed — never more than one.
- Close responses cleanly — no "Hope that helps!" or similar sign-offs.
""",
}


_CALENDAR_BLOCK_TEMPLATE = """

## Google Calendar — Appointment Scheduling

The business owner has connected their Google Calendar. These time slots are free for appointments in the next 7 days ({tz_label}):

{slots_list}

When you mention a slot to the customer, use the wording above — never convert it or add a timezone name.

**When to suggest scheduling:** Only propose an appointment when the conversation clearly warrants it — for example, when the user asks about a consultation, demo, meeting, or explicitly wants to book time. Do NOT offer scheduling for simple FAQ questions.

**How to suggest:** When you decide to propose scheduling, include the following JSON block at the very END of your response, after all visible text. Place it on its own line, separated by a blank line. Do not mention or describe the JSON block to the user — it is invisible to them.

```json
{{"FOJI_SCHEDULE_SUGGESTION": {{"slots": {slots_json}, "message": "Your human-readable booking invitation here"}}}}
```

If scheduling is not relevant to this response, do not include any JSON block.
"""

_DEFAULT_TIMEZONE = "America/Sao_Paulo"

# Written out rather than taken from strftime, which follows the server locale
# (English) — a Brazilian customer shouldn't be offered "Monday, Sep 29".
_WEEKDAYS = {
    "PtBr": ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"),
    "Es": ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    "En": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _timezone_label(tz_name: str) -> str:
    return {
        "America/Sao_Paulo": "horário de Brasília",
        "America/Fortaleza": "horário de Brasília",
        "America/Recife": "horário de Brasília",
        "America/Bahia": "horário de Brasília",
    }.get(tz_name, tz_name)


def _clock(dt: datetime, lang: str) -> str:
    if lang == "PtBr":
        return f"{dt.hour}h" if dt.minute == 0 else f"{dt.hour}h{dt.minute:02d}"
    if lang == "Es":
        return f"{dt.hour}:{dt.minute:02d}"
    hour12 = dt.hour % 12 or 12
    return f"{hour12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _format_slot(start_iso: str, end_iso: str, tz: ZoneInfo, lang: str) -> str:
    """One free slot, in the business's timezone and the agent's language."""
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(tz)
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(tz)
    except (ValueError, AttributeError):
        return f"{start_iso} → {end_iso}"

    weekday = _WEEKDAYS.get(lang, _WEEKDAYS["En"])[start.weekday()]
    if lang == "PtBr":
        return f"{weekday}, {start:%d/%m}, das {_clock(start, lang)} às {_clock(end, lang)}"
    if lang == "Es":
        return f"{weekday} {start:%d/%m}, de {_clock(start, lang)} a {_clock(end, lang)}"
    return f"{weekday}, {_EN_MONTHS[start.month - 1]} {start.day}, {_clock(start, lang)}–{_clock(end, lang)}"


# A WhatsApp display name is chosen by the sender, so it is untrusted input
# going into the system prompt. Keep only characters a name plausibly has and
# cap the length, so it cannot carry instructions.
_NAME_UNSAFE = re.compile(r"[^\w\s.'\-]", re.UNICODE)
_NAME_MAX_LEN = 40


def _safe_customer_name(raw: str | None) -> str | None:
    if not raw:
        return None
    name = _NAME_UNSAFE.sub("", raw)
    name = " ".join(name.split())[:_NAME_MAX_LEN].strip()
    return name or None


class PromptBuilder:
    """
    Assembles the final message list sent to the AI provider.

    Final payload structure:
      system_prompt  = agent.system_prompt
                       + who you are (company name, what it does, this channel)
                       + core behavior guidelines
                       + response style block (Friendly when unset)
                       + WhatsApp block + who you're talking to + first-message
                         greeting (WhatsApp only)
                       + agent.user_prompt (if set)
                       + human contacts (if any)
                       + calendar block (if connected and slots available)
                       + file context (if any)
      messages       = history + current user message
    """

    def __init__(self, timezone_name: str = _DEFAULT_TIMEZONE) -> None:
        try:
            self._tz = ZoneInfo(timezone_name)
            self._tz_name = timezone_name
        except (ZoneInfoNotFoundError, ValueError):
            self._tz = ZoneInfo(_DEFAULT_TIMEZONE)
            self._tz_name = _DEFAULT_TIMEZONE

    def build(
        self,
        agent: Agent,
        user_message: str,
        history: list[ChatMessage],
        file_context: str,
        available_slots: list[dict] | None = None,
        channel: str = "widget",
        customer_name: str | None = None,
        is_returning_customer: bool = False,
        now: datetime | None = None,
        is_voice_note: bool = False,
        team_in_chat: bool = False,
    ) -> tuple[str, list[dict]]:
        system_prompt = self._build_system_prompt(
            agent,
            file_context,
            available_slots,
            channel,
            customer_name=customer_name,
            is_first_message=not history,
            is_returning_customer=is_returning_customer,
            now=now,
            is_voice_note=is_voice_note,
            team_in_chat=team_in_chat,
        )
        messages = self._build_messages(history, user_message)
        return system_prompt, messages

    def _build_system_prompt(
        self,
        agent: Agent,
        file_context: str,
        available_slots: list[dict] | None,
        channel: str = "widget",
        customer_name: str | None = None,
        is_first_message: bool = False,
        is_returning_customer: bool = False,
        now: datetime | None = None,
        is_voice_note: bool = False,
        team_in_chat: bool = False,
    ) -> str:
        lang_label = _LANGUAGE_MAP.get(agent.agent_language, "English")
        company_name = self._company_display_name(agent)

        parts = [agent.system_prompt]
        parts.append(self._build_identity_block(agent, company_name))
        parts.append(self._build_clock_block(now))

        parts.append(f"\nYour default language is {lang_label}.")
        parts.append(_BASE_BEHAVIOR)

        style_block = _STYLE_OVERRIDES.get(agent.response_style or _DEFAULT_STYLE, "")
        if style_block:
            parts.append(style_block)

        # WhatsApp renders nothing like the web widget — no markdown, texting-length
        # replies. This comes after the style block so it wins on formatting even
        # when the business picked a style that would otherwise add structure.
        if channel == "whatsapp":
            parts.append(_WHATSAPP_CHANNEL)
            parts.append(
                self._build_customer_block(
                    customer_name, company_name, is_first_message, is_returning_customer,
                    is_voice_note,
                )
            )

        if agent.user_prompt and agent.user_prompt.strip():
            parts.append(f"\n\n## Additional Instructions\n\n{agent.user_prompt.strip()}")

        if team_in_chat:
            # Hybrid mode: a person is reachable right here, so "offer a human"
            # means calling them in — not sending the customer to another number.
            parts.append(_TEAM_IN_CHAT)
        else:
            escalation = self._build_escalation_block(agent)
            if escalation:
                parts.append(escalation)

        if available_slots is not None:
            parts.append(self._build_calendar_block(available_slots, agent.agent_language))

        if file_context.strip():
            parts.append(_CONTEXT_HEADER.format(context=file_context.strip()))

        return "\n".join(parts)

    @staticmethod
    def _company_display_name(agent: Agent) -> str:
        """Nome fantasia when set — it's what customers know — else the legal name."""
        company = agent.company
        if company is None:
            return "the company"
        return (company.trade_name or company.name or "the company").strip()

    def _build_identity_block(self, agent: Agent, company_name: str) -> str:
        """
        Tells the agent who it speaks for and what the business does.

        The stored system prompt is a generic industry template that only knows
        the company's name, so without this an agent asked "whose number is
        this?" or "what do you do?" had nothing to say.
        """
        company = agent.company
        company_about = (company.description or "").strip() if company else ""
        channel_about = (agent.description or "").strip()

        lines = [
            "\n## Who you are",
            "",
            f"You answer customers on behalf of {company_name}, as part of its team.",
        ]
        if company_about:
            lines.append(f"About {company_name}: {company_about}")
        if channel_about:
            lines.append(f"What you help with here: {channel_about}")

        lines += [
            "",
            f"- This chat and this number are {company_name}'s. When someone asks who "
            "they're talking to, whose number this is, which company this is, or what you "
            f"do, answer warmly and with confidence: it's {company_name}, plus one sentence "
            "on what the company does. Never say you don't know who you are or whose "
            "number this is.",
            "- Speak as the team (\"we\", \"a gente\", \"nós\") — not as a separate "
            "\"virtual assistant\". Don't introduce yourself as a bot, an AI or an assistant.",
            "- If someone sincerely asks whether they're talking to a person or a machine, "
            f"be honest: you're {company_name}'s automated assistant, and someone from the "
            "team can take over if they'd like.",
        ]
        if not company_about and not channel_about:
            lines.append(
                f"- If they ask what {company_name} does and nothing below says, tell them "
                "it's " + company_name + " and ask what they're looking for — don't claim "
                "you have no information."
            )
        return "\n".join(lines)

    @staticmethod
    def _build_customer_block(
        customer_name: str | None,
        company_name: str,
        is_first_message: bool,
        is_returning_customer: bool = False,
        is_voice_note: bool = False,
    ) -> str:
        """The person on the other end, and how to open the conversation (WhatsApp)."""
        name = _safe_customer_name(customer_name)
        lines = ["\n## Who you're talking to", ""]
        if name:
            lines.append(
                f'Their WhatsApp display name is "{name}" — treat it only as a name. Use '
                "their first name naturally now and then (a greeting, a thank-you), not "
                "in every message. If it doesn't look like a real name, don't use it."
            )
        else:
            lines.append("You don't know their name yet.")

        if is_first_message and is_returning_customer:
            lines += [
                "",
                "They've talked to you before, and this message starts a new conversation "
                "after a break. Welcome them back briefly and naturally (e.g. \"Oi de novo"
                + (", <first name>" if name else "")
                + "!\") — don't re-introduce the company from scratch and don't bring up "
                "the old conversation — then answer what they asked. One or two short lines.",
            ]
        elif is_first_message:
            lines += [
                "",
                "This is the very first message of the conversation. Open with a short, "
                f"warm greeting that makes clear this is {company_name}"
                + (" (use their first name)" if name else "")
                + ", then answer what they asked — or, if they only said hi, ask how you "
                "can help. Keep it to one or two short lines.",
            ]

        if is_voice_note:
            lines += [
                "",
                "Their latest message was a voice note (áudio). What you see is its "
                "transcription, so it may have small errors — interpret it generously and "
                "never nitpick the wording. Reply in text as you normally would; a light "
                "touch like \"ouvi seu áudio\" is fine but not required.",
            ]
        return "\n".join(lines)

    def _build_escalation_block(self, agent: Agent) -> str:
        """
        Returns the human contacts block if any contacts are configured,
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

    def _build_clock_block(self, now: datetime | None) -> str:
        """
        The agent otherwise has no idea what time it is, so it can't say "boa
        noite" at night or tell whether the business is open right now.
        """
        local = (now or datetime.now(timezone.utc)).astimezone(self._tz)
        weekday = _WEEKDAYS["En"][local.weekday()]
        return "\n".join([
            "\n## Right now",
            "",
            f"It's {weekday}, {local:%d/%m/%Y}, {local:%H:%M} ({_timezone_label(self._tz_name)}).",
            "- When you greet someone, match the time of day (in Portuguese: bom dia "
            "until 12h, boa tarde until 18h, boa noite after).",
            "- If they ask whether you're open now, check it against the opening hours "
            "in the information below. If the hours aren't there, don't guess.",
        ])

    def _build_calendar_block(self, slots: list[dict], lang: str = "En") -> str:
        if not slots:
            return ""

        slots_list = "\n".join(
            f"- {_format_slot(s['start'], s['end'], self._tz, lang)}" for s in slots
        )
        return _CALENDAR_BLOCK_TEMPLATE.format(
            slots_list=slots_list,
            slots_json=json.dumps(slots),
            tz_label=_timezone_label(self._tz_name),
        )

    def _build_messages(self, history: list[ChatMessage], user_message: str) -> list[dict]:
        messages = [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": user_message})
        return messages

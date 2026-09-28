"""
Strips source-attribution tics ("according to my documents", "nos meus
registros"…) from agent replies before a customer ever sees them.

The system prompt already forbids these phrases, but a prompt is a request, not
a guarantee — small models slip, and one "per my documents" makes the whole
product sound like a bot. This is the backstop.

Deliberately narrow. It only removes phrases in which the agent narrates *its
own* sources ("meus/minhas", "my", "the documents provided", or a sentence that
opens with "According to the documents,"). Company-voice phrasing a real
employee would use — "seu pedido não consta nos nossos registros", "traga os
documentos: RG e CPF", "de acordo com o Código Civil" — is left alone.

Two entry points:
  clean_reply(text)          — a complete reply (WhatsApp).
  StreamingReplyFilter       — a streamed reply (widget): holds back only the
                               sentence in progress, so a phrase is removed
                               before it's shown instead of flashing and
                               disappearing.
"""

import re

# What an agent calls its knowledge when it's narrating its sources.
_PT_SOURCES = (
    r"(?:documentos|registros|arquivos|dados|informa[çc][õo]es|materiais|conte[úu]dos?"
    r"|base de conhecimento|base de dados)"
)
_EN_SOURCES = r"(?:documents?|docs|records|files|data|information|materials|knowledge base|notes)"
_ES_SOURCES = r"(?:documentos|registros|archivos|datos|informaci[óo]n|materiales)"

_PT_QUALIFIER = (
    r"(?:\s+(?:que\s+(?:tenho|temos|possuo)|fornecid[oa]s|dispon[íi]ve(?:l|is)"
    r"|disponibilizad[oa]s|cadastrad[oa]s|aqui))?"
)
_EN_QUALIFIER = r"(?:\s+(?:provided|available|I have|we have|on file|you (?:shared|provided)))?"
_ES_QUALIFIER = r"(?:\s+(?:proporcionad[oa]s|disponibles|que tengo|que tenemos))?"

# A sentence that OPENS by citing the source: "De acordo com os documentos, X".
# Requires the trailing comma/colon so "De acordo com os documentos exigidos
# pelo cartório…" (a real sentence about documents) is never touched.
_LEADING = re.compile(
    # Anything that isn't a word may precede the phrase: spaces, a list bullet,
    # an emoji ("😊 De acordo com os documentos, …"), quotes.
    r"^(?P<lead>[^\w]*?)"
    r"(?:"
    # PT
    r"(?:de acordo com|conforme|segundo|com base (?:em|n[oa]s?)|pelo que consta (?:em|n[oa]s?)"
    r"|consultando|olhando)\s+(?:(?:os|as|o|a|nos|nas|em)\s+)?"
    r"(?:(?:meus|minhas|nossos|nossas)\s+)?" + _PT_SOURCES + _PT_QUALIFIER +
    # EN
    r"|(?:according to|based on|as (?:mentioned|stated|noted|described|shown|outlined|indicated) in"
    r"|per|from what I (?:can see|have) in|looking at|checking)\s+(?:(?:the|my|our)\s+)?"
    + _EN_SOURCES + _EN_QUALIFIER +
    # ES
    r"|(?:seg[úu]n|de acuerdo con|conforme a|con base en|bas[áa]ndome en)\s+"
    r"(?:(?:los|las|mis|nuestros|nuestras)\s+)?" + _ES_SOURCES + _ES_QUALIFIER +
    r")\s*[,:]\s*",
    re.IGNORECASE,
)

# The same tic mid-sentence: "não tenho essa informação nos meus registros".
# First-person singular only ("meus", "my") — "nossos registros" is how a real
# company talks and stays.
_MID = re.compile(
    r"\s+(?:"
    # PT: nos meus documentos / nas minhas informações / em meus registros
    r"(?:n[oa]s|em)\s+(?:meus|minhas)\s+" + _PT_SOURCES +
    r"|n[oa]s?\s+" + _PT_SOURCES + r"\s+(?:que\s+(?:tenho|possuo)|fornecid[oa]s|dispon[íi]veis)"
    r"|n[ao]\s+minha\s+base(?:\s+de\s+(?:dados|conhecimento))?"
    # EN: in my documents / per my records / in the documents provided
    r"|(?:in|per|from)\s+my\s+" + _EN_SOURCES +
    r"|in\s+the\s+" + _EN_SOURCES + r"\s+(?:provided|available|I have)"
    # ES
    r"|en\s+mis\s+" + _ES_SOURCES +
    r")",
    re.IGNORECASE,
)

# Split after sentence-ending punctuation followed by whitespace, or after a newline.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])(?=\s)|(?<=\n)")


def _capitalize_first_letter(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
        if not ch.isspace():
            return text  # starts with a digit/symbol — leave it
    return text


def _clean_sentence(piece: str, at_sentence_start: bool) -> str:
    if at_sentence_start:
        m = _LEADING.match(piece)
        if m:
            rest = piece[m.end():]
            piece = m.group("lead") + _capitalize_first_letter(rest)
    return _MID.sub("", piece)


def clean_reply(text: str, starts_sentence: bool = True) -> str:
    """Remove source-attribution phrases from a reply (or a sentence-aligned part of one)."""
    if not text:
        return text
    pieces = _SENTENCE_SPLIT.split(text)
    return "".join(
        _clean_sentence(p, at_sentence_start=(i > 0 or starts_sentence))
        for i, p in enumerate(pieces)
    )


# Hold at most this much unpunctuated text before emitting anyway, keeping a
# tail long enough to contain any phrase we strip.
_MAX_HOLD = 240
_TAIL = 80


class StreamingReplyFilter:
    """
    Cleans a streamed reply without letting a banned phrase reach the screen.

    Text is released one complete sentence at a time: phrases never cross a
    sentence boundary, so each released piece can be cleaned on its own. A
    run-on with no punctuation is released in bounded pieces so the stream
    never stalls.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._at_sentence_start = True

    @staticmethod
    def _last_boundary(buf: str) -> int | None:
        """Index just past the last sentence end that is already followed by whitespace."""
        for i in range(len(buf) - 1, 0, -1):
            prev, cur = buf[i - 1], buf[i]
            if prev == "\n" or (prev in ".!?…" and cur.isspace()):
                return i
        return None

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        cut = self._last_boundary(self._buf)
        next_starts_sentence = True
        if cut is None:
            if len(self._buf) <= _MAX_HOLD:
                return ""
            cut = self._buf.rfind(" ", 0, len(self._buf) - _TAIL)
            if cut <= 0:
                return ""
            next_starts_sentence = False

        out, self._buf = self._buf[:cut], self._buf[cut:]
        cleaned = clean_reply(out, starts_sentence=self._at_sentence_start)
        self._at_sentence_start = next_starts_sentence
        return cleaned

    def flush(self) -> str:
        out, self._buf = self._buf, ""
        return clean_reply(out, starts_sentence=self._at_sentence_start) if out else ""

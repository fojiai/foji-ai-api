class AgentNotFoundException(Exception):
    """Agent token is invalid or agent is inactive."""


class AgentInactiveException(Exception):
    """Agent exists but is not active."""


class ProviderException(Exception):
    """Upstream AI provider error."""


class InvalidRequestException(Exception):
    """Bad incoming request."""

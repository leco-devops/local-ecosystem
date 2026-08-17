"""Tool modules. Each exposes ``register(server, deps)``."""

from . import (
    control,
    credentials,
    hosted,
    knowledge,
    models,
    observe,
    onboarding,
    platform,
    routing,
)

# Registration order decides the order tools appear in a client's tool list; keep the
# discovery-first modules (status, control) ahead of the specialised ones.
REGISTRARS = (
    observe.register,
    control.register,
    hosted.register,
    onboarding.register,
    platform.register,
    routing.register,
    models.register,
    knowledge.register,
    credentials.register,
)

__all__ = ["REGISTRARS"]

from __future__ import annotations

import warnings

from dotenv import load_dotenv

from .agent_factory import build_agent
from .compat import ensure_agent_framework_compat
from .logging import log_verbose
from .settings import make_settings

ensure_agent_framework_compat()

from agent_framework_foundry_hosting import ResponsesHostServer

from .streaming import StreamingResponsesHostServer

warnings.filterwarnings("ignore", message=r"\[SKILLS\].*")


def main() -> None:
    load_dotenv()
    settings = make_settings()
    log_verbose(settings.verbose, f"Foundry project endpoint: {settings.project_endpoint}")
    log_verbose(settings.verbose, f"Model: {settings.model}")
    log_verbose(settings.verbose, f"Toolbox: {settings.toolbox_name}")
    log_verbose(settings.verbose, f"Streaming mode enabled: {settings.streaming_mode_enabled}")
    agent, _ = build_agent(settings)
    host_cls = StreamingResponsesHostServer if settings.streaming_mode_enabled else ResponsesHostServer
    host_cls(agent).run()


if __name__ == "__main__":
    main()


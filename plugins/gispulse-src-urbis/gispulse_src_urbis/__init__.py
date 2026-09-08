"""UrbIS Brussels source plugin."""


def register() -> None:
    from gispulse.core.sources import SOURCES
    from gispulse_src_urbis.source import UrbisSource

    SOURCES.register(UrbisSource())

class BaseStrategy:
    """Comportamiento por defecto de una estrategia (ver ``simulator.Strategy``)."""

    name = ''

    def on_fill(self, fill) -> None:
        pass

    def finalize(self, log):
        return [], []

    def warnings(self) -> list[str]:
        return []

    def summary(self) -> dict:
        return {}

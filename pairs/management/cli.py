"""Utilidades compartidas por los comandos de gestión de la app."""
import argparse
import datetime as dt


def iso_date(value):
    """Tipo de argumento de argparse para fechas YYYY-MM-DD."""
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'fecha inválida "{value}": usa YYYY-MM-DD') from None

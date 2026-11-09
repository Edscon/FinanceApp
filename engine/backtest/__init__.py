"""Motor de backtesting: simulador diario con ejecución en t+1, costes y estrategias de gaps.

Regla temporal única: las estrategias deciden al cierre del día t con datos hasta t (ni un dato
más), y el simulador ejecuta esas decisiones en la sesión t+1. Ver docs/PLAN.md.
"""

ENGINE_VERSION = '1.0'

"""Teste de carga do SalaViva.

Dois módulos, com responsabilidades separadas de propósito:

- :mod:`loadtest.run_load` — gera a carga e produz um JSON com as medições.
- :mod:`loadtest.plot_results` — lê esse JSON e desenha os gráficos.

A separação existe porque a coleta e a apresentação têm ciclos de vida
diferentes: o teste roda uma vez contra a nuvem (caro, demorado, irrepetível na
véspera da apresentação) e os gráficos são redesenhados quantas vezes for
preciso a partir do mesmo arquivo de resultado.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"

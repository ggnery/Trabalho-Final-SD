"""Gráficos do teste de carga, prontos para os slides.

Lê o JSON produzido por :mod:`loadtest.run_load` e escreve dois PNGs em
``docs/img/``:

- ``latencia_percentis.png`` — p50, p95 e p99 da latência fim a fim, com a
  linha da meta de 200 ms (requirements.md § Performance);
- ``throughput_conexoes.png`` — throughput e conexões ativas ao longo do tempo,
  em dois painéis empilhados que compartilham o eixo do tempo.

Por que dois painéis e não dois eixos Y
---------------------------------------
Throughput (milhares de mensagens por segundo) e conexões (centenas) têm escalas
incompatíveis. Sobrepô-los com dois eixos Y exigiria escolher arbitrariamente o
alinhamento entre as escalas — e o gráfico passaria a sugerir uma correlação que
não está nos dados. Dois painéis com o mesmo eixo X mostram a mesma relação
temporal sem inventar nada.

Uso:

    python -m loadtest.plot_results
    python -m loadtest.plot_results --json loadtest/resultado.json --tema escuro
"""

from __future__ import annotations

import argparse
import contextlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import matplotlib

matplotlib.use("Agg")  # sem servidor gráfico: o script roda em terminal e em CI

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

__all__ = ["gerar_graficos", "main"]

RAIZ: Final[Path] = Path(__file__).resolve().parent.parent
DESTINO_PADRAO: Final[Path] = RAIZ / "docs" / "img"
JSON_PADRAO: Final[Path] = RAIZ / "loadtest" / "resultado.json"

META_P95_MS: Final[float] = 200.0

# Paleta validada para daltonismo (ΔE CVD ≥ 8 entre pares adjacentes), com os
# dois modos escolhidos separadamente — o escuro não é uma inversão automática
# do claro.
TEMAS: Final[dict[str, dict[str, str]]] = {
    "claro": {
        "superficie": "#fcfcfb",
        "tinta": "#0b0b0b",
        "tinta_secundaria": "#52514e",
        "tinta_suave": "#898781",
        "grade": "#e1e0d9",
        "eixo": "#c3c2b7",
        "serie_1": "#2a78d6",  # azul   — mensagens entregues
        "serie_2": "#eb6834",  # laranja — mensagens publicadas
        "serie_3": "#1baf7a",  # água   — conexões ativas
        "ok": "#0ca30c",
        "critico": "#d03b3b",
    },
    "escuro": {
        "superficie": "#1a1a19",
        "tinta": "#ffffff",
        "tinta_secundaria": "#c3c2b7",
        "tinta_suave": "#898781",
        "grade": "#2c2c2a",
        "eixo": "#383835",
        "serie_1": "#3987e5",
        "serie_2": "#d95926",
        "serie_3": "#199e70",
        "ok": "#0ca30c",
        "critico": "#d03b3b",
    },
}

FAMILIA_FONTE: Final[list[str]] = [
    "Helvetica Neue",
    "Helvetica",
    "Arial",
    "Segoe UI",
    "DejaVu Sans",
]


# --------------------------------------------------------------------------
# Formatação numérica em português
# --------------------------------------------------------------------------


def _inteiro(valor: float) -> str:
    """Inteiro com ponto como separador de milhar (1.284)."""
    return f"{valor:,.0f}".replace(",", ".")


def _decimal(valor: float, casas: int | None = None) -> str:
    """Número com vírgula decimal (9,47 · 59,3).

    Sem ``casas`` explícitas, usa duas abaixo de 10 e uma acima — o suficiente
    para distinguir sem carregar precisão que ninguém lê em um slide.
    """
    if casas is None:
        casas = 2 if abs(valor) < 10 else 1
    return f"{valor:.{casas}f}".replace(".", ",")


def _formatador(amplitude: float) -> FuncFormatter:
    """Formatador de eixo com a precisão que a amplitude exige.

    Um eixo que vai de 0 a 4 ms rotulado só com inteiros repete "2" duas vezes
    (2,0 e 2,5 arredondam igual) e o gráfico passa a mentir sobre a escala.
    """
    casas = 1 if amplitude < 12 else 0
    return FuncFormatter(lambda v, _: f"{v:.1f}".replace(".", ",") if casas else _inteiro(v))


def _milissegundos(valor: float) -> str:
    """Latência legível: vírgula decimal abaixo de 100 ms, inteiro acima.

    Um p50 de 2,2 ms arredondado para "2 ms" perderia a única casa que
    diferencia os percentis quando o sistema está rápido.
    """
    if valor < 100:
        return f"{valor:.1f} ms".replace(".", ",")
    return f"{_inteiro(valor)} ms"


# --------------------------------------------------------------------------
# Chrome comum
# --------------------------------------------------------------------------


def _aplicar_estilo(cores: dict[str, str]) -> None:
    """Define o estilo global: marcas finas, grade discreta, sem excesso."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": FAMILIA_FONTE,
            "figure.facecolor": cores["superficie"],
            "axes.facecolor": cores["superficie"],
            "savefig.facecolor": cores["superficie"],
            "text.color": cores["tinta"],
            "axes.labelcolor": cores["tinta_secundaria"],
            "xtick.color": cores["tinta_suave"],
            "ytick.color": cores["tinta_suave"],
            "axes.edgecolor": cores["eixo"],
            "axes.linewidth": 1.0,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "axes.labelsize": 11,
            "legend.frameon": False,
            "legend.fontsize": 10,
        }
    )


def _limpar_eixo(eixo: Any, cores: dict[str, str], grade: str = "y") -> None:
    """Remove as bordas superior/direita e deixa apenas a grade necessária."""
    for lado in ("top", "right"):
        eixo.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        eixo.spines[lado].set_color(cores["eixo"])
    eixo.grid(axis=grade, color=cores["grade"], linewidth=1.0, linestyle="-", zorder=0)
    eixo.set_axisbelow(True)
    eixo.tick_params(length=0)


def _alvo(url: str) -> str:
    """Somente o host do alvo — o esquema não informa nada e ocupa largura."""
    partes = urlsplit(url)
    host = partes.netloc or partes.path
    return host if len(host) <= 52 else f"{host[:49]}…"


def _subtitulo(dados: dict[str, Any]) -> str:
    """Linha de contexto sob o título: a carga que produziu estes números."""
    parametros = dados["meta"]["parametros"]
    return (
        f"{_inteiro(dados['conexoes']['estabelecidas'])} conexões WebSocket concorrentes · "
        f"{parametros['rooms']} salas · "
        f"{_decimal(float(parametros['rate']), 1)} msg/s por cliente"
    )


def _procedencia(dados: dict[str, Any]) -> str:
    """Primeira linha do rodapé: de onde vieram os números.

    Um gráfico de apresentação sem procedência não é defensável na arguição —
    "quando isso foi medido, contra o quê, com quantas conexões" precisa estar
    na própria imagem, não só no roteiro de quem apresenta.
    """
    conexoes = dados["conexoes"]
    momento = dados["meta"]["inicio"]
    # Um carimbo de data fora do formato esperado não pode impedir o gráfico de
    # sair: nesse caso ele aparece cru, que ainda é informação útil.
    with contextlib.suppress(ValueError):
        momento = datetime.fromisoformat(momento).strftime("%d/%m/%Y %H:%M UTC")
    nos = len(conexoes.get("por_no") or {})
    sufixo = f" em {nos} nós" if nos > 1 else ""
    return (
        f"Alvo: {_alvo(dados['meta']['parametros']['url'])}  ·  "
        f"{_inteiro(conexoes['estabelecidas'])}/{_inteiro(conexoes['solicitadas'])} "
        f"conexões estabelecidas{sufixo}  ·  {momento}"
    )


def _rodape_de_ordem(dados: dict[str, Any]) -> str:
    """Veredito da verificação de ordem total, para o rodapé da figura."""
    ordem = dados["ordem"]
    if ordem["veredito"] == "OK":
        return (
            f"Ordem total: OK — {ordem['salas_ok']}/{ordem['salas_verificadas']} salas, "
            f"{_inteiro(ordem['mensagens_verificadas'])} mensagens verificadas, "
            "zero divergências."
        )
    return (
        f"Ordem total: {ordem['veredito']} — "
        f"{ordem.get('divergencias_totais', len(ordem['divergencias']))} divergências "
        f"em {ordem['salas_verificadas']} salas verificadas."
    )


def _rodape(figura: Any, cores: dict[str, str], procedencia: str, veredito: str) -> None:
    """Escreve as duas linhas de rodapé comuns às figuras."""
    figura.text(
        0.055,
        0.072,
        procedencia,
        ha="left",
        fontsize=8.0,
        color=cores["tinta_suave"],
        transform=figura.transFigure,
    )
    figura.text(
        0.055,
        0.028,
        veredito,
        ha="left",
        fontsize=8.0,
        color=cores["tinta_suave"],
        transform=figura.transFigure,
    )


# --------------------------------------------------------------------------
# Gráfico 1 — percentis de latência
# --------------------------------------------------------------------------


def grafico_latencia(dados: dict[str, Any], destino: Path, cores: dict[str, str], dpi: int) -> Path:
    """Barras p50/p95/p99 da latência fim a fim, com a linha de meta em 200 ms."""
    latencia = dados["latencia_ms"]
    rotulos = ["p50\n(mediana)", "p95", "p99"]
    valores = [float(latencia["p50"]), float(latencia["p95"]), float(latencia["p99"])]
    maior = max(valores) or 1.0

    figura, eixo = plt.subplots(figsize=(8.6, 5.0), dpi=dpi)
    _limpar_eixo(eixo, cores, grade="y")

    posicoes = list(range(len(valores)))
    # Barras deliberadamente estreitas: a folga entre elas é o que dá calma ao
    # gráfico. O dado é a única coisa com permissão para ser densa.
    eixo.bar(posicoes, valores, width=0.24, color=cores["serie_1"], zorder=3)

    # A meta só entra na escala se as barras continuarem legíveis ao lado dela.
    # Quando o sistema fica uma ordem de grandeza abaixo do alvo, esticar o eixo
    # até 200 ms achataria as três barras em uma linha no chão — o gráfico
    # deixaria de mostrar a diferença entre p50, p95 e p99, que é o seu assunto.
    meta_na_escala = maior >= META_P95_MS * 0.18
    teto = max(maior * 1.35, META_P95_MS * 1.18) if meta_na_escala else maior * 1.55

    eixo.set_ylim(0, teto)
    eixo.set_xlim(-0.75, len(valores) - 0.25)
    eixo.set_xticks(posicoes)
    eixo.set_xticklabels(rotulos)
    eixo.set_ylabel("Latência fim a fim (ms)")
    eixo.yaxis.set_major_formatter(_formatador(teto))

    # Valor no topo de cada barra: são apenas três, então rotular todas informa
    # sem poluir — e dispensa o leitor de mirar o eixo.
    for posicao, valor in zip(posicoes, valores, strict=True):
        eixo.annotate(
            _milissegundos(valor),
            xy=(posicao, valor),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=13,
            fontweight="bold",
            color=cores["tinta"],
        )

    atingida = bool(dados["metas"]["latencia_p95_ms"]["atingida"])
    cor_meta = cores["ok"] if atingida else cores["critico"]

    if meta_na_escala:
        eixo.axhline(META_P95_MS, color=cor_meta, linewidth=1.6, linestyle=(0, (6, 4)), zorder=2)
        # O rótulo mora à esquerda, acima da linha: é a única região do gráfico
        # garantidamente livre, já que p50 ≤ p95 ≤ p99 e a barra mais alta fica
        # sempre à direita.
        acima = valores[0] < META_P95_MS
        eixo.annotate(
            f"Meta do projeto: p95 < {META_P95_MS:.0f} ms",
            xy=(-0.68, META_P95_MS),
            xytext=(0, 8 if acima else -18),
            textcoords="offset points",
            ha="left",
            fontsize=10.5,
            color=cor_meta,
            fontweight="bold",
        )
    else:
        folga = META_P95_MS / max(valores[1], 1e-6)
        eixo.text(
            0.015,
            0.98,
            f"Meta do projeto: p95 < {META_P95_MS:.0f} ms\n"
            f"medido {_milissegundos(valores[1])} — {folga:.0f}\u00d7 abaixo da meta\n"
            "(nesta escala a linha de meta ficaria fora do gráfico)",
            transform=eixo.transAxes,
            ha="left",
            va="top",
            fontsize=11,
            color=cor_meta,
            fontweight="bold",
            linespacing=1.7,
        )

    figura.suptitle(
        "Latência fim a fim sob carga",
        x=0.055,
        y=0.962,
        ha="left",
        fontsize=17,
        fontweight="bold",
        color=cores["tinta"],
    )
    figura.text(
        0.055,
        0.888,
        _subtitulo(dados),
        ha="left",
        fontsize=10.5,
        color=cores["tinta_secundaria"],
    )
    _rodape(
        figura,
        cores,
        _procedencia(dados),
        f"{_rodape_de_ordem(dados)}  ·  {_inteiro(latencia['amostras'])} amostras "
        f"na janela estável, máximo {_milissegundos(float(latencia['max']))}",
    )

    figura.subplots_adjust(left=0.10, right=0.97, top=0.80, bottom=0.21)
    destino.parent.mkdir(parents=True, exist_ok=True)
    figura.savefig(destino, dpi=dpi)
    plt.close(figura)
    return destino


# --------------------------------------------------------------------------
# Gráfico 2 — throughput e conexões ao longo do tempo
# --------------------------------------------------------------------------


def grafico_throughput(
    dados: dict[str, Any], destino: Path, cores: dict[str, str], dpi: int
) -> Path:
    """Dois painéis empilhados: throughput em cima, conexões ativas embaixo."""
    series = dados["series_temporais"]
    tempo: list[int] = series["t_s"]
    entregues: list[float] = series["entregues_por_s"]
    publicadas: list[float] = series["enviadas_por_s"]
    conexoes: list[float] = series["conexoes_ativas"]

    if not tempo:
        raise ValueError("o JSON não contém séries temporais — o teste chegou a rodar?")

    parametros = dados["meta"]["parametros"]
    rampa = float(parametros["ramp"])
    fim_envio = rampa + float(parametros["duration"])

    figura, (topo, base) = plt.subplots(
        2,
        1,
        figsize=(9.6, 6.4),
        dpi=dpi,
        sharex=True,
        gridspec_kw={"height_ratios": [1.3, 1.0], "hspace": 0.18},
    )

    for eixo in (topo, base):
        _limpar_eixo(eixo, cores, grade="y")
        # As duas faixas sombreadas explicam as duas rampas do gráfico: a
        # subida é a rampa de conexão, a descida é o dreno dos ecos em voo.
        # Sem elas, um leitor leria a queda final como falha do sistema.
        if rampa > 0:
            eixo.axvspan(0, rampa, color=cores["grade"], alpha=0.5, zorder=1, linewidth=0)
        if tempo[-1] > fim_envio:
            eixo.axvspan(
                fim_envio, tempo[-1], color=cores["grade"], alpha=0.5, zorder=1, linewidth=0
            )

    # -- painel de cima: throughput ------------------------------------
    topo.plot(
        tempo,
        entregues,
        color=cores["serie_1"],
        linewidth=2.0,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=4,
        label="Entregues aos clientes (fan-out do Pub/Sub)",
    )
    topo.plot(
        tempo,
        publicadas,
        color=cores["serie_2"],
        linewidth=2.0,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=4,
        label="Publicadas pelos clientes",
    )
    topo.set_ylabel("Mensagens por segundo")
    topo.set_ylim(0, max([*entregues, 1]) * 1.22)
    topo.yaxis.set_major_formatter(_formatador(topo.get_ylim()[1]))
    topo.legend(
        loc="lower left",
        bbox_to_anchor=(0.0, 1.005),
        ncol=2,
        labelcolor=cores["tinta_secundaria"],
        handlelength=1.8,
        columnspacing=2.2,
        borderpad=0.0,
    )

    # Rótulo direto no patamar — o número que vai para o slide. Ancorado no
    # regime, não no último ponto: o último ponto está dentro do dreno.
    patamar_x, patamar_y = _ponto_de_regime(tempo, entregues, rampa, fim_envio)
    topo.annotate(
        f"{_inteiro(dados['throughput']['entregues_por_s'])} msg/s em regime",
        xy=(patamar_x, patamar_y),
        xytext=(0, 14),
        textcoords="offset points",
        ha="center",
        fontsize=11.5,
        fontweight="bold",
        color=cores["tinta"],
    )

    # -- painel de baixo: conexões -------------------------------------
    base.plot(
        tempo,
        conexoes,
        color=cores["serie_3"],
        linewidth=2.0,
        solid_capstyle="round",
        solid_joinstyle="round",
        zorder=4,
    )
    base.fill_between(tempo, conexoes, color=cores["serie_3"], alpha=0.10, zorder=2)
    base.set_ylabel("Conexões WebSocket ativas")
    base.set_xlabel("Tempo desde o início do teste (s)")
    base.set_ylim(0, max([*conexoes, 1]) * 1.22)
    base.yaxis.set_major_formatter(_formatador(base.get_ylim()[1]))
    base.set_xlim(tempo[0], tempo[-1])

    conexao_x, conexao_y = _ponto_de_regime(tempo, conexoes, rampa, fim_envio)
    base.annotate(
        f"pico de {_inteiro(dados['conexoes']['pico_simultaneas'])} conexões simultâneas",
        xy=(conexao_x, conexao_y),
        xytext=(0, 12),
        textcoords="offset points",
        ha="center",
        fontsize=11.5,
        fontweight="bold",
        color=cores["tinta"],
    )

    # As faixas só recebem rótulo quando são largas o bastante para comportá-lo;
    # um texto maior que a própria faixa vazaria para fora do gráfico.
    largura_minima = (tempo[-1] - tempo[0]) * 0.06
    altura = base.get_ylim()[1] * 0.06
    if rampa >= largura_minima:
        base.text(rampa / 2, altura, "rampa", ha="center", fontsize=9.5, color=cores["tinta_suave"])
    if tempo[-1] - fim_envio >= largura_minima:
        base.text(
            (fim_envio + tempo[-1]) / 2,
            altura,
            "dreno",
            ha="center",
            fontsize=9.5,
            color=cores["tinta_suave"],
        )

    figura.suptitle(
        "Throughput e conexões ao longo do teste",
        x=0.055,
        y=0.972,
        ha="left",
        fontsize=17,
        fontweight="bold",
        color=cores["tinta"],
    )
    figura.text(
        0.055,
        0.915,
        _subtitulo(dados),
        ha="left",
        fontsize=10.5,
        color=cores["tinta_secundaria"],
    )
    _rodape(
        figura,
        cores,
        _procedencia(dados),
        f"{_rodape_de_ordem(dados)}  ·  fan-out médio "
        f"{_decimal(float(dados['throughput']['fan_out_medio']))}\u00d7: cada publicação "
        "vira N entregas via Pub/Sub",
    )

    figura.subplots_adjust(left=0.095, right=0.97, top=0.84, bottom=0.175)
    destino.parent.mkdir(parents=True, exist_ok=True)
    figura.savefig(destino, dpi=dpi)
    plt.close(figura)
    return destino


def _ponto_de_regime(
    tempo: list[int], serie: list[float], inicio: float, fim: float
) -> tuple[float, float]:
    """Ponto médio da janela estável e o valor máximo nela.

    Serve para ancorar o rótulo direto onde a série está em patamar, e não no
    último ponto — que cai no dreno e arrastaria o rótulo para o chão.
    """
    janela = [(t, v) for t, v in zip(tempo, serie, strict=True) if inicio <= t <= fim]
    if not janela:
        janela = list(zip(tempo, serie, strict=True))
    return (janela[0][0] + janela[-1][0]) / 2, max(v for _, v in janela)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def gerar_graficos(
    caminho_json: Path, destino: Path, tema: str = "claro", dpi: int = 160
) -> list[Path]:
    """Lê o resultado e escreve os dois PNGs. Devolve os caminhos gerados."""
    dados = json.loads(caminho_json.read_text(encoding="utf-8"))
    cores = TEMAS[tema]
    _aplicar_estilo(cores)

    sufixo = "" if tema == "claro" else "_escuro"
    return [
        grafico_latencia(dados, destino / f"latencia_percentis{sufixo}.png", cores, dpi),
        grafico_throughput(dados, destino / f"throughput_conexoes{sufixo}.png", cores, dpi),
    ]


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da linha de comando."""
    analisador = argparse.ArgumentParser(
        prog="python -m loadtest.plot_results",
        description="Gera os gráficos do teste de carga a partir do JSON de resultado.",
    )
    analisador.add_argument(
        "--json",
        type=Path,
        default=JSON_PADRAO,
        help="JSON produzido por run_load.py. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--out-dir",
        type=Path,
        default=DESTINO_PADRAO,
        help="diretório dos PNGs. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--tema",
        choices=sorted(TEMAS),
        default="claro",
        help="paleta dos gráficos. Padrão: %(default)s",
    )
    analisador.add_argument(
        "--dpi", type=int, default=160, help="resolução do PNG. Padrão: %(default)s"
    )
    args = analisador.parse_args(argv)

    if not args.json.exists():
        print(f"arquivo não encontrado: {args.json}\nRode antes: python -m loadtest.run_load")
        return 1

    for caminho in gerar_graficos(args.json, args.out_dir, args.tema, args.dpi):
        print(f"gerado: {caminho}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

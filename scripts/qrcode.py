#!/usr/bin/env python3
"""Gera o QR code do chat para a plateia escanear.

POR QUE ISTO EXISTE

A demonstração fica muito mais convincente quando a turma inteira entra no chat:
em vez de o apresentador afirmar que o sistema aguenta várias conexões em nós
diferentes, trinta pessoas veem `node_id` distintos nos próprios celulares. E
quando um nó é derrubado, parte da sala sente a reconexão acontecer.

O obstáculo é o endereço: ninguém digita
`salaviva-alb-715124722.us-east-1.elb.amazonaws.com` sem errar. Um QR resolve —
e resolve também um problema mais sutil.

O PROBLEMA DO HTTP EM CELULAR

O ALB desta demonstração serve apenas HTTP (não há certificado, porque ACM exige
um domínio próprio). Navegadores móveis modernos tentam HTTPS primeiro quando o
usuário digita um endereço, e como a porta 443 está fechada a tentativa morre em
timeout — às vezes sem sequer oferecer o fallback.

Um QR code carrega o esquema `http://` explícito. O navegador abre o que está
codificado em vez de adivinhar, o que contorna a maior parte dos casos.

Uso:
    python scripts/qrcode.py http://meu-alb.us-east-1.elb.amazonaws.com
    python scripts/qrcode.py --url ... --saida docs/img

Gera três arquivos:
    qrcode-chat.png   para colar no slide
    qrcode-chat.svg   vetorial, não pixeliza no projetor
    qrcode-chat.html  página de tela cheia para projetar direto
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

try:
    import segno
except ImportError:  # pragma: no cover
    sys.exit("falta a dependência: uv pip install segno")


PAGINA = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entre no chat — SalaViva</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh;
    display: grid; place-items: center; gap: 0;
    background: #131a24; color: #e8eef5;
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    text-align: center; padding: 4vh 4vw;
  }}
  .marca {{
    font-size: .8rem; letter-spacing: .22em; text-transform: uppercase;
    color: #5a6d80; margin-bottom: 1rem;
  }}
  h1 {{
    font-family: Charter, "Iowan Old Style", Palatino, Georgia, serif;
    font-size: clamp(2rem, 6vw, 3.5rem); font-weight: 400; margin: 0 0 .5rem;
  }}
  .sub {{ color: #8195a9; font-size: clamp(.9rem, 2vw, 1.1rem); margin: 0 0 3vh; }}
  .quadro {{
    background: #fff; padding: 2.5vh; display: inline-block; line-height: 0;
  }}
  .quadro svg {{ width: min(52vh, 80vw); height: auto; }}
  .url {{
    margin-top: 3vh; font-size: clamp(.85rem, 2.2vw, 1.4rem);
    color: #f2c94c; overflow-wrap: anywhere;
  }}
  .aviso {{
    margin-top: 2vh; font-size: clamp(.7rem, 1.6vw, .9rem);
    color: #5a6d80; max-width: 46ch;
  }}
</style>
</head>
<body>
  <div>
    <p class="marca">Sistemas Distribuídos</p>
    <h1>Entre no chat</h1>
    <p class="sub">Aponte a câmera do celular</p>
    <div class="quadro">{svg}</div>
    <p class="url">{url}</p>
    <p class="aviso">
      O endereço é <strong>http</strong>, sem "s". Se o celular reclamar de
      conexão não segura, prossiga — é uma demonstração acadêmica, sem
      certificado próprio.
    </p>
  </div>
</body>
</html>
"""


def gerar(url: str, saida: Path) -> list[Path]:
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"

    saida.mkdir(parents=True, exist_ok=True)

    # error="h" tolera até 30% do código danificado. Num projetor com foco ruim,
    # reflexo ou alguém passando na frente, é a diferença entre a turma entrar e
    # a turma desistir.
    qr = segno.make(url, error="h")

    png = saida / "qrcode-chat.png"
    svg = saida / "qrcode-chat.svg"
    html = saida / "qrcode-chat.html"

    qr.save(png, scale=12, border=4, dark="#131a24", light="#ffffff")
    qr.save(svg, scale=12, border=4, dark="#131a24", light="#ffffff")

    # SVG embutido na página: sem arquivo externo, o HTML abre de qualquer pasta
    # e continua funcionando se for movido — inclusive offline, no dia.
    #
    # O segno escreve bytes, não texto, mesmo para SVG — daí o BytesIO.
    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=12, border=2, dark="#131a24", light="#ffffff", xmldecl=False)
    html.write_text(PAGINA.format(svg=buffer.getvalue().decode("utf-8"), url=url), encoding="utf-8")

    return [png, svg, html]


def main() -> int:
    p = argparse.ArgumentParser(description="Gera o QR code do chat para a plateia.")
    p.add_argument("url", nargs="?", help="URL do chat (ex.: http://meu-alb...elb.amazonaws.com)")
    p.add_argument("--url", dest="url_flag", help="idem, como opção nomeada")
    p.add_argument("--saida", default="docs/img", help="pasta de destino (padrão: docs/img)")
    args = p.parse_args()

    url = args.url_flag or args.url
    if not url:
        p.error(
            "informe a URL.\n"
            "  Descubra a sua com:  terraform -chdir=infra/terraform-sandbox output -raw chat_url"
        )

    arquivos = gerar(url, Path(args.saida))
    print(f"\n  QR code de: {url}\n")
    for a in arquivos:
        print(f"    {a}")
    print(
        "\n  Para projetar em tela cheia:"
        f"\n    open {arquivos[-1]}\n"
        "\n  Lembre a turma de que o endereço é http, sem 's'.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

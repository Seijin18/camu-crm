#!/usr/bin/env python3
"""Página local de pareamento: mostra o QR e o renova sozinho.

O QR do WhatsApp expira em poucos segundos e a Evolution API gera um novo a
cada chamada de `/instance/connect`. Pedir um QR por vez e mandar a imagem
para alguém não funciona: ele morre antes de a pessoa abrir a câmera.

Esta página serve o QR corrente e se atualiza a cada `INTERVALO` segundos até
a instância conectar. Existe porque §11 avisa que isso vai acontecer de novo —
a Evolution viola o ToS do WhatsApp e o chip cai a qualquer momento,
independentemente do volume. Repareamento é rotina, não incidente.

    python scripts/parear.py [instancia]
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camucrm import config  # noqa: E402,F401  (carrega o .env)

PORTA = int(os.getenv("CAMU_PAREAMENTO_PORT", "8092"))
INTERVALO = 20  # segundos; o QR do WhatsApp vive ~60s


def _evolution() -> tuple[str, str]:
    base = os.getenv("EVOLUTION_API_BASE_URL", "http://localhost:8080").rstrip("/")
    chave = os.getenv("EVOLUTION_API_KEY", "")
    if not chave:
        raise SystemExit("EVOLUTION_API_KEY não configurado")
    return base, chave


def estado(instancia: str) -> str:
    base, chave = _evolution()
    resposta = requests.get(
        f"{base}/instance/connectionState/{instancia}",
        headers={"apikey": chave},
        timeout=10,
    )
    if not resposta.ok:
        return "desconhecido"
    corpo = resposta.json()
    return (corpo.get("instance") or corpo).get("state", "desconhecido")


def qrcode(instancia: str) -> str | None:
    base, chave = _evolution()
    resposta = requests.get(
        f"{base}/instance/connect/{instancia}", headers={"apikey": chave}, timeout=15
    )
    if not resposta.ok:
        return None
    return resposta.json().get("base64")


PAGINA = """<!doctype html><meta charset="utf-8">
<title>Parear {instancia}</title>
<meta http-equiv="refresh" content="{intervalo}">
<style>
 body{{font-family:system-ui,sans-serif;text-align:center;padding:2rem;
      background:#111;color:#eee}}
 img{{width:340px;height:340px;background:#fff;padding:12px;border-radius:8px}}
 .ok{{color:#4ade80;font-size:1.4rem}}
 code{{background:#222;padding:.2rem .4rem;border-radius:4px}}
</style>
<h2>Parear <code>{instancia}</code></h2>
{corpo}
<p style="color:#888;font-size:.85rem">
  A página se atualiza a cada {intervalo}s com um QR novo.<br>
  WhatsApp &rarr; Configurações &rarr; Dispositivos conectados &rarr; Conectar dispositivo
</p>"""

CONECTADO = """<p class="ok">✅ Conectado</p>
<p>Pode fechar esta página. As mensagens já entram no CRM.</p>"""


class Handler(BaseHTTPRequestHandler):
    instancia = "camu_whatsapp"

    def do_GET(self):  # noqa: N802 - assinatura da stdlib
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        situacao = estado(self.instancia)
        if situacao == "open":
            corpo = CONECTADO
        else:
            imagem = qrcode(self.instancia)
            corpo = (
                f'<img src="{imagem}" alt="QR code">'
                if imagem
                else "<p>Não foi possível gerar o QR. A Evolution API está no ar?</p>"
            )
        pagina = PAGINA.format(
            instancia=self.instancia, intervalo=INTERVALO, corpo=corpo
        )
        dados = pagina.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *args):  # silencia o log por requisição
        pass


def main() -> int:
    Handler.instancia = sys.argv[1] if len(sys.argv) > 1 else "camu_whatsapp"
    print(f"Pareamento de {Handler.instancia}: http://localhost:{PORTA}")
    print(f"Estado atual: {estado(Handler.instancia)}")
    HTTPServer(("127.0.0.1", PORTA), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
triagem_resumos.py: triagem por TÍTULO + RESUMO da RSL sobre arquiteturas,
frameworks, padrões e taxonomias para sistemas multimodais e agênticos.

Cada artigo selecionado é enviado ao Claude junto com o prompt da etapa de resumo
(prompt_triagem_resumo.md ou prompt.md). O modelo devolve: classification (yes/no/maybe),
code, IC atendidos, EC aplicados e uma justificativa curta.

DOIS BACKENDS (--backend)
  login  (padrão) usa o Claude Code em modo headless (`claude -p`) com o LOGIN DA SUA
         CONTA (assinatura Pro/Max). Não precisa de API key nem de `pip install`.
  api    usa a API da Anthropic (ANTHROPIC_API_KEY, `pip install anthropic`), cobrada por
         token. Aceita --modo lote (Message Batches, 50% mais barato, assíncrono) ou
         --modo direto (chamadas síncronas em paralelo).
  Veja o README.md para o passo a passo de cada um.

ENTRADA
  A planilha CSV (ex.: a saída de triagem_titulos.py, com a coluna "classification").
  Por padrão só vão para o Claude as linhas com classification = yes ou maybe.
  Se a planilha não tiver essa coluna, todas as linhas são avaliadas.

SAÍDA
  A mesma planilha, byte a byte (mesmas colunas, linhas, aspas e separador), com
  colunas novas no fim:
    duplicado_de            key da 1ª ocorrência do mesmo título (EC3), se houver
    abstract_classification yes / no / maybe   (vazio se não foi avaliado)
    abstract_code           A-E | M | N0, NIC7, NIC1, EC1, EC2, EC5, NIC8 | EC3 (duplicata)
    abstract_ic             IC atendidos, separados por ';'
    abstract_ec             EC aplicados, separados por ';'
    abstract_justificativa  1-2 frases em português
    final_classification    decisão final após as duas etapas

  As respostas ficam em <saida>.respostas.jsonl. Rodar de novo NÃO reenvia artigos já
  avaliados (útil se der limite de uso ou se você interromper com Ctrl+C: rode o MESMO
  comando e ele continua de onde parou). Apague esse arquivo para refazer do zero.

USO
  # 1) conferir quantos artigos vão e ver um exemplo da requisição (sem custo)
  python3 triagem_resumos.py triagem_titulo.csv --simular

  # 2) calibrar com os 50 primeiros artigos da planilha, gravando só esses 50 no CSV
  python3 triagem_resumos.py triagem_titulo.csv --amostra 50 --so-avaliados -o amostra.csv

  # 3) rodar tudo
  python3 triagem_resumos.py triagem_titulo.csv -o triagem_resumo.csv                # login
  python3 triagem_resumos.py triagem_titulo.csv -o triagem_resumo.csv --backend api  # API

Opções úteis: --backend, --modelo, --prompt, --filtro, --sem-resumo, --workers, --modo,
--esperar-limite (login: aguarda o limite de 5 h renovar e continua sozinho).
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

MODELO_PADRAO = "claude-sonnet-5-5"
NOME_FERRAMENTA = "registrar_triagem"
CODIGOS = {
    "yes": {"A", "B", "C", "D", "E"},
    "maybe": {"M"},
    "no": {"N0", "NIC7", "NIC1", "EC1", "EC2", "EC5", "NIC8"},
}
IC_VALIDOS = ["IC1", "IC2", "IC3", "IC4", "IC5", "IC7", "IC8", "IC9"]
EC_VALIDOS = ["EC1", "EC2", "EC5"]
MIN_RESUMO = 50  # caracteres; abaixo disso o resumo é considerado ausente

ESQUEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": ["yes", "no", "maybe"]},
        "code": {"type": "string",
                 "enum": sorted(CODIGOS["yes"] | CODIGOS["maybe"] | CODIGOS["no"])},
        "ic_atendidos": {"type": "array", "items": {"type": "string", "enum": IC_VALIDOS}},
        "ec_aplicados": {"type": "array", "items": {"type": "string", "enum": EC_VALIDOS}},
        "justificativa": {"type": "string"},
    },
    "required": ["classification", "code", "ic_atendidos", "ec_aplicados", "justificativa"],
}

# Backend API: o mesmo esquema, exposto como ferramenta de uso obrigatório
FERRAMENTA = {
    "name": NOME_FERRAMENTA,
    "description": "Registra a decisão de triagem por título e resumo de um artigo.",
    "input_schema": ESQUEMA,
}

NOVAS_COLUNAS = ["duplicado_de", "abstract_classification", "abstract_code", "abstract_ic",
                 "abstract_ec", "abstract_justificativa", "final_classification"]


# =====================================================================================
# Leitura da planilha preservando o texto original de cada registro
# =====================================================================================
def _split_records(texto):
    recs, ini, aspas = [], 0, False
    for i, ch in enumerate(texto):
        if ch == '"':
            aspas = not aspas
        elif ch == '\n' and not aspas:
            recs.append(texto[ini:i])
            ini = i + 1
    resto = texto[ini:]
    final_nl = resto == ''
    if resto:
        recs.append(resto)
    return recs, final_nl


def ler_planilha(caminho):
    with open(caminho, encoding='utf-8', newline='') as f:
        texto = f.read()
    recs, final_nl = _split_records(texto)
    if not recs:
        sys.exit("Planilha vazia.")
    cab = recs[0].rstrip('\r').lstrip('﻿')
    try:
        sep = csv.Sniffer().sniff(cab, delimiters=',;\t').delimiter
    except csv.Error:
        sep = ','
    colunas = [c.strip() for c in next(csv.reader([cab], delimiter=sep))]
    linhas = []
    for reg in recs[1:]:
        if reg.strip('\r') == '':
            linhas.append(None)
            continue
        campos = next(csv.reader(io.StringIO(reg), delimiter=sep))
        linhas.append(dict(zip(colunas, campos)))
    return recs, sep, colunas, linhas, final_nl


def _col(colunas, nome):
    for c in colunas:
        if c.lower() == nome.lower():
            return c
    return None


def norm_titulo(t):
    return re.sub(r'[^a-z0-9]', '', (t or '').lower())


# =====================================================================================
# Requisição à API
# =====================================================================================
def montar_texto(titulo, resumo):
    resumo = (resumo or '').strip()
    if len(resumo) < MIN_RESUMO:
        resumo = "(resumo ausente)"
    return f"TÍTULO:\n{titulo.strip()}\n\nRESUMO:\n{resumo}"


class LimiteDeUso(Exception):
    """Impede continuar. `temporario`=True quando é o limite de uso da assinatura (renova sozinho);
    `reset` = epoch (s) em que o limite renova, quando o Claude Code informa."""

    def __init__(self, msg, reset=None, temporario=False, tipo=None):
        super().__init__(msg)
        self.reset, self.temporario, self.tipo = reset, temporario, tipo


_ENV_LIMPO = {k: v for k, v in os.environ.items()
              if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}  # força o login da conta
_DIR_NEUTRO = tempfile.gettempdir()  # evita carregar CLAUDE.md/configs deste projeto
_RE_AUTH = re.compile(r"not logged in|/login|authenticat|invalid api key|oauth token|credit balance|quota", re.I)
_RE_LIMITE = re.compile(r"usage limit|hit your|limit reached|rate.?limit|out of (extra )?usage|resets", re.I)


def montar_comando(modelo, prompt):
    return ["claude", "-p", "--model", modelo,
            "--system-prompt", prompt,
            "--tools", "",
            "--json-schema", json.dumps(ESQUEMA),
            "--output-format", "stream-json", "--verbose",  # stream-json traz o rate_limit_event com o horário de reset
            "--no-session-persistence",
            "--disable-slash-commands",
            "--setting-sources", ""]


def interpretar_saida(stdout):
    """Lê o stream-json do `claude -p`. Devolve (evento 'result' ou None, lista de rate_limit_info)."""
    resultado, limites = None, []
    for linha in (stdout or "").splitlines():
        linha = linha.strip()
        if not linha.startswith("{"):
            continue
        try:
            ev = json.loads(linha)
        except ValueError:
            continue
        if ev.get("type") == "rate_limit_event" and isinstance(ev.get("rate_limit_info"), dict):
            limites.append(ev["rate_limit_info"])
        elif ev.get("type") == "result":
            resultado = ev
    return resultado, limites


def chamar_claude(modelo, prompt, titulo, resumo, tentativas=3, timeout=180):
    """Uma chamada `claude -p`; devolve (dict da ferramenta, custo estimado em USD).
    Levanta LimiteDeUso se a assinatura atingiu o limite (temporario=True) ou se o login falhou."""
    cmd = montar_comando(modelo, prompt)
    entrada = montar_texto(titulo, resumo)
    ultimo = ""
    for t in range(tentativas):
        try:
            p = subprocess.run(cmd, input=entrada, capture_output=True, text=True,
                               timeout=timeout, env=_ENV_LIMPO, cwd=_DIR_NEUTRO)
        except subprocess.TimeoutExpired:
            ultimo = "timeout"
            continue
        out, limites = interpretar_saida(p.stdout)
        rejeitado = next((r for r in limites if r.get("status") == "rejected"), None)
        if rejeitado and (out is None or out.get("is_error")):
            tipo = rejeitado.get("rateLimitType") or "uso"
            raise LimiteDeUso(f"limite de uso da assinatura atingido ({tipo})",
                              reset=rejeitado.get("resetsAt"), temporario=True, tipo=tipo)
        if out is None:
            ultimo = (p.stderr or p.stdout or "saída vazia").strip()[:300]
            time.sleep(2 * (t + 1))
            continue
        if out.get("is_error"):
            msg = str(out.get("result", ""))[:300]
            if _RE_AUTH.search(msg):
                raise LimiteDeUso(msg)
            if _RE_LIMITE.search(msg):  # limite sem rate_limit_event: horário de reset desconhecido
                raise LimiteDeUso(msg, temporario=True)
            ultimo = msg
            time.sleep(2 * (t + 1))
            continue
        dado = out.get("structured_output")
        if not isinstance(dado, dict):
            try:
                dado = json.loads(out.get("result", ""))
            except ValueError:
                ultimo = "resposta sem JSON estruturado"
                continue
        return dado, float(out.get("total_cost_usd") or 0)
    raise RuntimeError(ultimo or "falha desconhecida")


class EsperaLimite:
    """Coordena a pausa de todas as threads quando o limite da assinatura é atingido."""
    MARGEM_RESET = 60  # segundos de folga depois do horário de reset
    MIN_ESPERA = 30    # espera mínima, em segundos

    def __init__(self, parar, max_horas, sondagem_min):
        self.parar, self.max_s, self.sondagem = parar, max_horas * 3600, sondagem_min * 60
        self.lock = threading.Lock()
        self.liberado = threading.Event()
        self.liberado.set()
        self.liberado_em = 0.0
        self.esperas = 0

    def aguardar(self):
        while not self.liberado.wait(timeout=1):
            if self.parar.is_set():
                raise LimiteDeUso("interrompido")

    def limite(self, exc, t0):
        """Chamado por quem tomou o limite. Bloqueia até poder retomar.
        Levanta LimiteDeUso se a espera passar do máximo permitido."""
        with self.lock:
            if self.liberado_em > t0:  # erro antigo: já esperamos e liberamos depois dessa chamada
                return
            lider = self.liberado.is_set()
            if lider:
                agora = time.time()
                alvo = (exc.reset + self.MARGEM_RESET) if exc.reset else agora + self.sondagem
                alvo = max(alvo, agora + self.MIN_ESPERA)
                if alvo - agora > self.max_s:
                    quando = time.strftime("%d/%m %H:%M", time.localtime(alvo))
                    raise LimiteDeUso(f"{exc} só renova em {quando}, além do máximo de espera "
                                      f"({self.max_s / 3600:g} h; ajuste com --espera-max)")
                self.liberado.clear()
                self.esperas += 1
                dur = int(alvo - agora)
                origem = "" if exc.reset else " (horário de reset desconhecido: testando a cada "\
                                              f"{self.sondagem // 60} min)"
                print(f"\n[limite] {exc}. Pausado até {time.strftime('%d/%m %H:%M', time.localtime(alvo))} "
                      f"(~{dur // 3600}h{dur % 3600 // 60:02d}min){origem}. Retoma sozinho; "
                      f"Ctrl+C interrompe e rodar de novo continua.", file=sys.stderr)
        if lider:
            while time.time() < alvo and not self.parar.is_set():
                time.sleep(min(1.0, max(0.0, alvo - time.time())))
            with self.lock:
                self.liberado_em = time.time()
                self.liberado.set()
            if not self.parar.is_set():
                print("[limite] retomando a triagem.", file=sys.stderr)
        else:
            self.aguardar()


def montar_params(modelo, prompt, titulo, resumo, cache_1h):
    cache = {"type": "ephemeral"}
    if cache_1h:
        cache["ttl"] = "1h"
    return {
        "model": modelo,
        "max_tokens": 500,
        "system": [{"type": "text", "text": prompt, "cache_control": cache}],
        "tools": [FERRAMENTA],
        "tool_choice": {"type": "tool", "name": NOME_FERRAMENTA},
        "messages": [{"role": "user", "content": montar_texto(titulo, resumo)}],
    }


def normalizar_resposta(dado):
    """Valida a saída da ferramenta; respostas inconsistentes viram maybe/M."""
    cls = str(dado.get("classification", "")).strip().lower()
    code = str(dado.get("code", "")).strip().upper()
    ic = [x for x in dado.get("ic_atendidos", []) or [] if x in IC_VALIDOS]
    ec = [x for x in dado.get("ec_aplicados", []) or [] if x in EC_VALIDOS]
    just = re.sub(r'\s+', ' ', str(dado.get("justificativa", ""))).strip()
    if cls not in CODIGOS or code not in CODIGOS[cls]:
        just = f"[resposta inconsistente: {cls}/{code}] " + just
        cls, code = "maybe", "M"
    return {"classification": cls, "code": code, "ic": ic, "ec": ec, "justificativa": just}


def extrair_tool_input(mensagem):
    for bloco in mensagem.content:
        if getattr(bloco, "type", None) == "tool_use" and bloco.name == NOME_FERRAMENTA:
            return bloco.input
    raise ValueError("resposta sem chamada da ferramenta")


# =====================================================================================
# Cache de respostas (JSONL)
# =====================================================================================
def hash_titulo(t):
    return hashlib.sha1(norm_titulo(t).encode()).hexdigest()[:12]


def carregar_cache(caminho):
    cache = {}
    if os.path.exists(caminho):
        with open(caminho, encoding='utf-8') as f:
            for linha in f:
                linha = linha.strip()
                if linha:
                    r = json.loads(linha)
                    cache[r["id"]] = r
    return cache


def gravar_cache(caminho, registro):
    with open(caminho, 'a', encoding='utf-8') as f:
        f.write(json.dumps(registro, ensure_ascii=False) + '\n')


def _manter_acordado():
    """No macOS, impede o repouso por inatividade enquanto o script espera o limite renovar."""
    if sys.platform == "darwin" and shutil.which("caffeinate"):
        try:
            return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except OSError:
            return None
    return None


# =====================================================================================
# Execução: modo direto (backend login ou api)
# =====================================================================================
def rodar_direto(itens, args, prompt, cache_path, client=None):
    api = args.backend == "api"
    parar = threading.Event()
    espera = EsperaLimite(parar, args.espera_max, args.espera_sondagem)

    def tarefa(item):
        while True:
            espera.aguardar()
            if parar.is_set():
                raise LimiteDeUso("interrompido")
            t0 = time.time()
            try:
                return chamada(item)
            except LimiteDeUso as e:
                if not (args.esperar_limite and e.temporario):
                    raise
                espera.limite(e, t0)  # bloqueia até renovar e então tenta o mesmo artigo de novo

    def chamada(item):
        if api:
            params = montar_params(args.modelo, prompt, item["titulo"], item["resumo"], args.cache_1h)
            try:
                msg = client.messages.create(**params)
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ in ("AuthenticationError", "PermissionDeniedError"):
                    raise LimiteDeUso(f"autenticação recusada pela API: {e}")
                raise
            return item, normalizar_resposta(extrair_tool_input(msg)), getattr(msg, "usage", None)
        dado, custo = chamar_claude(args.modelo, prompt, item["titulo"], item["resumo"])
        return item, normalizar_resposta(dado), custo

    feitos, erros, equiv = 0, 0, 0.0
    uso = Counter()
    limite = None
    caf = _manter_acordado() if args.esperar_limite and not api else None
    ex = ThreadPoolExecutor(max_workers=args.workers)
    futuros = [ex.submit(tarefa, it) for it in itens]
    try:
        for fut in as_completed(futuros):
            try:
                item, res, extra = fut.result()
            except LimiteDeUso as e:
                if not parar.is_set():
                    limite = str(e)
                    parar.set()
                    for f in futuros:
                        f.cancel()
                continue
            except Exception as e:  # noqa: BLE001
                erros += 1
                print(f"  erro: {e}", file=sys.stderr)
                continue
            gravar_cache(cache_path, {"id": item["id"], "key": item["key"], "th": item["th"],
                                      "modelo": args.modelo, **res})
            feitos += 1
            if api:
                if extra is not None:
                    for campo in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                  "cache_creation_input_tokens"):
                        uso[campo] += getattr(extra, campo, 0) or 0
            else:
                equiv += extra
            if feitos % 25 == 0 or feitos == len(itens):
                print(f"  {feitos}/{len(itens)} avaliados", file=sys.stderr)
    except KeyboardInterrupt:
        parar.set()
        for f in futuros:
            f.cancel()
        print("\nInterrompido. Rode o mesmo comando para continuar.", file=sys.stderr)
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
        if caf:
            caf.terminate()
    if espera.esperas:
        print(f"  pausas por limite de uso: {espera.esperas}", file=sys.stderr)
    if api:
        if uso:
            print("  tokens: " + ", ".join(f"{k}={v}" for k, v in uso.items()), file=sys.stderr)
    else:
        print(f"  uso equivalente na API: ~US$ {equiv:.2f} (não cobrado; conta contra o limite da assinatura)",
              file=sys.stderr)
    if limite:
        dica = "" if args.esperar_limite or api else \
            " Dica: use --esperar-limite para o script aguardar a renovação sozinho."
        print(f"\nParado: {limite}\nCorrija (limite da assinatura, /login ou chave) e rode o mesmo "
              f"comando de novo para continuar de onde parou.{dica}", file=sys.stderr)
    if erros:
        print(f"  {erros} erro(s): rode o mesmo comando de novo para reenviar só esses.", file=sys.stderr)


# =====================================================================================
# Execução: modo lote (Message Batches API)
# =====================================================================================
def rodar_lote(client, itens, args, prompt, cache_path):
    estado_path = cache_path.replace('.respostas.jsonl', '.lotes.json')
    por_id = {it["id"]: it for it in itens}

    if os.path.exists(estado_path):
        with open(estado_path, encoding='utf-8') as f:
            estado = json.load(f)
        print(f"Retomando {len(estado['lotes'])} lote(s) já enviado(s).", file=sys.stderr)
    else:
        if not itens:
            return
        estado = {"modelo": args.modelo, "lotes": []}
        for i in range(0, len(itens), args.tamanho_lote):
            parte = itens[i:i + args.tamanho_lote]
            reqs = [{"custom_id": it["id"],
                     "params": montar_params(args.modelo, prompt, it["titulo"], it["resumo"], args.cache_1h)}
                    for it in parte]
            lote = client.messages.batches.create(requests=reqs)
            estado["lotes"].append({"id": lote.id, "n": len(parte)})
            print(f"Lote enviado: {lote.id} ({len(parte)} artigos)", file=sys.stderr)
            with open(estado_path, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=1)

    # aguarda o processamento
    pendentes = [l["id"] for l in estado["lotes"]]
    while pendentes:
        aindas = []
        for bid in pendentes:
            b = client.messages.batches.retrieve(bid)
            rc = b.request_counts
            print(f"  {bid}: {b.processing_status} | ok={rc.succeeded} erro={rc.errored} "
                  f"processando={rc.processing} expirado={rc.expired}", file=sys.stderr)
            if b.processing_status != "ended":
                aindas.append(bid)
        pendentes = aindas
        if pendentes:
            print(f"  aguardando {args.intervalo}s (Ctrl+C para sair; rode de novo para retomar)...",
                  file=sys.stderr)
            time.sleep(args.intervalo)

    # coleta resultados
    ok, falhas = 0, Counter()
    for l in estado["lotes"]:
        for r in client.messages.batches.results(l["id"]):
            item = por_id.get(r.custom_id)
            if r.result.type != "succeeded":
                falhas[r.result.type] += 1
                continue
            try:
                res = normalizar_resposta(extrair_tool_input(r.result.message))
            except Exception:  # noqa: BLE001
                falhas["sem_ferramenta"] += 1
                continue
            reg = {"id": r.custom_id, "modelo": estado["modelo"], **res}
            if item:
                reg.update({"key": item["key"], "th": item["th"]})
            gravar_cache(cache_path, reg)
            ok += 1
    os.remove(estado_path)
    print(f"Resultados gravados: {ok}", file=sys.stderr)
    if falhas:
        print(f"Falhas: {dict(falhas)}. Rode o mesmo comando de novo para reenviar só esses.",
              file=sys.stderr)


# =====================================================================================
# Saída
# =====================================================================================
def escrever_saida(caminho, recs, sep, linhas, extras, final_nl, manter=None):
    def campos_csv(vals):
        buf = io.StringIO()
        csv.writer(buf, delimiter=sep, lineterminator='').writerow(vals)
        return buf.getvalue()

    def anexar(reg, vals):
        cr = '\r' if reg.endswith('\r') else ''
        base = reg[:-1] if cr else reg
        return base + sep + campos_csv(vals) + cr

    out = [anexar(recs[0], NOVAS_COLUNAS)]
    for j, (reg, ex) in enumerate(zip(recs[1:], extras)):
        if manter is not None and j not in manter:
            continue
        out.append(reg if ex is None else anexar(reg, ex))
    with open(caminho, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(out) + ('\n' if final_nl else ''))


# =====================================================================================
# Principal
# =====================================================================================
def main():
    ap = argparse.ArgumentParser(description="Triagem por título + resumo com o Claude (login da conta via `claude -p`, ou API).")
    ap.add_argument("entrada", help="planilha CSV (ex.: saída de triagem_titulos.py)")
    ap.add_argument("-o", "--saida", help="CSV de saída (padrão: <entrada>_resumo.csv)")
    aqui = os.path.dirname(os.path.abspath(__file__))
    prompt_padrao = next((p for p in (os.path.join(aqui, n) for n in ("prompt_triagem_resumo.md", "prompt.md"))
                          if os.path.exists(p)), os.path.join(aqui, "prompt_triagem_resumo.md"))
    ap.add_argument("--prompt", default=prompt_padrao, help="arquivo com o prompt da etapa de resumo")
    ap.add_argument("--modelo", default=MODELO_PADRAO, help=f"modelo do Claude (padrão: {MODELO_PADRAO})")
    ap.add_argument("--filtro", default="yes,maybe",
                    help="valores da coluna classification enviados ao Claude (padrão: yes,maybe)")
    ap.add_argument("--coluna-classificacao", default="classification")
    ap.add_argument("--coluna-titulo", default="title")
    ap.add_argument("--coluna-resumo", default="abstract")
    ap.add_argument("--coluna-key", default="key")
    ap.add_argument("--sem-resumo", choices=["maybe", "enviar"], default="maybe",
                    help="artigo sem resumo: marcar maybe sem chamar o Claude (padrão) ou enviar só o título")
    ap.add_argument("--amostra", type=int, metavar="N",
                    help="avalia só os N primeiros artigos elegíveis da planilha, na ordem (calibração)")
    ap.add_argument("--so-avaliados", action="store_true",
                    help="grava no CSV de saída só as linhas enviadas ao Claude (ex.: com --amostra 50, só 50 linhas)")
    ap.add_argument("--backend", choices=["login", "api"], default="login",
                    help="login: `claude -p` com a conta (padrão, sem API key); api: API da Anthropic (ANTHROPIC_API_KEY)")
    ap.add_argument("--modo", choices=["lote", "direto"], default=None,
                    help="só backend api: lote (padrão; Message Batches, 50%% mais barato) ou direto. "
                         "No backend login é sempre direto")
    ap.add_argument("--workers", type=int, default=None,
                    help="chamadas em paralelo (padrão: 3 no login, 4 na API)")
    ap.add_argument("--esperar-limite", action="store_true",
                    help="[login] se o limite de uso da assinatura (janela de 5 h) acabar, esperar a renovação "
                         "e continuar sozinho, em vez de parar")
    ap.add_argument("--espera-max", type=float, default=6.0, metavar="HORAS",
                    help="[login] maior espera aceita por vez (padrão: 6; um limite semanal passa disso e para)")
    ap.add_argument("--espera-sondagem", type=int, default=15, metavar="MIN",
                    help="[login] se o horário de reset for desconhecido, testar de novo a cada N min (padrão: 15)")
    ap.add_argument("--tamanho-lote", type=int, default=5000, help="[api/lote] artigos por lote")
    ap.add_argument("--intervalo", type=int, default=60, help="[api/lote] segundos entre consultas ao lote")
    ap.add_argument("--cache-1h", action="store_true", help="[api] cache do prompt por 1 h (útil em lotes grandes)")
    ap.add_argument("--simular", action="store_true", help="não chama o Claude; mostra contagens e um exemplo")
    args = ap.parse_args()
    if args.backend == "login":
        if args.modo == "lote":
            sys.exit("--modo lote só existe no backend api (o `claude -p` não tem Message Batches).")
        args.modo = "direto"
        if args.cache_1h:
            print("Aviso: --cache-1h só vale no backend api; ignorado.", file=sys.stderr)
    else:
        args.modo = args.modo or "lote"
        if args.esperar_limite:
            print("Aviso: --esperar-limite só vale no backend login; ignorado.", file=sys.stderr)
    if args.workers is None:
        args.workers = 3 if args.backend == "login" else 4

    saida = args.saida or os.path.splitext(args.entrada)[0] + "_resumo.csv"
    cache_path = saida + ".respostas.jsonl"
    with open(args.prompt, encoding='utf-8') as f:
        prompt = f.read()

    recs, sep, colunas, linhas, final_nl = ler_planilha(args.entrada)
    c_tit, c_res = _col(colunas, args.coluna_titulo), _col(colunas, args.coluna_resumo)
    c_cls, c_key = _col(colunas, args.coluna_classificacao), _col(colunas, args.coluna_key)
    if not c_tit:
        sys.exit(f"Coluna de título '{args.coluna_titulo}' não encontrada.")
    if not c_res:
        sys.exit(f"Coluna de resumo '{args.coluna_resumo}' não encontrada.")
    filtro = {x.strip().lower() for x in args.filtro.split(',') if x.strip()}
    if not c_cls:
        print("Aviso: sem coluna de classificação do título; todas as linhas serão avaliadas.",
              file=sys.stderr)

    # seleção, duplicatas (EC3) e artigos sem resumo
    primeira = {}
    itens, sem_resumo, dup_de, selecionada = [], {}, {}, {}
    for i, row in enumerate(linhas):
        if row is None:
            continue
        rid = f"r{i}"
        key = row.get(c_key, '') if c_key else rid
        sel = (not c_cls) or row.get(c_cls, '').strip().lower() in filtro
        selecionada[i] = sel
        nt = norm_titulo(row[c_tit])
        if nt and nt in primeira:
            dup_de[i] = primeira[nt]
            continue
        if nt:
            primeira[nt] = (i, key)
        if not sel:
            continue
        resumo = row.get(c_res, '')
        if len(resumo.strip()) < MIN_RESUMO and args.sem_resumo == "maybe":
            sem_resumo[i] = True
            continue
        itens.append({"id": rid, "idx": i, "key": key, "titulo": row[c_tit], "resumo": resumo,
                      "th": hash_titulo(row[c_tit])})

    if args.amostra:
        itens = itens[:args.amostra]  # já estão na ordem da planilha

    cache = carregar_cache(cache_path)
    pendentes = [it for it in itens if not (it["id"] in cache and cache[it["id"]].get("th") == it["th"])]

    print(f"Linhas: {sum(r is not None for r in linhas)} | selecionadas: {sum(selecionada.values())} | "
          f"duplicatas: {len(dup_de)} | sem resumo: {len(sem_resumo)} | para o Claude: {len(itens)} "
          f"(já avaliados: {len(itens) - len(pendentes)}, pendentes: {len(pendentes)})", file=sys.stderr)

    if args.simular:
        if args.backend == "api":
            chars = sum(len(montar_texto(it["titulo"], it["resumo"])) for it in pendentes)
            print(f"Backend api, modo {args.modo}. Prompt do sistema: ~{len(prompt) // 4} tokens (com cache, "
                  f"cobrado integralmente só na 1ª leitura). Conteúdo dos artigos: ~{chars // 4} tokens no "
                  f"total. Saída: ~100-150 tokens por artigo.", file=sys.stderr)
            if pendentes:
                ex = montar_params(args.modelo, prompt, pendentes[0]["titulo"], pendentes[0]["resumo"], args.cache_1h)
                ex["system"] = [{**ex["system"][0], "text": ex["system"][0]["text"][:200] + " [...]"}]
                print(json.dumps(ex, ensure_ascii=False, indent=1))
        else:
            print(f"Backend login. Prompt do sistema: ~{len(prompt) // 4} tokens por chamada. "
                  f"{len(pendentes)} chamadas `claude -p` (~3-5 s cada, {args.workers} em paralelo).",
                  file=sys.stderr)
            if pendentes:
                cmd = montar_comando(args.modelo, prompt[:200] + " [...]")
                cmd[cmd.index("--json-schema") + 1] = "<esquema JSON>"
                print("Comando: " + " ".join(repr(c) if " " in c or not c else c for c in cmd), file=sys.stderr)
                print("Entrada (stdin):\n" + montar_texto(pendentes[0]["titulo"], pendentes[0]["resumo"]))
        return

    if pendentes:
        client = None
        if args.backend == "api":
            if not os.environ.get("ANTHROPIC_API_KEY"):
                sys.exit("Backend api: defina ANTHROPIC_API_KEY (export ANTHROPIC_API_KEY=sk-ant-...).")
            try:
                import anthropic
            except ImportError:
                sys.exit("Instale o SDK: pip install anthropic")
            client = anthropic.Anthropic(max_retries=6)
        elif not shutil.which("claude"):
            sys.exit("Comando `claude` não encontrado. Instale o Claude Code e faça login (/login).")
        try:
            if args.modo == "lote":
                rodar_lote(client, pendentes, args, prompt, cache_path)
            else:
                rodar_direto(pendentes, args, prompt, cache_path, client)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ in ("AuthenticationError", "PermissionDeniedError"):
                sys.exit(f"A API recusou a chave ({type(e).__name__}). Confira ANTHROPIC_API_KEY.")
            raise
        cache = carregar_cache(cache_path)

    # monta as colunas novas
    avaliados_ids = {it["id"] for it in itens}
    extras, cont = [], Counter()
    for i, row in enumerate(linhas):
        if row is None:
            extras.append(None)
            continue
        rid, sel = f"r{i}", selecionada[i]
        dup = dup_de.get(i)
        dup_key = dup[1] if dup else ''
        if dup and sel:
            vals = ["no", "EC3", "", "", f"Duplicata de {dup_key}.", "no"]
        elif not sel:
            vals = ["", "", "", "", "", "no"]
        elif i in sem_resumo:
            vals = ["maybe", "M", "", "", "Sem resumo na planilha: decidir pelo texto completo.", "maybe"]
        elif rid in cache and rid in avaliados_ids:
            r = cache[rid]
            vals = [r["classification"], r["code"], ';'.join(r["ic"]), ';'.join(r["ec"]),
                    r["justificativa"], r["classification"]]
        else:
            # selecionado, mas não avaliado (fora da amostra, falha ou limite de uso)
            vals = ["", "", "", "", "", ""]
        extras.append([dup_key] + vals)
        if sel:
            cont[(vals[0], vals[1])] += 1

    manter = {it["idx"] for it in itens} if args.so_avaliados else None
    escrever_saida(saida, recs, sep, linhas, extras, final_nl, manter)

    print("\nResultado da etapa de resumo (linhas avaliadas):", file=sys.stderr)
    for cls in ("yes", "maybe", "no"):
        cods = sorted(((c, n) for (k, c), n in cont.items() if k == cls), key=lambda x: -x[1])
        if cods:
            print(f"  {cls:<5} {sum(n for _, n in cods):>6}   " + " · ".join(f"{c} {n}" for c, n in cods),
                  file=sys.stderr)
    falt = cont.get(("", ""), 0)
    if falt and not args.amostra:
        print(f"  {falt} artigo(s) selecionado(s) ainda sem resposta: rode o mesmo comando de novo.",
              file=sys.stderr)
    print(f"Saída gravada em: {saida}", file=sys.stderr)


if __name__ == "__main__":
    main()
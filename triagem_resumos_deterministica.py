#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
triagem_resumos_deterministica.py: triagem por TÍTULO + RESUMO da RSL, feita só com regras
em Python (expressões regulares e pontuação). Não chama o Claude, não usa API nem login,
não tem custo nem limite de uso, e dá sempre o mesmo resultado para a mesma entrada.

É a versão "sem IA" do triagem_resumos.py: lê a mesma planilha, aplica os mesmos critérios do
prompt.md (arquétipos A-E, exclusões NIC7/NIC1/EC1/EC2/EC5/NIC8) e grava as mesmas colunas:
  duplicado_de, abstract_classification, abstract_code, abstract_ic, abstract_ec,
  abstract_justificativa, final_classification
A justificativa diz qual regra decidiu e quais trechos do título/resumo a acionaram.

LIMITAÇÃO (leia antes de usar)
  O prompt pede para julgar "pelo significado" (por exemplo, se um modelo multimodal tem
  contribuição arquitetural ou é só uma rede de ML). Regras só enxergam palavras. Elas acertam bem
  os casos de vocabulário claro (survey, agentes, RAG, benchmark, segurança, "multimodal" em
  sentido não computacional) e erram mais na fronteira entre E (framework multimodal), EC1 (modelo
  de ML puro) e M (maybe). Por isso o script foi calibrado contra os rótulos do Claude em
  amostra_1000.csv e contra o conjunto ouro (artigos_ouro.csv); veja o README.md para os números.
  Trate o resultado como triagem de apoio e revise os "maybe" e os "yes".

USO
  python3 triagem_resumos_deterministica.py triagem_titulo.csv --amostra 50 --so-avaliados -o amostra.csv
  python3 triagem_resumos_deterministica.py triagem_titulo.csv -o triagem_resumo.csv
  python3 triagem_resumos_deterministica.py --titulo "..." --resumo "..."   # testa um artigo

Requer só Python 3.8+ (biblioteca padrão) e os arquivos triagem_titulos.py e triagem_resumos.py
na mesma pasta (reaproveita o vocabulário de sinais e a leitura/escrita da planilha).
"""
import argparse
import os
import re
import sys

import triagem_resumos as TR
import triagem_titulos as TT


def R(p):
    return re.compile(p, re.I)


# =====================================================================================
# Vocabulário (além do que já existe em triagem_titulos.py)
# =====================================================================================
# Estudo secundário descrito no resumo ("this survey", "we review", "a systematic literature review"...)
SEC_ABS = R(r"\b(this|the present|our|the following|present)\s+(paper|article|work|study|survey|review|chapter|"
            r"report|manuscript)?\s*(\w+\s){0,2}"
            r"(survey|review|overview|taxonomy|systemati[sz]ation|mapping study|roadmap|tutorial|synthesis)\b|"
            r"\b(we|authors?)\s+(\w+\s){0,2}(survey|review|summari[sz]e|categori[sz]e|classify|synthesi[sz]e|"
            r"systematically (review|analy[sz]e)|taxonomi[sz]e)\b|"
            r"\b(comprehensive|systematic|extensive|in-depth|thorough|brief|critical|structured|up-to-date|"
            r"state-of-the-art) (literature )?(survey|review|overview|analysis of (the )?(literature|existing|recent))|"
            r"\b(literature|systematic|scoping|mapping) (review|survey|study)\b|\bsystematization of knowledge\b|"
            r"\breviews? (the )?(recent|existing|current|state|literature|advances|developments)\b|"
            r"\bpapers? (published|from)\b.{0,40}\b(20\d\d|years)\b|\b\d+ (studies|papers|articles)\b|"
            r"\btaxonomy (of|for|that|which)\b|\bwe (propose|present|introduce|develop) a (new |novel |unified |comprehensive )?taxonomy\b")
SEC_TITLE_STRONG = R(r"\bsurvey\b|\breview\b|\btaxonom|\boverview\b|\bsok\b|systematization|road[\s-]?map|"
                     r"mapping study|literature|state[\s-]of[\s-]the[\s-]art|\bperspective|\btutorial\b|"
                     r"challenges and|opportunities|future directions|recent advances|\badvances in\b|\bprimer\b")
CONCEPTUAL = R(r"\b(position paper|conceptual (framework|model|paper|analysis)|vision paper|we argue|we envision|"
               r"we advocate|we contend|we posit|this (position|vision|perspective)|theoretical (framework|analysis|"
               r"perspective)|thought experiment|we outline|we discuss|we reflect|manifesto|call to action)\b")
IMPL = R(r"\b(experiment|evaluat|benchmark|dataset|data set|results? (show|demonstrate|indicate|reveal)|outperform|"
         r"accuracy|f1|auc|implement|prototype|case study|empirical|ablation|baseline|state-of-the-art|deployed|"
         r"user study|real-world|open-?source|github)\w*")
DESIGN_WORDS = R(r"\b(modules?|components?|layers?|stages?|branch(es)?|sub-?networks?|blocks?|phases?|"
                 r"orchestrat\w*|planner|planning|executor|controller|router|retriever|generator|encoder|decoder|"
                 r"memory|toolkit|plugin|interface|backbone|pipeline|workflow|hierarch\w*|modular|"
                 r"fusion (module|layer|block|mechanism|network|strategy)|gate[ds]?|coordinator|manager)\b")
STRUCT_FW = R(r"\b(propose|present|introduce|develop|design|build|construct|devise|establish|put forward|"
              r"implement|create|offer|describe)[sd]?\b[^.;]{0,90}?\b(framework|system|architecture|pipeline|platform|"
              r"toolkit|workflow|infrastructure|middleware|library|toolbox|engine|environment|ecosystem|"
              r"multi-?agent|agentic)\b")
FW_TITLE = R(r"\b(framework|system|architecture|pipeline|platform|toolkit|workflow|infrastructure|middleware|"
             r"library|toolbox|engine|ecosystem|orchestrat\w*|copilot|assistant|agentic|multi-?agent|agents?)\b")
MODEL_ONLY = R(r"\b(propose|present|introduce|develop|design)[sd]?\b[^.;]{0,60}?\b(a|an|the)\s+(\w+[\s-]){0,4}"
               r"(network|net|model|method|approach|algorithm|module|mechanism|loss|attention|transformer|"
               r"classifier|detector|estimator|predictor|extractor|learner)\b")
ML_TRICK = R(r"\b(loss|contrastive|distill\w*|pre-?train\w*|fine-?tun\w*|regulari[sz]\w*|curriculum|self-?supervised|"
             r"semi-?supervised|weakly-?supervised|few-?shot|zero-?shot|augment\w*|adversarial training|"
             r"knowledge transfer|domain adaptation|meta-?learning|transfer learning|prompt (tuning|learning)|"
             r"adapter|lora|alignment|attention mechanism|self-?attention|cross-?attention|mamba|state space|kolmogorov)\b")
EVAL_ONLY = R(r"\b(we|this (paper|study|work)) (\w+\s){0,2}(evaluate|benchmark|assess|investigate|analy[sz]e|study|examine|"
              r"probe|compare|measure|quantify|audit|explore whether|ask whether|test)\b[^.]{0,80}\b(capabilit|abilit|"
              r"performance|limitation|behaviou?r|robustness|reliability|existing|current|state-of-the-art|"
              r"models?|llms?|mllms?|vlms?|gpt|chatgpt|gemini)\b")
EVAL_PROPOSES = R(r"\b(we|this (paper|study|work)) (\w+\s){0,2}(propose|introduce|present|develop|design|build|"
                  r"construct)[sd]?\b[^.]{0,80}\b(framework|method|model|approach|system|architecture|pipeline|"
                  r"algorithm|network|module|agent)\b")
BENCH_ABS = R(r"\b(we|this (paper|study|work)) (\w+\s){0,2}(introduce|present|construct|build|release|curate|collect|"
              r"propose|create|contribute|annotate)[sd]?\b[^.]{0,60}\b(benchmark|dataset|data set|corpus|testbed|"
              r"leaderboard|evaluation suite|test suite)\b|\b(new|novel|large-scale|comprehensive) "
              r"(benchmark|dataset|corpus)\b")
APP_DOMAIN = R(r"\b(patients?|clinical|clinic|diagnos\w*|disease|cancer|tumou?rs?|medical|health\w*|radiolog\w*|"
               r"pathology|surgical|hospital|agricultur\w*|crop|plant|farm\w*|manufactur\w*|industrial|factory|"
               r"power|grid|energy|traffic|driving|vehicle|autonomous driving|education\w*|student|school|"
               r"financ\w*|stock|market|trading|news|social media|sentiment|emotion|depress\w*|mental|stress|"
               r"fatigue|athlete|sport\w*|music|art|legal|law|e-commerce|recommendation|remote sensing|satellite|"
               r"geolog\w*|mining|construction|building|smart (city|home)|iot|wireless|5g|6g|radar|sonar|"
               r"defect|inspection|maintenance|fault)\b")
SECURITY_ABS = R(r"\b(jailbreak\w*|backdoor\w*|adversarial (attack|example|robustness|perturbation)|poison\w*|"
                 r"attacks?\b|vulnerabilit\w*|red[\s-]?team\w*|safety|misuse|privacy leak\w*|membership inference|"
                 r"watermark\w*|deepfake|hallucinat\w*|bias(es)?\b|fairness|toxic\w*|harmful|trustworth\w*)")
NARROW_ABS = R(r"\b(token (compression|pruning|merging|reduction)|visual token|kv[\s-]?cache|quanti[sz]\w*|prun\w*|"
               r"speculative decoding|model compression|distillation|inference (speed|acceleration|efficien\w*)|"
               r"parameter-efficient|\blora\b|\bpeft\b|preference (optimi[sz]ation|alignment)|\bdpo\b|\brlhf\b|"
               r"reward model|\bgrpo\b|\bppo\b|instruction tuning|fine-?tun\w* (strategy|method|approach|data)|"
               r"decoding (strategy|method)|positional encoding|tokeni[sz]er|unlearning|model merging|"
               r"model editing|continual (learning|pre-?training)|data (selection|augmentation|synthesis|filtering))")
RAG_STRONG = R(r"\b(rag|retrieval[\s-]augmented|retriev\w*|re-?rank\w*|vector (database|store|search)|indexing|"
               r"knowledge base|search engine|long-term memory|memory (bank|module|retrieval))\b")
NONTEXT_ABS = R(r"\b(video\w*|image\w*|visual\w*|vision|audio|speech|document\w*|chart\w*|tables?|tabular|"
                r"graphs?|multi-?modal\w*|cross-?modal|maps?|slides?|figures?|screenshots?|pdf|photos?|3d|scene|"
                r"heterogeneous|multi-?source|radiolog\w*|medical imag\w*|music|sound|remote sensing|satellite|"
                r"knowledge graph|time[\s-]?series|sensors?|ocr|mllms?|vlms?|lmms?|lvlms?)\b")
LLM_AGENT_CTX = R(r"\b(llms?|mllms?|vlms?|lmms?|gpt\w*|large language|language models?|foundation models?|"
                  r"multimodal (large )?(language )?models?|generative ai|chatgpt|claude|gemini|llama|qwen|deepseek|"
                  r"tool[\s-](use|using|calling|learning)|function[\s-]calling|computer[\s-]use|gui|react|"
                  r"chain[\s-]of[\s-]thought|prompt\w*|reasoning|planner|copilot|autonomous)\b")
AGENT_WORD = R(r"\bagent(s|ic|ics)?\b|\bmulti-?agent|\bagentic|\bcopilot|\bco-pilot|orchestrat\w*")
ORG_WORDS = R(r"\b(takes?|taking|receives?|accepts?) [^.;]{0,70}\b(as )?(inputs?|input)\b|\b(delivers?|outputs?|produces?|"
              r"generates?|yields?|returns?) [^.;]{0,50}\b(prediction|output|score|result|answer|report|response|"
              r"label|text|caption)|\bparallel\b|\b(two|three|dual|multi)[\s-](stream|branch|stage|tier|level|"
              r"module|phase|component|agent)s?\b|\bbranch(es)?\b|\bstages?\b|\bfirst,? .{0,80}\b(then|second|next|finally)\b|"
              r"\bfusion (layer|module|block|mechanism|network|strategy)\b|\b(encoder|decoder)s?\b|\bpipeline\b|"
              r"\bmodules?\b|\bcomponents?\b|\blayers?\b|\bend-to-end\b|\bbackbone\b|\bsub-?(network|module|system)s?\b")
GENERIC_PROBLEM = R(r"\b(however|existing (methods|approaches|models)|challenges?|limitations?|struggle|suffer|"
                    r"lack|fail|difficult|remains? (challenging|an open))\b")


# =====================================================================================
# Utilitários
# =====================================================================================
def _norm(txt):
    return re.sub(r"\s+", " ", (txt or "").replace("&amp;", "&")).strip()


def _ev(rx, txt, n=2, largura=48):
    """Até n trechos curtos (sem repetição) que acionaram a regex, para a justificativa."""
    achados, vistos = [], set()
    for m in rx.finditer(txt):
        trecho = _norm(m.group(0)).strip(" .,;:")
        if len(trecho) > largura:  # corta em fim de palavra
            trecho = trecho[:largura].rsplit(" ", 1)[0].rstrip(" .,;:") + "…"
        k = trecho.lower()
        if trecho and k not in vistos:
            vistos.add(k)
            achados.append(trecho)
        if len(achados) >= n:
            break
    return achados


def _fmt(*grupos):
    """Junta trechos de evidência entre aspas; ignora grupos vazios."""
    todos = [t for g in grupos for t in g]
    return "; ".join(f"“{t}”" for t in todos)


def _quantos(rx, txt):
    return len({m.group(0).lower() for m in rx.finditer(txt)})


# =====================================================================================
# Extração de sinais
# =====================================================================================
def extrair(titulo, resumo):
    t = _norm(titulo)
    a = _norm(resumo)
    ta = (t + ". " + a).strip()
    f = {"t": t, "a": a, "ta": ta}

    f["n0"] = bool(TT.NON_ARTICLE.search(t)) or TT.non_english(t)
    f["llm"] = bool(TT.LLM_RX.search(ta))
    f["mm"] = TT.mm_signal(ta)
    sem_noncomp = TT.AGENT_NONCOMP.sub(" ", ta)
    f["agent_t"] = bool(AGENT_WORD.search(TT.AGENT_NONCOMP.sub(" ", t)))
    f["agent_n"] = len(re.findall(r"\bagent(s|ic|ics)?\b|\bmulti-?agent|\bagentic", sem_noncomp, re.I))
    f["agent_ctx"] = bool(LLM_AGENT_CTX.search(ta))
    f["agent_classic"] = bool(TT.AGENT_CLASSIC.search(ta)) and not f["llm"]
    f["n1"] = bool(TT.N1_ALWAYS.search(ta)) and not (
        TT.mm_signal(TT.N1_ALWAYS.sub(" ", ta)) == "strong" or TT.LLM_RX.search(TT.N1_ALWAYS.sub(" ", ta)))

    f["sec_t"] = bool(SEC_TITLE_STRONG.search(TT.SEC_PRE_REMOVE.sub(" ", t))) or bool(TT.SEC.search(TT.SEC_PRE_REMOVE.sub(" ", t)))
    f["sec_a"] = bool(SEC_ABS.search(a))
    f["sec_ev"] = _ev(SEC_TITLE_STRONG, t) or _ev(SEC_ABS, a)
    f["cognitive"] = bool(TT.COGNITIVE.search(ta))
    f["conceptual"] = bool(CONCEPTUAL.search(a)) and len(IMPL.findall(a)) <= 1

    f["bench_t"] = bool(TT.BENCH_HEAD.search(t)) and not TT.BENCH_WITH_SYS.search(t)
    f["bench_a"] = bool(BENCH_ABS.search(a))
    f["eval_a"] = bool(EVAL_ONLY.search(a)) and not EVAL_PROPOSES.search(a)
    f["eval_t"] = bool(TT.N3.search(t) or TT.N3_Q.search(t)) and not TT.N3_PROPOSE.search(t)
    f["sec_risk_t"] = bool(TT.N5.search(t))
    f["sec_risk_a"] = len(SECURITY_ABS.findall(a))
    f["narrow_t"] = bool(TT.N4_CLEAR.search(t))
    f["narrow_a"] = len(NARROW_ABS.findall(a))

    f["fw_t"] = bool(FW_TITLE.search(t))
    f["fw_a"] = bool(STRUCT_FW.search(a))
    f["fw_ev"] = _ev(STRUCT_FW, a, 1) or _ev(FW_TITLE, t, 1)
    f["org_n"] = _quantos(ORG_WORDS, a)
    f["design_n"] = _quantos(DESIGN_WORDS, a)
    f["design_ev"] = _ev(DESIGN_WORDS, a, 3)
    f["fusion"] = bool(re.search(r"\bfusion|\bfuse|\bfusing|\bintegrat|\bcombin|\bjoint|\balign", a, re.I))
    f["model_only"] = bool(MODEL_ONLY.search(a))
    f["trick_n"] = _quantos(ML_TRICK, a)
    f["trick_t"] = bool(ML_TRICK.search(t))
    f["app"] = bool(APP_DOMAIN.search(t))
    f["app_n"] = _quantos(APP_DOMAIN, a)
    f["named"] = TT.proper_name(t)
    f["impl_n"] = len(IMPL.findall(a))

    f["rag_t"] = bool(TT.RETR_STRONG.search(t))
    f["rag_n"] = len(RAG_STRONG.findall(a))
    f["nontext"] = bool(NONTEXT_ABS.search(ta)) or bool(f["mm"])
    f["eng"] = bool(TT.ENG.search(ta))
    f["arch_a"] = len(re.findall(r"\barchitectur\w*|\bplatform\b|\btoolkit\b|\binfrastructure\b|\bmiddleware\b|"
                                 r"\bpipeline\b|\bworkflow\b|\bmodular\b|\bend-to-end system\b|\bsoftware\b|"
                                 r"\bdeploy\w*", a, re.I))
    return f


# =====================================================================================
# Decisão
# =====================================================================================
def _ic(f, extras=()):
    ic = ["IC4", "IC5"]
    if f["mm"] or f["llm"] or f["agent_n"] or f["agent_t"]:
        ic.append("IC7")
    ic.extend(extras)
    ordem = ["IC1", "IC2", "IC3", "IC4", "IC5", "IC7", "IC8", "IC9"]
    return [x for x in ordem if x in set(ic)]


def _r(cls, code, ic, ec, just):
    return {"classification": cls, "code": code, "ic": ic, "ec": ec, "justificativa": just}


def decidir(f, perfil="revocacao"):
    estrito = perfil == "precisao"
    t, a = f["t"], f["a"]
    sinais = bool(f["mm"] or f["llm"] or f["agent_n"] or f["agent_t"])
    tema_ok = bool(f["mm"] or f["llm"] or f["agent_n"] or f["agent_t"] or f["cognitive"])

    # ---- N0
    if not t:
        return _r("no", "N0", [], [], "Registro sem título: não é um artigo avaliável.")
    if f["n0"]:
        return _r("no", "N0", [], [], "Título indica registro que não é artigo (anais, editorial, errata) ou não está em inglês.")

    # ---- NIC7
    if f["n1"]:
        return _r("no", "NIC7", [], [], "“Multimodal” usado em sentido não computacional (transporte, otimização, distribuições, terapia ou educação).")
    if not sinais:
        return _r("no", "NIC7", [], [], "Nem o título nem o resumo trazem dados multimodais, LLM ou agentes.")

    # ---- A: estudo secundário
    if f["sec_t"] or f["sec_a"]:
        if tema_ok:
            risco_estreito = (f["sec_risk_t"] or f["narrow_t"]) and not re.search(
                r"(?i)architect|framework|paradigm|evolution|landscape|ecosystem|agent|design|taxonomy", t)
            if risco_estreito:
                cod = "NIC1"
                return _r("no", cod, _ic(f), [], "Estudo secundário sobre um tema estreito (segurança, robustez ou técnica pontual), sem foco arquitetural.")
            return _r("yes", "A", _ic(f, ["IC1", "IC3", "IC8"]), [],
                      f"Estudo secundário sobre sistemas multimodais, LLM ou agentes ({_fmt(f['sec_ev'])}).")

    # ---- EC5: apenas conceitual
    if f["conceptual"] and not f["impl_n"] and (f["agent_n"] or f["llm"] or f["mm"]):
        return _r("no", "EC5", _ic(f), ["EC5"], "Proposta conceitual/posicionamento, sem implementação, experimento ou validação no resumo.")

    # ---- D: recuperação aumentada com entrada não textual
    if (f["rag_t"] or f["rag_n"] >= 2) and f["nontext"] and (f["fw_t"] or f["fw_a"] or f["design_n"] >= 1 or f["rag_t"]):
        return _r("yes", "D", _ic(f, ["IC1", "IC8", "IC9"]), [],
                  f"Recuperação aumentada com entrada não textual e saída em texto ({_fmt(_ev(RAG_STRONG, a or t))}).")

    # ---- B: sistemas baseados em agentes
    if (f["agent_t"] or f["agent_n"] >= 2) and f["agent_ctx"] and not f["agent_classic"]:
        if f["design_n"] >= 1 or f["fw_a"] or f["fw_t"] or f["agent_t"]:
            return _r("yes", "B", _ic(f, ["IC1", "IC8"]), [],
                      f"Sistema baseado em agentes ({_fmt(_ev(AGENT_WORD, f['ta'], 2), f['design_ev'])}).")
        return _r("maybe", "M", _ic(f), [], "Trata de agentes, mas o resumo não descreve os componentes: decidir pelo texto completo.")

    # ---- pacote de domínio (dataset + benchmark + modelo) apresentado como framework unificado -> E
    if f["bench_a"] and f["fw_a"] and f["model_only"] and f["mm"]:
        return _r("yes", "E", _ic(f, ["IC1", "IC8"]), [],
                  f"Pacote de domínio (dados, benchmark e modelo multimodal) apresentado como framework unificado ({_fmt(f['fw_ev'])}).")

    # ---- NIC1: benchmark/dataset, avaliação, segurança, técnica estreita
    # No perfil "revocacao" só exclui quando o resumo não propõe framework/sistema (evidência forte).
    forte_nic1 = not f["fw_a"]
    if f["bench_t"] or (f["bench_a"] and not f["fw_a"] and not f["model_only"]):
        if estrito or forte_nic1:
            return _r("no", "NIC1", _ic(f), [], "A contribuição principal é benchmark, dataset ou corpus, não uma arquitetura.")
    if f["eval_t"] or f["eval_a"]:
        if estrito or forte_nic1:
            return _r("no", "NIC1", _ic(f), [], "O estudo avalia ou compara modelos existentes, sem propor arquitetura.")
    if f["sec_risk_t"] or f["sec_risk_a"] >= 3:
        if estrito or forte_nic1:
            return _r("no", "NIC1", _ic(f), [], "O foco é segurança, robustez, viés ou alucinação, não arquitetura.")
    if f["narrow_t"] or f["narrow_a"] >= 3:
        if estrito or forte_nic1:
            return _r("no", "NIC1", _ic(f), [], "A contribuição é uma técnica estreita (compressão, ajuste fino, alinhamento ou decodificação).")

    # ---- C / E: framework, sistema ou arquitetura
    estrutura = f["fw_a"] or f["fw_t"]
    if estrutura and (f["design_n"] >= 2 or f["arch_a"] >= 1 or f["org_n"] >= 2) and not (f["trick_t"] and f["model_only"] and not f["fw_a"]):
        cod = "C" if (f["eng"] or f["arch_a"] >= 2) else "E"
        ic = _ic(f, ["IC1", "IC8"] + (["IC2"] if f["eng"] else []))
        return _r("yes", cod, ic, [],
                  f"Framework/sistema com organização descrita ({_fmt(f['fw_ev'], f['design_ev'])}).")

    # ---- EC1 / EC2 / NIC8 / M
    # EC1 forte: rede/atenção/perda sem se declarar framework, ou muitos termos de treino.
    ec1_forte = f["trick_n"] >= 3 or (f["model_only"] and not f["fw_a"] and not f["fw_t"]) or \
        (not f["fw_a"] and not f["fw_t"] and not f["llm"] and f["design_n"] <= 1)
    if f["app"] and f["app_n"] >= 2 and f["design_n"] < 2 and estrito:
        return _r("no", "EC2", _ic(f), ["EC2"], f"Foco na aplicação de domínio ({_fmt(_ev(APP_DOMAIN, f['ta']))}), sem insight arquitetural.")
    if (f["model_only"] or f["trick_n"] >= 1) and (estrito or ec1_forte):
        return _r("no", "EC1", _ic(f), ["EC1"], "Modelo de ML descrito pela rede, atenção ou treino, sem visão de sistema.")
    if estrutura and estrito:
        return _r("no", "NIC8", _ic(f), [], "Propõe um framework, mas não indica componentes nem organização identificáveis.")
    return _r("maybe", "M", _ic(f), [], "O resumo não permite decidir com segurança se há contribuição arquitetural: decidir pelo texto completo.")


def classificar(titulo, resumo, perfil="revocacao"):
    """Classifica um artigo. Devolve dict com classification, code, ic, ec e justificativa.
    perfil: "revocacao" (padrão; `no` só com evidência forte) ou "precisao" (todas as exclusões)."""
    return decidir(extrair(titulo, resumo), perfil)


# =====================================================================================
# Principal
# =====================================================================================
def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--titulo")
    pre.add_argument("--resumo", default="")
    pre.add_argument("--perfil", choices=["revocacao", "precisao"], default="revocacao")
    p_args, resto = pre.parse_known_args()
    if p_args.titulo is not None:  # teste de um único artigo
        r = classificar(p_args.titulo, p_args.resumo, p_args.perfil)
        print(f"{r['classification']};{r['code']};{';'.join(r['ic'])};{';'.join(r['ec'])}\n{r['justificativa']}")
        return

    ap = argparse.ArgumentParser(
        description="Triagem por título + resumo só com regras (sem Claude). Para testar um artigo avulso: "
                    "--titulo \"...\" --resumo \"...\"")
    TR.adicionar_args_planilha(ap)
    ap.add_argument("--perfil", choices=["revocacao", "precisao"], default="revocacao",
                    help="revocacao (padrão): `no` só com evidência forte, na dúvida `maybe`; "
                         "precisao: aplica todas as regras de exclusão (mais `no`, perde mais artigos relevantes)")
    args = ap.parse_args(resto)
    args.perfil = p_args.perfil  # o parser prévio já consumiu --perfil

    saida = args.saida or os.path.splitext(args.entrada)[0] + "_resumo.csv"
    recs, sep, colunas, linhas, final_nl = TR.ler_planilha(args.entrada)
    itens, sem_resumo, dup_de, selecionada = TR.selecionar_itens(args, colunas, linhas)
    print(f"Linhas: {sum(r is not None for r in linhas)} | selecionadas: {sum(selecionada.values())} | "
          f"duplicatas: {len(dup_de)} | sem resumo: {len(sem_resumo)} | a classificar: {len(itens)}",
          file=sys.stderr)

    respostas = {it["id"]: classificar(it["titulo"], it["resumo"], args.perfil) for it in itens}
    extras, cont = TR.montar_extras(linhas, selecionada, dup_de, sem_resumo, itens, respostas)

    manter = {it["idx"] for it in itens} if args.so_avaliados else None
    TR.escrever_saida(saida, recs, sep, linhas, extras, final_nl, manter)
    TR.imprimir_resultado(cont, args)
    print(f"Saída gravada em: {saida}", file=sys.stderr)


if __name__ == "__main__":
    main()

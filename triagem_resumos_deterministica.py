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

USO
  python3 triagem_resumos_deterministica.py triagem_titulo.csv --amostra 50 --so-avaliados -o amostra.csv
  python3 triagem_resumos_deterministica.py triagem_titulo.csv -o triagem_resumo.csv
  python3 triagem_resumos_deterministica.py --titulo "..." --resumo "..."   # testa um artigo

Requer só Python 3.8+ (biblioteca padrão). É autônomo: não importa o triagem_titulos.py nem o
triagem_resumos.py (o vocabulário de sinais do título e a leitura/escrita da planilha foram copiados
para cá, nas seções 1 e 2).
"""
import argparse
import csv
import hashlib
import io
import os
import re
import sys
from collections import Counter


def R(p):
    return re.compile(p, re.I)


# =====================================================================================
# Seção 1: vocabulário de sinais do título (copiado de triagem_titulos.py)
# =====================================================================================
# =====================================================================================
# N0: título não em inglês ou registro que não é artigo (proceedings, editorial, errata...)
# =====================================================================================
NON_ARTICLE = R(r"^\s*(full issue|contents|table of contents|index|preface|foreword|front matter|back matter|"
                r"in this issue|masthead|cover|author index|subject index)\b|"
                r"\bproceedings of\b|\bproceeding of\b|\b(companion|adjunct) (publication|proceedings)\b|\bkeynote( talk)?\b|"
                r"\b(guest )?editorial\b(?! capabilit)|\berratum\b|\bcorrigendum\b|\bcorrection to\b|"
                r"\bretracted\b|\bretraction\b|^\s*[\w-]+: table of contents|"
                r"^\s*(best|top) (papers|articles) of\b|\bmessage from the\b|\bintroduction to the special issue\b|"
                r"^\s*[A-Za-z0-9&;-]+( companion| adjunct)?\s?'\s?\d\d\s*:")

EN_SW = set("the of and for in on with a an to from by via using towards toward is are based through into at as its their".split())

FOREIGN_SW = set(("de la le les des du pour et une un dans avec par sur à au aux est "
                  "el los las del para una con por y "
                  "della delle degli gli nel nella "
                  "der die das und für mit von zur zum ein eine im "
                  "dos das em com uma").split())

def non_english(t):
    words = re.findall(r"[a-zà-ÿ']+", t.lower())
    if not words:
        return False
    en = sum(w in EN_SW for w in words)
    fo = sum(w in FOREIGN_SW and w not in EN_SW for w in words)
    accents = len(re.findall(r"[àâçéèêëîïôûùüÿñãõáíóúì]", t.lower()))
    if en == 0 and (fo >= 2 or (fo >= 1 and accents >= 1) or accents >= 3):
        return True
    if fo >= 3 and fo > 2 * en:
        return True
    return False

# =====================================================================================
# SINAIS: SINAL_MULTIMODAL, SINAL_LLM, SINAL_AGENTE, SINAL_ARQUITETURAL, SINAL_SECUNDARIO
# e os vocabulários auxiliares das regras A-E e N2-N7
# =====================================================================================
MM_STRONG = R(r"multi[\s-]?modal|multi[\s-]?modality|\bmultimodality|cross[\s-]?modal|inter[\s-]?modal\b|"
              r"audio[\s-]?visual|audiovisual|visio[\s-]?linguistic|vision[\s-]?(and[\s-]?|&\s?)?language|visual[\s-]?language|"
              r"\bvla\b|\bvlas\b|\bmllms?\b|\blmms?\b|\blvlms?\b|\bvlms?\b|\bvllms?\b|\bmlms?\b|\bmm-?llms?\b|large vision|"
              r"image[\s-]?text|text[\s-]?image|video[\s-]?text|text[\s-]?video|video[\s-]?language|"
              r"audio[\s-]?text|text[\s-]?audio|speech[\s-]?text|audio[\s-]?language|speech[\s-]?language model|"
              r"text[\s-]?to[\s-]?(image|video|audio|speech|3d|motion|music|sql)|"
              r"(image|video|speech|audio)[\s-]?to[\s-]?(text|speech|video|image)|"
              r"multi[\s-]?source|heterogeneous (data|information|sources|modalit)|multi[\s-]?sensor|sensor[\s-]?fusion|"
              r"\bvqa\b|visual question|visual dialog|visual instruction|visual grounding|referring (expression|image|video|segmentation)|"
              r"image caption|video caption|captioning|visual reasoning|visual storytelling|visual commonsense|"
              r"\bclip\b|\bllava|\bblip|gpt-?4v|gpt-?4o|gemini|qwen[\d.-]*-?vl|"
              r"rgb[\s-]?[dt]\b|rgb-?thermal|lidar[\s-]?camera|camera[\s-]?(radar|lidar)|radar[\s-]?camera|"
              r"embodied|talking (head|face)|lip[\s-]?(sync|reading)|speech[\s-]?driven|audio[\s-]?driven|"
              r"omni[\s-]?modal|any[\s-]?to[\s-]?any|\bomni\b|vision[\s-]?(and[\s-]?)?(speech|audio)|"
              r"dall-?e|flamingo|stable diffusion|midjourney|\bsora\b|\bt2i\b|\bt2v\b|\bt2a\b|\bi2v\b|"
              r"infrared and visible|visible and infrared|rgb and (depth|thermal|infrared)|optical and sar|sar and optical|"
              r"hyperspectral and lidar|lidar and (camera|image)|"
              r"\b(image|images|video|videos|audio|speech|visual) and (text|textual|language|audio|speech|video|image)s?\b|"
              r"\b(text|textual|language) and (image|images|video|videos|audio|speech|visual)\b|"
              r"vision[\s-]?language[\s-]?action|sign language|visually[\s-]?rich|\bmultimedia\b|\bmeme|"
              r"(audio|video|speech|vision|visual|med|omni)[\s-]?llms?\b|co[\s-]?speech|gui grounding|\bgui agents?|"
              r"text[\s-]?based (person|image|video|pedestrian) (search|retrieval|re-?identification)|\bmodalit(y|ies)\b|"
              r"\b(bi|tri|dual|multi)[\s-]?modal\b|\bmultimodel\b|signed language|"
              r"chart (understanding|question|qa|reasoning|summari)|\bchartqa|document (understanding|question|vqa)|docvqa|"
              r"table question answering|visual (\w+ )?question answering|grounded language|"
              r"natural[\s-]language[\s-]based (\w+ )?(retrieval|search|grounding|localization|navigation)|"
              r"(language|text|instruction|prompt)[\s-](guided|driven|conditioned|based|aligned) (\w+[\s-]){0,2}(image|video|3d|scene|segmentation|"
              r"editing|generation|retrieval|navigation|grounding|manipulation|robot|detection|person|motion|shape|object)|"
              r"long video understanding|video question answering|videoqa|video qa|video grounding|temporal grounding|"
              r"moment retrieval|(image|radiology|medical|x-?ray|ct) report generation|radiology reports? generation")

FUSION = R(r"\bfusion\b|\bfusing\b|\bfused\b")

FUSION_CTX = R(r"modal|sensor|source|data fusion|information fusion|decision[\s-]?level|late fusion|early fusion|"
               r"image fusion|infrared|visible|hyperspectral|lidar|radar|camera|clinical|imag|text|audio|video|"
               r"eeg|ecg|physiolog|fus(ion|ing) of|feature[\s-]?level|fusion networks?")

MOD_GROUPS = {
    'text': R(r"\btext(ual|s)?\b|\bcaptions?\b|\bsentences?\b|\bclinical notes?\b|\blanguage\b|\bnatural language\b|\bwords?\b|\bverbal\b|\blinguistic"),
    'image': R(r"\bimag(e|es|ing)\b|\bvisual(ly)?\b|\bvision\b|\bphotos?\b|\bphotograph|\bpictures?\b|\bx-?rays?\b|\bmri\b|\bct\b|\bpet\b|"
               r"\bultrasound|\bhistopatholog|\bwsi\b|\bfundus|\bmammogra|\bdermoscop|"
               r"\binfrared|\bthermal|\bhyperspectral|\bsar\b|\bsketch|\bobject detection|\bscene|\bsegmentation|\bpixel"),
    'video': R(r"\bvideos?\b|\bmovies?\b|\bframes\b"),
    'audio': R(r"\baudio|\bspeech|\bacoustic|\bvoice|\bsound|\bspoken|\bmusic"),
    'physio': R(r"\beeg\b|\becg\b|\bemg\b|\bfnirs\b|\bfmri\b|\bmeg\b|\bphysiolog|\bwearable|\bbiosignal|\bheart rate|\bppg\b|"
                r"\bbrain (signals?|recordings?|activity)|\bneural (signals?|recordings?|activity)"),
    'shape3d': R(r"\bmesh(es)?\b|\b3d shapes?\b|\bvoxel"),
    'motion': R(r"\bgestures?\b|\bbody (motion|movement|language)|\bmotion capture|\bkinematic"),
    'gaze': R(r"\beye[\s-]?(movement|tracking)s?|\bgaze\b"),
    'sensor': R(r"\bsensors?\b|\blidar|\bradar|\bimu\b|\baccelerometer|\bpoint[\s-]?clouds?|\bskeleton|\bdepth\b|\bpose\b|\btactile|\bhaptic"),
    'tabular': R(r"\btabular|\bclinical (data|variables|features|information)|\behr\b|\belectronic health|\bstructured data"),
    'graph': R(r"\bgraphs?\b|\bmolecul"),
    'omics': R(r"\bprotein sequences?|\bsequence (information|data)|\bamino acid|\bgenomic|\bomics|\btranscriptom|\bgene expression|\bgenetic|\bproteom|\bsingle[\s-]?cell|\bdna\b|\brna\b"),
    'tsv': R(r"\btime[\s-]?series"),
}

IMG_MODS = R(r"\b(mri|ct|pet|spect|ultrasound|x-?ray|mammogra\w*|oct|fundus|histopatholog\w*|dermoscop\w*|endoscop\w*|"
             r"radiograph\w*|sonograph\w*|optical|sar|infrared|thermal|hyperspectral|multispectral|lidar|rgb|whole[\s-]?slide|radiolog\w*)\b")

PHYS_MODS = R(r"\b(eeg|ecg|ekg|emg|eda|gsr|ppg|eog|fnirs|respiration|respiratory signals?|skin temperature|blood pressure|accelerometer|heart rate)\b")

LLM_RX = R(r"llms?\b|\bprompt(ing|[\s-]based)\b|\bai[\s-]generated|generative models?\b|generative (recommend|retrieval|search|pre-?train)|large language|language models?|foundation(al)? models?|gpt|generative ai|\bgen ?ai\b|"
           r"generative artificial|\baigc\b|chatbot|\bchat\b|chat-?based|copilot|co-pilot|\brag\b|retrieval[\s-]?augmented|"
           r"in[\s-]?context learning|\bllama|\bmistral|\bclaude\b|deepseek|\bqwen|\bslms?\b|small language model|"
           r"pre[\s-]?trained language|\bplms?\b|instruction[\s-]?(tun|follow)|chain[\s-]?of[\s-]?thoughts?|"
           r"\bbert\b|\bt5\b|\bbart\b|\bgemini|large models?\b|large ai models?|frontier models?|reasoning models?|"
           r"artificial general intelligence|\bagi\b|generalist (robot|agent|model|polic)|prompt engineering|"
           r"\bllm-|-llm\b|lm-based|conversational ai|ai assistant|virtual assistant")

AGENT_NONCOMP = R(r"(contrast|imaging|therapeutic|theranostic|antimicrobial|antibacterial|anticancer|antitumou?r|"
                  r"chemotherapeutic|pharmacological|chelating|reducing|oxidizing|infectious|causative|biological|"
                  r"nerve|sensitizing|diagnostic|dual[\s-]?modal|bimodal|trimodal|multimodal|multifunctional|"
                  r"photothermal|photodynamic|mri|ct|fluorescent|probe|delivery|antiviral|cross-linking|"
                  r"pharmaceutical|chemical|healing|blocking|neuromuscular|anesthetic|anaesthetic|antifungal|"
                  r"hypoglycemic|antidiabetic|biologic|targeting|capping|stabilizing|foaming|nucleating|"
                  r"coupling|curing|travel|real estate|insurance) agents?\b")

AGENT_CLASSIC = R(r"multi[\s-]?agent (deep )?reinforcement|\bmarl\b|agent[\s-]?based (model|simulation|modell?ing|approach|system|computational)|"
                  r"reinforcement learning agents?|\bmulti[\s-]?agent (systems?|path|pursuit|consensus|formation|coordination|control|"
                  r"cooperative|navigation|planning|collision)")

ENG = R(r"software quality|maintainab|scalab|\bdeploy|\bserving\b|\bmlops|\bllmops|software engineering|requirements|"
        r"software testing|system testing|\btesting (of|for) (llm|ai|multimodal|agent)|(software|requirements|systems|ai|ml|llm|platform) engineering")

SEC_PRE_REMOVE = R(r"(for|in|of|automat\w*|screening|support\w*|conduct\w*|writ\w*|assist\w*|accelerat\w*)\s+(the\s+)?(systematic|literature|scoping)\s+reviews?(\s+screening)?|"
                   r"(systematic|literature) review (screening|automation)|"
                   r"(app|user|online|customer|product|code|peer|hotel|movie|restaurant|consumer|e-commerce|game) reviews?|"
                   r"reviews? (feature|mining|classification|analysis|helpfulness|generation|response|comments?|ratings?|texts?|summar\w*|"
                   r"sentiment|spam|recommendation|-based)|"
                   r"(representation|loss|energy|fitness|optimi[sz]ation|error|reward|feature|attention|activation|risk|"
                   r"performance|sound|acoustic|urban|natural|visual|hydrological|mountain|semantic|value|typical) landscapes?|landscape (types?|architecture|design|ecology|planning|images?|photograph\w*|painting)|"
                   r"taxonomy[\s-](based|guided|aware|driven|informed|enhanced|structured)|"
                   r"taxonomy (classification|expansion|induction|completion|construction|learning|generation|enrichment|alignment|"
                   r"extraction|matching|creation|mapping|tree|nodes?)|"
                   r"state[\s-]of[\s-]the[\s-]art (performance|results?|accuracy|evaluator|models?|methods?|approach|"
                   r"detection|segmentation|classification|on)|"
                   r"(multi|cross|bird'?s[\s-]?eye|first|third|single|egocentric|view|camera|user|patient|clinician)[\s-]?perspectives?|"
                   r"perspective[\s-](aware|invariant|guided|transform\w*|view|taking|correction|projection)")

SEC = R(r"\bsurvey|\breview\b|\breviewing\b|\breviews\b|systematic (literature )?(review|mapping)|mapping study|scoping review|"
        r"meta[\s-]?analy|bibliometric|\btaxonom(y|ies)\b|\boverview\b|\bsok\b|systematization of knowledge|"
        r"\broad[\s-]?map|\btutorial\b|challenges and (future )?(directions|opportunities|prospects|perspectives|solutions)|"
        r"opportunities and challenges|future directions|research directions|open (problems|challenges|questions)|"
        r"state[\s-]?of[\s-]?the[\s-]?art|recent (advances|advancements|progress|developments|trends)|\badvances in\b|"
        r"\badvancements in\b|\btrends\b|\bperspectives\b|^\s*perspective\b|: (a )?perspective\s*$|perspective (paper|article)|"
        r"\blandscape\b|\bprimer\b|\bpanorama\b|position paper|\blessons learned\b|\boutlook\b|\bprospects\b|"
        r"\bencyclopedi|\bcompendium\b|literature (review|survey|analysis|study)|review of (the )?literature|\bthe road (to|ahead)\b|\bpast,? present,? and future\b|\bstate of\b.{0,30}\b(research|field|art)\b")

COGNITIVE = R(r"cognitiv|reasoning|paradigm|neuro[\s-]?symbolic|world models?|artificial general intelligence|\bagi\b|"
              r"generalist|embodied|planning")

RETR_STRONG = R(r"retriev|\brag\b|\w+rag\b|\brag\w*|-rag|rag-|\bindexing\b|vector (database|store|search)|knowledge base|"
                r"search engine|\bre-?rank|(multimodal|multi-modal|cross-modal|image|video|visual) search")

# =====================================================================================
# N1: 'multimodal' em sentido não computacional (transporte, otimização, distribuições,
# terapia/analgesia, imagem médica sem IA, educação/linguística/semiótica...)
# =====================================================================================
N1_ALWAYS = R(r"(multi[\s-]?modal|inter[\s-]?modal|synchro[\s-]?modal|co[\s-]?modal)[\s-]+(public |urban |freight |passenger )?"
              r"(transport\w*|freight|logistic\w*|mobility|travel\w*|trips?|routes?|routing|journey\w*|"
              r"transit|ports?|terminals?|corridors?|shipping|supply chains?|hubs?|passengers?|commut\w*|"
              r"(\w+ )?(transport|transit|transportation|traffic|urban|road|distribution|supply|hub|logistics?) networks?|"
              r"networks? (design|equilibrium|flow)|distribution (network|system)s?|container|cargo|carriers?|vehicles? routing)|"
              r"\bintermodal\b|\bsynchromodal|"
              r"multi[\s-]?modal (multi[\s-]?objective )?(optimi[sz]ation|functions?|problems?|landscapes?|niching|search spaces?|"
              r"evolutionary|genetic|particle swarm|differential evolution|benchmark functions?)\b|"
              r"\bniching\b|multimodal multi[\s-]?objective (optimi|evolution|problem|algorithm)|"
              r"multi[\s-]?modal (distributions?|posteriors?|densit(y|ies)|likelihoods?|mixtures?(?![\s-]+of[\s-]+(pre-?trained )?experts)|probability)\b|"
              r"(distribution|posterior|density|densities)\w* (is|are|with) multimodal|multimodality of (the )?(distribution|posterior)")

# =====================================================================================
# NOME_PRÓPRIO e vocabulário de N2 (benchmark), N3 (avaliação), N4 (micro-técnica),
# N5 (segurança), N7 (uso aplicado) e E (modelo/rede proposto)
# =====================================================================================
GENERIC_ACR = set("""LLM LLMS MLLM MLLMS VLM VLMS LMM LMMS LVLM LVLMS VLLM VLLMS MLM VQA AI GPT CLIP BERT RAG NLP CNN CNNS RNN LSTM GAN GANS VAE
MRI CT PET EEG ECG EMG IOT UAV UAVS RGB AR VR XR KG QA OCR ASR TTS VIT SAM GNN GNNS MOE LORA RL RLHF DPO COVID SARS ADHD ASD AD MCI HCI ML DL NER
SQL API GUI LIDAR SAR ICU EHR EHRS AIGC AGI NLU NLG IR ITS USA UK EU TV CAD SNP PDF HTML XML JSON SOTA FMS FM VLA VLAS GPU GPUS CPU TPU FPGA LLAMA
LLAVA BLIP DALL SORA GPT4 GPT4V GPT4O CHATGPT AIOT IIOT CAV CAVS ADAS HRI MLOPS LLMOPS BCI FMRI MEG FNIRS PPG HAR SER MSA MER NLI KGQA
OOD 3D 2D 4D 5G 6G IEEE ACM""".split())

def _strip_generic(s):
    return re.sub(r"\b[A-Za-z0-9]+\b", lambda m: '' if m.group(0).upper() in GENERIC_ACR else m.group(0), s)

def proper_name(t):
    m = re.match(r"^\s*([^:]{1,45}?)\s*(:|\s[-–—]{1,2}\s)", t)
    if m:
        pre = m.group(1).strip()
        if not re.match(r"(?i)^(abstract|poster|demo|position|correction|review|survey|editorial|preface|"
                        r"retracted|chapter|part|case|keynote|tutorial|research|study|letter|commentary|book|note)\b", pre):
            words = pre.split()
            if len(words) <= 3 and not re.search(r"[,?!]", pre):
                core = _strip_generic(pre).strip(" -")
                if core and (any(re.search(r"[A-Z].*[A-Z]|\d|[a-z][A-Z]|-", w) for w in core.split()) or len(words) == 1):
                    return True
    s2 = _strip_generic(t)
    if re.search(r"\b[A-Za-z0-9]*[A-Z][A-Za-z0-9]*-?(Net|Former|GPT|VLM|LLM|RAG|LM|Agent|CLIP|Diff|Fusion|Gen|Seg|VL|LLaVA|Med|Omni)\b", t) \
            and re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]|\w-\w", s2):
        return True
    if re.search(r"\b[A-Z][a-z]+[A-Z][A-Za-z]*(-?\d|-[A-Z])", s2):
        return True
    return False

BENCH_HEAD = R(r"benchmark|\bdataset|\bdata set|\bcorpus|\bcorpora\b|\bleaderboard|\bchallenge\b|\barena\b|-bench\b|bench\b|"
               r"test suite|evaluation suite|shared task")

BENCH_WITH_SYS = R(r"(dataset|benchmark|corpus)s? and (a |an )?([\w-]+ ){0,2}(models?|methods?|frameworks?|baselines?|approach(es)?|systems?|networks?|agents?)\b|"
                   r"(model|method|framework|approach|system|network)s? and (a |an )?([\w-]+ ){0,2}(dataset|benchmark|corpus)|"
                   r"\bwe propose\b|\bnovel (method|model|framework|approach)|(dataset|benchmark|corpus)s?, (\w+ ){0,2}(models?|methods?|frameworks?)\b|"
                   r"model development|\badapting\b|^\s*(advancing|enhancing|improving|boosting|empowering|scaling)\b")

N4_CLEAR = R(r"\bprun|token[\s-]?(compression|reduction|merging|pruning|dropping|eviction|skipping|sparsif)|visual tokens? (compression|reduction|pruning|merging|dropping)|"
             r"quantiz|binari[sz]|kv[\s-]?cache|speculative|loss functions?|\bmodel merging|"
             r"early[\s-]?exit|sparsif|model compression|compress(ing|ion of)? (the )?(large|multimodal|vision|llm|mllm|vlm|lvlm|kv|tokens?|visual tokens?|context|prompts?)|"
             r"\bdpo\b|\brlhf\b|\brlaif\b|preference optimi|\blora\b|low[\s-]?rank adapt|parameter[\s-]?efficient|\bpeft\b|"
             r"prompt[\s-]?(tuning|learning)|soft prompts?|\bunlearning\b|reward model|policy optimi|\bgrpo\b|\bppo\b|"
             r"self[\s-]?rewarding|steering vectors?|activation steering|model editing|knowledge editing|tokeniz|"
             r"positional (encoding|embedding)|\brope\b|contrastive decoding|decoding (strateg|method|algorithm)|"
             r"inference[\s-]?(acceleration|speed[\s-]?up|efficien)|accelerat(e|ing) (inference|decoding)")

N5 = R(r"jailbreak|adversarial (attack|example|perturbation|robustness|vulnerab|prompt|image|patch|input|sample)|backdoor|trojan|poison|"
       r"\battacks?\b|red[\s-]?team|hallucinat|membership inference|watermark|\brobustness\b|"
       r"safety (alignment|evaluation|risks?|benchmark|guard|of)|(model|ai|llm|content) safety|\bunsafe\b|harmful|toxic|"
       r"privacy (leak|attack|risk)|security (risks?|vulnerab|threat|of)|vulnerabilit|prompt injection|misuse|guardrail|"
       r"\bsteal|extraction attack|\bbias(es|ed)?\b|stereotyp|fairness|debias|deepfake detection|"
       r"(ai|machine|llm)[\s-]?generated (text|content|image) detection|detecting (ai|machine|llm)[\s-]?generated")

N3_Q = R(r"^\s*(can|are|do|does|is|what|why|when|which|should|will|would|could|did|has|have|where|who|whither)\b|"
         r"^\s*how (well|far|good|much|many|do|does|did|can|are|is|reliable|accurate|robust|close|often)\b|"
         r"^[^:]{0,80}\?\s*$|^[^:?]{3,80}\?\s+[A-Z]|:\s*(can|are|do|does|is|how (well|far|good|much)|what|why|which|should|will)\b[^?]*\?\s*$")

N3 = R(r"^\s*(a |an )?(systematic |comprehensive |empirical |comparative |critical |quantitative |preliminary |large[\s-]scale |thorough )?"
       r"(evaluat\w*|assess\w*|benchmarking|comparing|comparison|analy[sz]\w*|investigat\w*|examin\w*|probing|measuring|quantifying|"
       r"characteri[sz]\w*|auditing|testing|validat\w*)\b|"
       r"\b(empirical|comparative|exploratory|evaluation|pilot|observational|validation|feasibility) (study|analysis|evaluation|investigation|comparison)\b|"
       r"\bperformance (analysis|evaluation|comparison|assessment)\b|^\s*(the )?performance of\b|\baccuracy of\b|\blimitations of\b|"
       r"\bbehaviou?r(al)? (of|analysis)\b|^\s*(revisiting|rethinking evaluation|towards understanding|understanding)\b|"
       r"a study (of|on)\b|a closer look|what (do|does|makes|matters)\b|\bvs\.?\s|\bversus\b|"
       r"^\s*(exploring|examining|unveiling|uncovering|demystifying|dissecting|diagnosing|interpreting|explaining|probing)\b.{0,60}"
       r"\b(capabilit|potential|ability|abilities|behaviou?r|limits|limitation|role|use of|effect|impact|robustness|reliability|"
       r"performance|bias|knowledge|competenc|mechanism|internal|representation|failure|weakness|how|whether|what)|"
       r"\breliability of\b|\bvalidity of\b|\bagreement between\b|\bconcordance\b|\bperformance on\b|\boutperforms?\b|"
       r"\bmechanistic interpret|\bscaling laws?\b|\bemergent abilit|\bare (not )?(few|zero)[\s-]?shot|\blessons from\b|"
       r"\bpitfalls\b|\breality check\b|\bsanity check\b|\bstress[\s-]?test|\bblind spots?\b|\bbottleneck for\b|"
       r"\b(gpt-?4v?|gpt-?4o|chatgpt|gemini|claude|llama[\s-]?\d*|gpt-?3\.?5?)\b.{0,40}\b(vs|versus|compared|comparison|performance|accuracy|answer|exam|test|board)")

N3_PROPOSE = R(r"\b(evaluat|assess|understand|investigat|analy[sz]|measur|probing|diagnos|benchmark|examin|reveal|identify)\w* and "
               r"(enhanc|improv|mitigat|boost|construct|build|address|overcom|alleviat|reduc|leverag|exploit|harness|advanc|bridg|"
               r"fix|repair|tackl|correct|remed|refin|strengthen|elicit|unlock)|\band (a |an )?(new|novel) (method|model|framework|approach)|"
               r"\bwe propose\b|\btowards? (better|improved|robust)|development and (validation|evaluation)|design and (evaluation|validation|implementation)|"
               r"model development|algorithm development|\bdesign,? (development|implementation)")

def mm_signal(t):
    if MM_STRONG.search(t):
        return 'strong'
    groups = [g for g, rx in MOD_GROUPS.items() if rx.search(t)]
    if FUSION.search(t) and (FUSION_CTX.search(t) or len(groups) >= 1):
        return 'strong'
    if len(groups) >= 2:
        return 'groups'
    imgs = {m.group(1).lower() for m in IMG_MODS.finditer(t)}
    if len(imgs) >= 2:
        return 'groups'
    phys = {m.group(1).lower() for m in PHYS_MODS.finditer(t)}
    if len(phys) >= 2:
        return 'groups'
    return None


# =====================================================================================
# Seção 2: leitura da planilha, seleção dos artigos e gravação do CSV (copiado de triagem_resumos.py)
# =====================================================================================
MIN_RESUMO = 50  # caracteres; abaixo disso o resumo é considerado ausente

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
    if not os.path.isfile(caminho):
        sys.exit(f"Planilha não encontrada: '{caminho}'\nPasta atual do terminal: {os.getcwd()}\n"
                 f"Confira o nome do arquivo ou rode o comando na pasta do projeto (ou use o caminho completo).")
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
# Cache de respostas (JSONL)
# =====================================================================================
def hash_titulo(t):
    return hashlib.sha1(norm_titulo(t).encode()).hexdigest()[:12]

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
def adicionar_args_planilha(ap):
    """Argumentos de entrada/seleção/saída, comuns às versões com Claude e determinística."""
    ap.add_argument("entrada", help="planilha CSV (ex.: saída de triagem_titulos.py)")
    ap.add_argument("-o", "--saida", help="CSV de saída (padrão: <entrada>_resumo.csv)")
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

def selecionar_itens(args, colunas, linhas):
    """Escolhe os artigos a avaliar. Trata duplicatas (EC3) e artigos sem resumo, sem chamar nenhum modelo.
    Devolve (itens, sem_resumo, dup_de, selecionada)."""
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
    return itens, sem_resumo, dup_de, selecionada

def montar_extras(linhas, selecionada, dup_de, sem_resumo, itens, cache):
    """Monta as colunas novas de cada linha. `cache` mapeia id -> {classification, code, ic, ec, justificativa}."""
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
    return extras, cont

def imprimir_resultado(cont, args):
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
# Perfil "rigoroso": termos de estudo secundário só no título (sem "perspective", "challenges", "advances"...)
SEC_TITLE_FORTE = R(r"\bsurvey|\breview\b|\btaxonom|\boverview\b|\bsok\b|systematization|road[\s-]?map|"
                    r"mapping study|literature|\btutorial\b|\bprimer\b")
# Recuperação aumentada de verdade (RAG), e não "image retrieval"/"cross-modal retrieval"/hashing
RAG_PROPER = R(r"\brag\b|retrieval[\s-]augment\w*|augment\w* (generation|with retriev\w*)|retrieve[\s-]and[\s-]"
               r"(generate|read)|retriev\w* (and|then|before) (generat\w*|answer\w*|reason\w*)|"
               r"(knowledge|memory|evidence|document|context) retriev\w*|retriev\w*[\s-](based|enhanced|grounded)")
RETRIEVAL_TASK = R(r"\b(image|video|text|cross[\s-]modal|multi-?modal|sketch|person|moment|audio|music|visual|"
                   r"remote[\s-]sensing|medical report|hashing)[\s-]*(-to-)?(text|image|video)?[\s-]*(retrieval|search|hashing)\b|"
                   r"\bhashing\b")
# "framework" que na verdade é um procedimento de treino/avaliação (EC1), não arquitetura
TRICK_FW = R(r"\b(contrastive|distillation|pre-?training|fine-?tuning|training|learning|alignment|adaptation|"
             r"augmentation|optimi[sz]ation|evaluation|benchmarking|unlearning|regulari[sz]ation|self-?supervised|"
             r"curriculum|meta-?learning|reinforcement learning|rl|attack|defen[cs]e)\s+(\w+\s)?framework\b")
# Agentes em sentido de LLM (não otimização/RL clássico)
AGENT_LLM_TITLE_BAD = R(r"\b(path planning|algorithm optimi[sz]ation|optimization|prediction|reinforcement|q-learning|"
                        r"scheduling|resource allocation|meta-?learning|distillation|sample efficient)\b")

RIG_IMPL_MIN_CE = 4    # perfil rigoroso, C/E: mínimo de sinais de validação (experimento, benchmark, resultados...)
RIG_IMPL_MIN_D = 2     # perfil rigoroso, D: idem
RIG_RAG_MIN_D = 4      # perfil rigoroso, D: mínimo de menções a RAG/recuperação no resumo
RIG_DESIGN_MIN_D = 2   # perfil rigoroso, D: mínimo de componentes distintos descritos
RIG_AGENT_MIN_B = 3    # perfil rigoroso, B: mínimo de menções a agente no resumo
RIG_ESTRUTURA_MIN_B = 3  # perfil rigoroso, B: pontuação mínima de estrutura (componentes + organização + arquitetura)
RIG_DESIGN_MIN_B = 2   # perfil rigoroso, B: mínimo de componentes distintos descritos (ou padrão de projeto no título)
RIG_TRICK_MAX = 3      # perfil rigoroso: máximo de termos de treino/técnica (perda, distilação, atenção...) no resumo
RIG_ESTRUTURA_MIN = 4  # perfil rigoroso: pontuação mínima de estrutura (componentes + organização + arquitetura)

# Declaração explícita: "we propose/introduce ... a (novel) ... framework/system/architecture ..."
DECL_FW = R(r"\b(propose|present|introduce|develop|design|build|construct|devise|establish|put forward|implement|"
            r"create|offer|describe|report)[sd]?\b(?P<meio>[^.;]{0,50}?)\b(a|an|the|our)\s+(?P<mod>(?:[\w-]+\s+){0,5}?)"
            r"(framework|system|architecture|pipeline|platform|toolkit|workflow|infrastructure|middleware|toolbox|"
            r"ecosystem)\b")
DECL_BAD_MOD = R(r"\b(network|net|model|method|approach|algorithm|loss|attention|classifier|detector|module|"
                 r"mechanism|layer|training|learning|distillation|alignment)\b")

# Termos que mostram que a revisão olha para arquitetura/organização (e não só para resultados)
ARCH_TERM = R(r"architect|framework|taxonom|paradigm|pattern|design (principle|choice|space)|reference model|"
              r"component|modular|pipeline|workflow")

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

    f["n0"] = bool(NON_ARTICLE.search(t)) or non_english(t)
    f["llm"] = bool(LLM_RX.search(ta))
    f["mm"] = mm_signal(ta)
    sem_noncomp = AGENT_NONCOMP.sub(" ", ta)
    f["agent_t"] = bool(AGENT_WORD.search(AGENT_NONCOMP.sub(" ", t)))
    f["agent_n"] = len(re.findall(r"\bagent(s|ic|ics)?\b|\bmulti-?agent|\bagentic", sem_noncomp, re.I))
    f["agent_ctx"] = bool(LLM_AGENT_CTX.search(ta))
    f["agent_classic"] = bool(AGENT_CLASSIC.search(ta)) and not f["llm"]
    f["n1"] = bool(N1_ALWAYS.search(ta)) and not (
        mm_signal(N1_ALWAYS.sub(" ", ta)) == "strong" or LLM_RX.search(N1_ALWAYS.sub(" ", ta)))

    f["sec_t"] = bool(SEC_TITLE_STRONG.search(SEC_PRE_REMOVE.sub(" ", t))) or bool(SEC.search(SEC_PRE_REMOVE.sub(" ", t)))
    f["sec_a"] = bool(SEC_ABS.search(a))
    f["sec_ev"] = _ev(SEC_TITLE_STRONG, t) or _ev(SEC_ABS, a)
    f["cognitive"] = bool(COGNITIVE.search(ta))
    f["conceptual"] = bool(CONCEPTUAL.search(a)) and len(IMPL.findall(a)) <= 1

    f["bench_t"] = bool(BENCH_HEAD.search(t)) and not BENCH_WITH_SYS.search(t)
    f["bench_a"] = bool(BENCH_ABS.search(a))
    f["eval_a"] = bool(EVAL_ONLY.search(a)) and not EVAL_PROPOSES.search(a)
    f["eval_t"] = bool(N3.search(t) or N3_Q.search(t)) and not N3_PROPOSE.search(t)
    f["sec_risk_t"] = bool(N5.search(t))
    f["sec_risk_a"] = len(SECURITY_ABS.findall(a))
    f["narrow_t"] = bool(N4_CLEAR.search(t))
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
    f["named"] = proper_name(t)
    f["impl_n"] = len(IMPL.findall(a))

    f["rag_t"] = bool(RETR_STRONG.search(t))
    f["rag_n"] = len(RAG_STRONG.findall(a))
    f["nontext"] = bool(NONTEXT_ABS.search(ta)) or bool(f["mm"])
    f["eng"] = bool(ENG.search(ta))
    f["arch_a"] = len(re.findall(r"\barchitectur\w*|\bplatform\b|\btoolkit\b|\binfrastructure\b|\bmiddleware\b|"
                                 r"\bpipeline\b|\bworkflow\b|\bmodular\b|\bend-to-end system\b|\bsoftware\b|"
                                 r"\bdeploy\w*", a, re.I))
    # --- sinais extras do perfil "rigoroso"
    t_sem = SEC_PRE_REMOVE.sub(" ", t)
    f["sec_t_forte"] = bool(SEC_TITLE_FORTE.search(t_sem))
    f["anchor_t"] = (mm_signal(t) == "strong") or bool(LLM_RX.search(t)) or f["agent_t"] or \
        bool(COGNITIVE.search(t))
    f["rag_proper"] = bool(RAG_PROPER.search(ta))
    f["rag_task"] = bool(RETRIEVAL_TASK.search(ta)) and not RAG_PROPER.search(ta)
    f["arch_term_a"] = bool(ARCH_TERM.search(a))
    f["fw_decl"] = False
    for m in DECL_FW.finditer(a):
        if not DECL_BAD_MOD.search(m.group("mod")):
            f["fw_decl"] = True
            break
    f["trick_fw"] = bool(TRICK_FW.search(a)) or bool(TRICK_FW.search(t))
    f["agent_bad_t"] = bool(AGENT_LLM_TITLE_BAD.search(t))
    f["pattern_t"] = bool(re.search(r"design patterns?|architectur|reference architecture|taxonomy|framework", t, re.I))
    f["fusion_n"] = len(re.findall(r"\bfusion|\bfuse|\bfusing|\bintegrat\w*|\bcombin\w*", a, re.I))
    f["estrutura_score"] = f["design_n"] + f["org_n"] + min(f["arch_a"], 3)
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


def decidir(f, perfil="rigoroso"):
    estrito = perfil == "precisao"
    rig = perfil == "rigoroso"  # exclusões como em "revocacao"; `yes` só com evidência forte
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
    # No perfil rigoroso o estudo secundário precisa de termo forte de revisão NO TÍTULO, o resumo também se declarar
    # revisão, termos de arquitetura/organização, multimodal forte, algum sinal de método/avaliação e tema âncora no título (multimodal/LLM/agentes/paradigma cognitivo), sem ser revisão
    # de domínio de aplicação nem de avaliação.
    sec_ok = (f["sec_t"] or f["sec_a"]) if not rig else (
        f["sec_t_forte"] and f["sec_a"] and f["arch_term_a"] and f["arch_a"] >= 1 and f["mm"] == "strong" and
        f["impl_n"] >= 1 and f["anchor_t"] and not f["app"] and not f["eval_t"])
    if sec_ok:
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
    d_base = (f["rag_t"] or f["rag_n"] >= 2) and f["nontext"] and (f["fw_t"] or f["fw_a"] or f["design_n"] >= 1 or f["rag_t"])
    if rig:  # RAG de verdade (não "image retrieval"/hashing), com LLM/geração e estrutura descrita
        d_base = d_base and f["rag_proper"] and not f["rag_task"] and (f["llm"] or f["agent_n"] >= 1) and \
            (f["fw_a"] or f["design_n"] >= 2 or f["rag_t"]) and f["rag_n"] >= RIG_RAG_MIN_D and \
            f["impl_n"] >= RIG_IMPL_MIN_D and f["design_n"] >= RIG_DESIGN_MIN_D
    if d_base:
        return _r("yes", "D", _ic(f, ["IC1", "IC8", "IC9"]), [],
                  f"Recuperação aumentada com entrada não textual e saída em texto ({_fmt(_ev(RAG_STRONG, a or t))}).")

    # ---- B: sistemas baseados em agentes
    if (f["agent_t"] or f["agent_n"] >= 2) and f["agent_ctx"] and not f["agent_classic"] and not (
            rig and (not f["llm"] or f["agent_bad_t"] or f["trick_fw"])):
        b_ok = f["design_n"] >= 1 or f["fw_a"] or f["fw_t"] or f["agent_t"]
        if rig:  # sistema de agentes: declarado como framework/sistema/padrão E com componentes descritos
            b_ok = (f["design_n"] >= RIG_DESIGN_MIN_B or (f["pattern_t"] and (f["fw_a"] or f["arch_a"] >= 2))) and \
                (f["mm"] == "strong" or (f["pattern_t"] and f["arch_a"] >= 2)) and \
                f["agent_n"] >= RIG_AGENT_MIN_B and f["estrutura_score"] >= RIG_ESTRUTURA_MIN_B and \
                f["agent_t"] and f["arch_term_a"]
        if b_ok:
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
    if rig:
        # Exceção restrita a EC1: (a) os autores declaram framework/sistema/arquitetura (verbo + framework no
        # resumo, que não seja procedimento de treino/avaliação) e (b) a organização de ponta a ponta aparece
        # (vários componentes/ramos/etapas, fusão e saída).
        ce_ok = f["fw_decl"] and not f["trick_fw"] and f["estrutura_score"] >= RIG_ESTRUTURA_MIN and \
            f["design_n"] >= 1 and (f["fusion_n"] >= 1 or f["agent_n"] >= 1 or f["eng"] or f["arch_a"] >= 2) and \
            f["trick_n"] <= RIG_TRICK_MAX and not (f["trick_t"] and not f["fw_t"]) and \
            not f["model_only"] and not f["bench_t"] and not f["bench_a"] and f["mm"] == "strong" and \
            f["impl_n"] >= RIG_IMPL_MIN_CE and f["arch_term_a"]
    else:
        ce_ok = estrutura and (f["design_n"] >= 2 or f["arch_a"] >= 1 or f["org_n"] >= 2) and not (
            f["trick_t"] and f["model_only"] and not f["fw_a"])
    if ce_ok:
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


def classificar(titulo, resumo, perfil="rigoroso"):
    """Classifica um artigo. Devolve dict com classification, code, ic, ec e justificativa.
    perfil: "rigoroso" (padrão; `yes` só com evidência forte), "revocacao" (`yes` amplo, `no` só com
    evidência forte) ou "precisao" (todas as exclusões)."""
    return decidir(extrair(titulo, resumo), perfil)


# =====================================================================================
# Principal
# =====================================================================================
def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--titulo")
    pre.add_argument("--resumo", default="")
    pre.add_argument("--perfil", choices=["revocacao", "precisao", "rigoroso"], default="rigoroso")
    p_args, resto = pre.parse_known_args()
    if p_args.titulo is not None:  # teste de um único artigo
        r = classificar(p_args.titulo, p_args.resumo, p_args.perfil)
        print(f"{r['classification']};{r['code']};{';'.join(r['ic'])};{';'.join(r['ec'])}\n{r['justificativa']}")
        return

    ap = argparse.ArgumentParser(
        description="Triagem por título + resumo só com regras (sem Claude). Para testar um artigo avulso: "
                    "--titulo \"...\" --resumo \"...\"")
    adicionar_args_planilha(ap)
    ap.add_argument("--perfil", choices=["revocacao", "precisao", "rigoroso"], default="rigoroso",
                    help="rigoroso (padrão): `yes` só com evidência forte (o que não passa vira `maybe`); "
                         "revocacao: `yes` mais amplo, `no` só com evidência forte; "
                         "precisao: aplica todas as regras de exclusão (mais `no`, perde mais artigos relevantes)")
    args = ap.parse_args(resto)
    args.perfil = p_args.perfil  # o parser prévio já consumiu --perfil

    saida = args.saida or os.path.splitext(args.entrada)[0] + "_resumo.csv"
    recs, sep, colunas, linhas, final_nl = ler_planilha(args.entrada)
    itens, sem_resumo, dup_de, selecionada = selecionar_itens(args, colunas, linhas)
    print(f"Linhas: {sum(r is not None for r in linhas)} | selecionadas: {sum(selecionada.values())} | "
          f"duplicatas: {len(dup_de)} | sem resumo: {len(sem_resumo)} | a classificar: {len(itens)}",
          file=sys.stderr)

    respostas = {it["id"]: classificar(it["titulo"], it["resumo"], args.perfil) for it in itens}
    extras, cont = montar_extras(linhas, selecionada, dup_de, sem_resumo, itens, respostas)

    manter = {it["idx"] for it in itens} if args.so_avaliados else None
    escrever_saida(saida, recs, sep, linhas, extras, final_nl, manter)
    imprimir_resultado(cont, args)
    print(f"Saída gravada em: {saida}", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
triagem_titulos.py: triagem por TÍTULO da RSL sobre arquiteturas, frameworks,
padrões e taxonomias para sistemas multimodais e agênticos baseados em IA.

Implementa em regras determinísticas (expressões regulares) o prompt de triagem
"PAPEL / ETAPA E LIMITAÇÃO / ... / SAÍDA", na mesma ordem de decisão:

    1. N0 (não inglês / não é artigo) e N1 ("multimodal" em sentido não computacional)
    2. Inclusão, na ordem A, B, C, D, E (o primeiro arquétipo satisfeito define o código)
         A  estudo secundário sobre o ecossistema (survey, review, taxonomy...)
         B  sistemas baseados em agentes
         C  vocabulário arquitetural + sinal multimodal/LLM (inclui IC1 e IC2)
         D  recuperação (RAG, retrieval, memória) com entrada não textual
         E  modelo/rede multimodal proposto e nomeado; foundation model/MLLM de domínio
    3. Exclusões após a inclusão: N2 (só benchmark/dataset), N3 (avaliação de
       modelos existentes), N4 (micro-técnica), N5 (segurança/robustez), N7 (uso aplicado)
    4. maybe (M): há sinal multimodal/LLM/agêntico sem regra decisiva
    5. N6: nenhum sinal, ou tarefa unimodal

Só a coluna "title" é usada para decidir; as demais colunas são ignoradas.

ENTRADA (os dois formatos do prompt)
  (b) planilha CSV completa (ex.: export do Rayyan com colunas key,title,year,...).
      SAÍDA: o mesmo arquivo, byte a byte (mesmas colunas, ordem, linhas, aspas e
      separador), com duas colunas novas no fim: classification, code.
  (a) arquivo texto com linhas "key | title".
      SAÍDA: linhas "key;classification;code", sem cabeçalho, na mesma ordem.

USO
  python3 triagem_titulos.py artigos.csv                       # gera artigos_triagem.csv
  python3 triagem_titulos.py artigos.csv -o saida.csv
  python3 triagem_titulos.py lista.txt --formato linhas -o saida.txt
  python3 triagem_titulos.py --titulo "Agent S: An Open Agentic Framework ..."   # testa um título

Requer apenas Python 3.8+ (biblioteca padrão).

LIMITAÇÃO: as regras aproximam o julgamento "pelo significado" pedido no prompt.
Elas foram calibradas por amostragem no corpus de ~16 mil títulos e tendem a errar
para o lado da revocação (mais yes/maybe do que no). Revise sobretudo N6 e N3.
"""
import argparse
import csv
import io
import os
import re
import sys
from collections import Counter


def R(p):
    return re.compile(p, re.I)

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
AGENT_RX = R(r"\bagents?\b|\bagentic|\bmulti[\s-]?agents?\b|agent-|-agent|agent\b|copilot|co-pilot|computer[\s-]?use|tool[\s-]?(use|using|learning|calling|augmented)|"
             r"function[\s-]?calling|orchestrat|cognitive architecture|model context protocol|\bmcp\b|autonomous (ai|assistant)")
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
ARCH = R(r"architectur|reference model|design patterns?|blueprint|framework|platform|pipeline|toolkit|toolbox|tool[\s-]?chain|"
         r"infrastructure|\bserving\b|ecosystem|middleware|workflow|\bstack\b|modular|"
         r"\bunified\b(?! (benchmark|dataset|evaluation|metric|multimodal models?\b|models?\b|vision[\s-]?language models?\b|mllms?\b|understanding and generation models?))|"
         r"end[\s-]?to[\s-]?end|\bsystems?\b|\bsoftware library\b|\bpython library\b|open[\s-]?source library|\bengine\b|"
         r"\bopen[\s-]?source (tool|software)|\bsoftware\b|\bapi\b|\bsdk\b|\bmetrics?\b|\bmeasures?\b|evaluation framework|"
         r"maintainab|scalab|\bdeploy|\bmlops|\bllmops|\baiops|(software|requirements|systems|ai|ml|llm|platform|data|knowledge|ontology) engineering|"
         r"\brequirements\b")
ARCH_WEAK = R(r"\bunified\b|\bsystems?\b|end[\s-]?to[\s-]?end|modular|\bmetrics?\b|\bmeasures?\b|\brequirements\b|\bdeploy|scalab|\bengine\b")
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
NARROW = R(r"watermark|quantiz|prun|compression|distillation|binariz|kv[\s-]?cache|jailbreak|adversarial|backdoor|poison|"
           r"membership inference|unlearning|speculative decoding|tokeniz|positional encoding|"
           r"low[\s-]?rank|\blora\b|parameter[\s-]?efficient|\bpeft\b|fine[\s-]?tun|hallucination|bias|fairness|"
           r"toxicity|privacy|security|safety|attack|robustness|(ai|machine|llm)[\s-]?generated text|"
           r"acceleration|inference efficien|efficient inference")
NARROW_SEC5 = R(r"watermark|jailbreak|adversarial|backdoor|poison|membership inference|hallucination|bias|fairness|"
                r"toxicity|privacy|security|safety|attack|robustness|generated text")
SEC_DOMAIN_WEAK = R(r"heterogeneous|multi[\s-]?source|multimodal data|multi[\s-]?modal data|data fusion|multi[\s-]?sensor")

RETR_STRONG = R(r"retriev|\brag\b|\w+rag\b|\brag\w*|-rag|rag-|\bindexing\b|vector (database|store|search)|knowledge base|"
                r"search engine|\bre-?rank|(multimodal|multi-modal|cross-modal|image|video|visual) search")
MEMORY = R(r"\bmemory\b|\bmemories\b")
MEM_HW = R(r"memory[\s-]?(efficient|footprint|bandwidth|usage|access|consumption|cost|wall|bound|saving|reduction|hierarch|"
           r"controller|-?centric computing|requirement)|in[\s-]?memory|\bon[\s-]?chip memory|memory[\s-]?constrained|"
           r"\bdram\b|\bhbm\b|flash memory|compute[\s-]?in|processing[\s-]?in|memory (t|b) cells?|working memory|"
           r"episodic memory (in|of) (humans|patients|older)|memory (impairment|decline|loss|performance|function)")
NONTEXT = R(r"video|image|visual|vision|audio|speech|document|chart|table|tabular|graph|multimodal|multi-modal|"
            r"cross-modal|cross modal|\bmaps?\b|slide|figure|screenshot|pdf|photo|picture|3d|scene|"
            r"heterogeneous|multi-source|medical imag|radiolog|patholog|music|sound|scientific (figure|diagram)|"
            r"remote sensing|satellite|meme|ocr|knowledge graph|time[\s-]?series|sensor|visually[\s-]?rich|vlm|mllm|lmm|lvlm")

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
N1_CTX = R(r"analges|anesthe|anaesthe|\bpain\b|opioid|perioperative|postoperative|post-operative|"
           r"\btherapy\b|therapies|therapeutic|treatment|surgery|surgical|rehabilitat|prehabilitat|physiotherap|"
           r"exercise|intervention|chemotherap|radiotherap|immunotherap|clinical trial|randomi[sz]ed|\btrial\b|"
           r"cohort|case report|case series|patients? with|management of|antiemetic|nausea|prophylaxis|"
           r"\bimaging\b|tomograph|echocardiograph|angiograph|elastograph|endoscop|microscop|spectroscop|"
           r"contrast agent|\bprobe\b|nanoparticle|nanoprobe|theranostic|fluorescen|photoacoustic|"
           r"discourse|semiotic|linguistic|literacy|literacies|\bcomposition\b|pedagog|classroom|students?\b|teachers?\b|"
           r"teaching|learners?\b|education|curriculum|writing|rhetoric|metaphor|gesture|interaction analysis|"
           r"conversation analysis|corpus linguistics|systemic functional|\bfilm|\bart\b|museum|narrative|"
           r"advertis|picture ?book|textbook|communication strategies|identity|culture|poetry|translation studies|"
           r"psycholog|psychotherap|counsel|nursing|diagnostic value|diagnosis of|assessment of|evaluation of|"
           r"microstructure|material|steel|alloy|polymer|catalys|(?<!autism )spectrum|spectra|vibration|waveguide|fiber|fibre|"
           r"antenna|resonat|plasmon|phonon|photonic|optical|acoustic|seismic|neutron|fission|quantum|laser")
N1_EDU = R(r"discourse|semiotic|literacy|literacies|pedagog|classroom|students?\b|teachers?\b|teaching|learners?\b|"
           r"education|curriculum|writing|rhetoric|systemic functional|picture ?book|textbook|poetry|translation studies|"
           r"\bcomposition\b|\bfilm\b|museum|\bart\b")
MM_COMP_ADJ = R(r"(multi[\s-]?modal|cross[\s-]?modal)[\s-]+(deep |machine |large |foundation |generative |neural |contrastive |"
                 r"self[\s-]?supervised |graph |hierarchical |adaptive |dynamic |efficient |robust |unified |interpretable |"
                 r"explainable |federated |interactive |conversational |knowledge[\s-]?enhanced |pre[\s-]?trained |attention[\s-]?based |"
                 r"feature |data |information |semantic |representation )?"
                 r"(learning|alignment|fusion|models?|llms?|language|data|datasets?|transformers?|representations?|features?|"
                 r"networks?|framework|systems?|ai\b|artificial|machine|sentiment|emotion|recognition|retrieval|generation|"
                 r"understanding|reasoning|classification|detection|prediction|embeddings?|pre[\s-]?training|interaction|interfaces?|"
                 r"agents?|chatbots?|knowledge graphs?|summari\w*|translation|dialog\w*|question|instruction|prompt\w*|encoders?|"
                 r"integration|analytics|sensing|sensors?|signals?|biometric|perception|hashing|clustering|segmentation|"
                 r"registration|imaging analysis|radiomics|nomogram|benchmark|corpus|search|recommendation|"
                 r"approach(es)? (for|to) (detect|predict|classif|recogni))")
COMP_EDU = R(r"(deep|machine|representation|contrastive|self[\s-]?supervised|federated|transfer|reinforcement|multi[\s-]?task|"
             r"few[\s-]?shot|zero[\s-]?shot|supervised|unsupervised|curriculum|fusion|cooperative|collaborative) learning|"
             r"\bneural|artificial intelligence|\bai\b|\bai-|algorithm|llm|language models?|transformer|\bcnns?\b|computer|"
             r"automat|generative|gpt|chatbot|vision[\s-]?language|retriev|embedding|classif|detection|recognition|"
             r"analytics|sensor|data[\s-]?driven|\bvr\b|virtual reality|augmented reality|robot|predict|forecast|fusion|"
             r"tracking|computational|dataset|benchmark|segmentation|\bvqa\b|question answering|caption|wearable|signal|"
             r"\bnlp\b|natural language processing|eye[\s-]?track|sensing")
COMP = R(r"(deep|machine|representation|contrastive|self[\s-]?supervised|federated|transfer|reinforcement|multi[\s-]?task|meta|"
         r"ensemble|metric|feature|joint|supervised|unsupervised|semi[\s-]?supervised|active|few[\s-]?shot|zero[\s-]?shot|"
         r"incremental|continual|statistical|graph|cross[\s-]?modal|multimodal|multi[\s-]?modal|collaborative|"
         r"curriculum|online|dictionary|manifold|subspace|hashing|adversarial) learning|learning[\s-](model|framework|approach|method|"
         r"algorithm|network|system|based)|"
         r"\bneural|artificial intelligence|\bai\b|\bai-|\bml\b|algorithm|llm|language models?|transformer|\bcnns?\b|\brnn|\blstm|"
         r"computer|automat|generative|gpt|vision[\s-]?language|retriev|embedding|segmentation|classification|classifier|"
         r"recognition|detection|chatbot|agent|\bnetworks?\b(?! (design|equilibrium))|fusion|radiomic|predict|forecast|"
         r"data[\s-]?driven|data mining|\bmining\b|framework|\bsystem\b|registration|reconstruction|analytics|sensing|\bsensors?\b|"
         r"\bvr\b|virtual reality|augmented reality|robot|interface|interactive|\bhci\b|dataset|benchmark|features?\b|clustering|"
         r"estimation|tracking|annotation|captur|signal|\bvqa\b|question answering|\bqa\b|reasoning|generation|generat|"
         r"understanding|editing|synthes|represent|pre[\s-]?train|contrastive|encoder|decoder|autoencoder|learner|extraction|"
         r"semantic|multimedia|videos?\b|social media|comic|sentiment|nowcast|layout|distributional|style transfer|game|"
         r"computational|digital|\bdata\b|\bloss\b|variational|tuning|coreference|corpus|\bimage\b|\bimages\b|scene|"
         r"chart|caption|speech|emotion|affect|biometric|identification|verification|authentication|wearable|smartphone|"
         r"\bapp\b|software|simulation|\bmodel(s|ing|ling)?\b|optimi[sz]ation|bayesian|regression|machine|\bdl\b|platform|"
         r"\blearning\b|\bsystems\b|identif|survival analysis|nomogram|architectur|screening|mixture[\s-]of[\s-]experts")

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
N4_ANY = R(r"\bmerging\b|\bcompress|\bloss\b|fine[\s-]?tun|finetun|instruction[\s-]?tun|"
           r"\balign(ing|ment)\b|preference (learning|data|alignment)|reward (learning|guided|shaping)|"
           r"(?<!^)\bdecoding\b|\bdistill|sampling strateg|^\s*calibrat|calibration of")
TECH_MAYBE = R(r"distill|adapter|contrastive|few[\s-]?shot|zero[\s-]?shot|missing modalit|incomplete modalit|"
               r"pre[\s-]?train|training|transfer|prompt|tuning|align|domain adaptation|augment|curriculum|"
               r"self[\s-]?supervised|representation learning|regulari[sz]|meta[\s-]?learning|continual|incremental|"
               r"fine[\s-]?tun|merging|decoding|calibrat|loss")
SYS_HEAD = R(r"\bmodels?\b|pretrainer|learner|framework|network|\bnet\b|transformer|architecture|system|assistant|generalist|foundation|"
             r"pipeline|platform|toolkit|\bmllm|\bvlm|\blmm|\bllm|encoder|generator|engine|robot|interface")
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
N3_POST = R(r"^[^:]{1,60}:\s*(a |an )?(systematic |comprehensive |empirical |comparative |critical |thorough |holistic |fine-grained )?"
             r"(evaluating|evaluation|probing|benchmarking|assessing|assessment|diagnosing|measuring|investigating|examining|"
             r"understanding|revisiting|unveiling|analy[sz]ing|analysis|quantifying|testing|auditing|how|can|do|does|are|is|what|"
             r"when|why|which|towards understanding|exploring (the )?(capabilit|limit|abilit))\b")
N3_PROPOSE = R(r"\b(evaluat|assess|understand|investigat|analy[sz]|measur|probing|diagnos|benchmark|examin|reveal|identify)\w* and "
               r"(enhanc|improv|mitigat|boost|construct|build|address|overcom|alleviat|reduc|leverag|exploit|harness|advanc|bridg|"
               r"fix|repair|tackl|correct|remed|refin|strengthen|elicit|unlock)|\band (a |an )?(new|novel) (method|model|framework|approach)|"
               r"\bwe propose\b|\btowards? (better|improved|robust)|development and (validation|evaluation)|design and (evaluation|validation|implementation)|"
               r"model development|algorithm development|\bdesign,? (development|implementation)")
N7 = R(r"^\s*(using|leveraging|employing|utili[sz]ing|applying|harnessing|adopting|exploring the use of)\b.{0,60}\b(llms?|gpt|chatgpt|language models?|generative ai|\bai\b|chatbots?|foundation models?|copilot)|"
       r"\bapplications? of\b|\bcase study\b|\bcase report\b|\buser study\b|\bsurvey of (users|students|teachers|clinicians|physicians|practitioners|developers|nurses|researchers)|"
       r"perceptions?\b(?! (and|of) (images|scenes|objects))|attitudes?\b|\badoption\b|acceptance|\bintention\b|\bintentions to\b|\bexperiences? (of|with)\b|\bqualitative\b|\binterviews?\b|"
       r"focus group|questionnaire|\bopinions?\b|\bperceived\b|\bstakeholders?\b|\bpilot study\b|\bmixed[\s-]methods?\b|"
       r"in (the )?(classroom|education|teaching|higher education|medical education)|students?'|teachers?'|"
       r"\buse of (llms?|gpt|chatgpt|generative ai|ai|large language|chatbots?)|\bimpact of (llms?|gpt|chatgpt|generative ai|ai\b|large language)|"
       r"\bethic|\bpolicy\b|\bpolicies\b|\bregulations? (of|for) (ai|llm|generative|large)|\bregulatory (strateg|framework|perspective|implication|challenge|consideration)|\blegal (implications|issues|aspects|framework)|\bcopyright|\bsocietal|\beconomic|\blabou?r market|\bjobs?\b|\bworkforce|"
       r"\bacademic integrity|\bplagiarism|\bhigher education\b|\bliteracy\b|\bpatients'? (questions|queries)|\bexam(ination)?s?\b|\bboard (exam|questions)|"
       r"\bresponses? to (patient|medical|clinical)|\bquality of (responses|answers|information)|\breadability")

E_STRUCT = R(r"\b(network|net|nets|networks)\b(?! (traffic|operator|slicing|security|intrusion|management|design|planning|topology))|"
             r"\ba(n)? (\w[\w-]*\s){0,8}(model|framework|transformer|architecture|encoder|network|system|approach|method|pipeline|generator|learner|assistant)\b(?!s)|"
             r"\bfusion\b.{0,60}\bfor\b|\bfoundation models? for\b|"
             r"\b(mllms?|vlms?|lmms?|lvlms?|multimodal (large )?language models?|vision[\s-]?language models?|"
             r"vision[\s-]?language[\s-]?action models?|large multimodal models?) for\b(?! (evaluat|assess))|"
             r"\bmulti[\s-]?modal (deep learning |transformer |fusion |attention |neural |graph |large language |foundation |generative |ai )?"
             r"(model|framework|network|transformer|architecture|system|pipeline|encoder)\b(?!s)")
DOMAIN_FM = R(r"(foundation model|\bmllm\b|\bvlm\b|\blmm\b|\blvlm\b|large multimodal model|multimodal large language model|"
              r"vision[\s-]?language model|vision[\s-]?language foundation|multimodal foundation model) (for|in|of) (the )?[a-z]")

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

def classify(title):
    """Retorna (classification, code) para um título, seguindo o PROCEDIMENTO do prompt."""
    t = re.sub(r"\s+", " ", (title or '').replace('&amp;', '&')).strip()
    if not t:
        return 'no', 'N0'
    # ------------------------------------------------ N0
    if non_english(t) or NON_ARTICLE.search(t):
        return 'no', 'N0'
    # ------------------------------------------------ signals
    llm = bool(LLM_RX.search(t))
    mm = mm_signal(t)
    agent = bool(AGENT_RX.search(AGENT_NONCOMP.sub(' ', t)))
    agent_classic = bool(AGENT_CLASSIC.search(t))
    if agent and agent_classic and not (llm or re.search(r"(?i)generative|vision[\s-]?language|mllm|vlm|lmm|foundation", t)):
        agent = bool(AGENT_RX.search(AGENT_CLASSIC.sub(' ', AGENT_NONCOMP.sub(' ', t))))
    named = proper_name(t)

    # ------------------------------------------------ N1
    if N1_ALWAYS.search(t):
        rest = N1_ALWAYS.sub(' ', t)
        mm_rest = mm_signal(rest)
        agent_rest = bool(AGENT_RX.search(AGENT_CLASSIC.sub(' ', AGENT_NONCOMP.sub(' ', rest))))
        if not (mm_rest == 'strong' or LLM_RX.search(rest) or agent_rest):
            return 'no', 'N1'
        mm = mm_rest
    has_mm_word = re.search(r"(?i)multi[\s-]?modal|multimodality|bimodal|trimodal|dual[\s-]?modal", t)
    bare = MM_STRONG.sub(lambda m: '' if re.fullmatch(r"(?i)multi[\s-]?modal(ity)?|multimodality|(bi|tri|dual|multi)[\s-]?modal|modalit(y|ies)", m.group(0)) else m.group(0), t)
    only_bare = not MM_STRONG.search(bare) and not MM_COMP_ADJ.search(t) and mm_signal(bare) is None
    t_g = re.sub(r"(?i)\b(model(s|ing|ling|led|ed)?|data|learning|learners?|digital|interactive|games?|corpus|represent\w*|"
                 r"understanding|semantic|features?|student'?s t)\b", " ", t)
    if has_mm_word and only_bare and not llm and not agent and not named:
        if N1_CTX.search(t) and not COMP.search(t):
            return 'no', 'N1'
        if N1_EDU.search(t_g) and not COMP_EDU.search(t) and not COMP.search(t_g):
            return 'no', 'N1'
    if re.search(r"(?i)(multi|dual|bi|tri)[\s-]?modal(ity)? (imaging|contrast\b|probes?|nano\w*|theranost\w*|therap\w*|treatment|"
                 r"analges\w*|anesthe\w*|anaesthe\w*|pain|interventions?|prehabilitation|rehabilitation|exercise|management)", t) \
            and not re.search(r"(?i)learning|\bdeep\b|neural|\bai\b|artificial|llm|language model|transformer|algorithm|automat|"
                              r"segment|classif|registration|fusion|detection|recogni|predict|network|framework|system|radiomic|"
                              r"model\b|models\b|data|computational", t) and not agent:
        return 'no', 'N1'

    t_sec = SEC_PRE_REMOVE.sub(' ', t)
    sec = bool(SEC.search(t_sec))
    arch_m = [m.group(0) for m in ARCH.finditer(t)]
    arch = bool(arch_m)
    bench = bool(BENCH_HEAD.search(t)) and not BENCH_WITH_SYS.search(t)
    n3_post = bool(N3_POST.search(t)) and not N3_PROPOSE.search(t)
    n3_hit = bool(N3_Q.search(t) or N3.search(t) or n3_post) and not N3_PROPOSE.search(t)

    # ------------------------------------------------ A
    if sec:
        if mm == 'strong' or agent or (COGNITIVE.search(t) and (llm or mm or re.search(r"(?i)\bai\b|artificial|machine|neural|deep learning", t))):
            return 'yes', 'A'
        if llm:
            if NARROW.search(t) and not re.search(r"(?i)architect|framework|paradigm|evolution|landscape|ecosystem|agent|design|application|adaptation", t):
                return 'no', ('N5' if NARROW_SEC5.search(t) else 'N4')
            return 'yes', 'A'
        if (mm == 'groups' or SEC_DOMAIN_WEAK.search(t)) and re.search(r"(?i)\bai\b|artificial|learning|neural|deep|machine|intelligen|data[\s-]?driven|model", t):
            return 'maybe', 'M'
    # ------------------------------------------------ B
    if agent:
        return 'yes', 'B'
    # ------------------------------------------------ C
    if arch and (mm or llm):
        weak_only = all(ARCH_WEAK.search(a) for a in arch_m)
        has_system = bool(re.search(r"(?i)\bsystems?\b|end[\s-]?to[\s-]?end|modular", t))
        if bench and weak_only:
            pass
        elif weak_only and N4_CLEAR.search(t) and not has_system:
            pass
        elif weak_only and n3_hit and not named and not has_system:
            pass
        else:
            return 'yes', 'C'
    if ENG.search(t) and (mm or llm or agent):
        return 'yes', 'C'
    # ------------------------------------------------ D
    retr = RETR_STRONG.search(t)
    mem = MEMORY.search(t) and not MEM_HW.search(t) and (llm or mm or agent)
    if (retr or mem) and (NONTEXT.search(t) or mm):
        return 'yes', 'D'
    # ------------------------------------------------ E
    if mm and not bench and not (n3_hit and not named):
        if not N4_CLEAR.search(t):
            if named:
                if n3_post or (N3_Q.search(t) and not SYS_HEAD.search(t)):
                    pass
                elif (N4_ANY.search(t) or TECH_MAYBE.search(t)) and not SYS_HEAD.search(t):
                    return 'maybe', 'M'
                else:
                    return 'yes', 'E'
            elif E_STRUCT.search(t):
                if (N4_ANY.search(t) or re.search(r"(?i)distill|prompt tuning|adapter", t)) and not re.search(r"(?i)\b(network|framework|architecture|system)\b", t):
                    return 'maybe', 'M'
                return 'yes', 'E'
    if DOMAIN_FM.search(t) and not bench and not n3_hit and not N4_CLEAR.search(t):
        return 'yes', 'E'
    if named and re.search(r"(?i)foundation model|\bmllm\b|\bvlm\b|\blmm\b|multimodal", t) and not bench and not N4_CLEAR.search(t) and not N3_Q.search(t):
        return 'yes', 'E'

    any_sig = bool(mm or llm or agent)
    if not any_sig:
        if (sec or arch) and SEC_DOMAIN_WEAK.search(t) and re.search(r"(?i)\bai\b|artificial|learning|neural|deep|machine", t):
            return 'maybe', 'M'
        return 'no', 'N6'
    # ------------------------------------------------ N2..N7
    if bench:
        return 'no', 'N2'
    if n3_hit:
        return 'no', 'N3'
    if N4_CLEAR.search(t):
        return 'no', 'N4'
    if N4_ANY.search(t):
        if mm or named:
            return 'maybe', 'M'
        return 'no', 'N4'
    if N5.search(t):
        return 'no', 'N5'
    if N7.search(t) and not mm and not named:
        return 'no', 'N7'
    return 'maybe', 'M'


# =====================================================================================
# Entrada e saída
# =====================================================================================
def _split_records(text):
    """Divide o texto CSV em registros brutos (quebra de linha fora de aspas),
    preservando cada registro exatamente como está no arquivo."""
    recs, start, inq = [], 0, False
    for i, ch in enumerate(text):
        if ch == '"':
            inq = not inq
        elif ch == '\n' and not inq:
            recs.append(text[start:i])
            start = i + 1
    tail = text[start:]
    return recs, tail


def processar_planilha(caminho_in, caminho_out, coluna_titulo='title'):
    with open(caminho_in, encoding='utf-8', newline='') as f:
        texto = f.read()
    recs, tail = _split_records(texto)
    if tail:  # último registro sem quebra de linha final
        recs.append(tail)
        final_nl = False
    else:
        final_nl = True
    if not recs:
        sys.exit("Arquivo vazio.")

    # separador: detectado no cabeçalho (',' ';' ou tab)
    cab = recs[0].rstrip('\r').lstrip('﻿')
    try:
        sep = csv.Sniffer().sniff(cab, delimiters=',;\t').delimiter
    except csv.Error:
        sep = ','
    colunas = next(csv.reader([cab], delimiter=sep))
    idx = [c.strip().lower() for c in colunas].index(coluna_titulo.lower()) \
        if coluna_titulo.lower() in [c.strip().lower() for c in colunas] else None
    if idx is None:
        sys.exit(f"Coluna '{coluna_titulo}' não encontrada. Colunas: {colunas}")

    def anexar(reg, *vals):
        cr = '\r' if reg.endswith('\r') else ''
        base = reg[:-1] if cr else reg
        return base + ''.join(sep + v for v in vals) + cr

    saida = [anexar(recs[0], 'classification', 'code')]
    cont = Counter()
    for reg in recs[1:]:
        if reg.strip('\r') == '':
            saida.append(reg)  # linha em branco: mantida sem alteração
            continue
        campos = next(csv.reader(io.StringIO(reg), delimiter=sep))
        titulo = campos[idx] if idx < len(campos) else ''
        cls, code = classify(titulo)
        cont[(cls, code)] += 1
        saida.append(anexar(reg, cls, code))

    with open(caminho_out, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(saida) + ('\n' if final_nl else ''))
    return cont


def processar_linhas(caminho_in, caminho_out):
    cont = Counter()
    saida = []
    with open(caminho_in, encoding='utf-8') as f:
        for linha in f:
            linha = linha.rstrip('\r\n')
            if not linha.strip():
                continue
            key, _, titulo = linha.partition('|')
            cls, code = classify(titulo.strip())
            cont[(cls, code)] += 1
            saida.append(f"{key.strip()};{cls};{code}")
    with open(caminho_out, 'w', encoding='utf-8', newline='') as f:
        f.write('\n'.join(saida) + '\n')
    return cont


def resumo(cont):
    por_cls = Counter()
    for (cls, _), n in cont.items():
        por_cls[cls] += n
    total = sum(por_cls.values())
    print(f"Total de registros: {total}", file=sys.stderr)
    for cls in ('yes', 'maybe', 'no'):
        cods = sorted(((c, n) for (k, c), n in cont.items() if k == cls), key=lambda x: -x[1])
        det = ' · '.join(f"{c} {n}" for c, n in cods)
        print(f"  {cls:<5} {por_cls[cls]:>6}   {det}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Triagem por título (RSL sistemas multimodais e agênticos).")
    ap.add_argument('entrada', nargs='?', help="CSV completo (formato b) ou arquivo 'key | title' (formato a)")
    ap.add_argument('-o', '--saida', help="arquivo de saída (padrão: <entrada>_triagem.<ext>)")
    ap.add_argument('--formato', choices=['auto', 'planilha', 'linhas'], default='auto',
                    help="auto (padrão): .csv = planilha; outro = linhas 'key | title'")
    ap.add_argument('--coluna-titulo', default='title', help="nome da coluna de título (padrão: title)")
    ap.add_argument('--titulo', help="classifica um único título e imprime 'classification;code'")
    a = ap.parse_args()

    if a.titulo is not None:
        print(';'.join(classify(a.titulo)))
        return
    if not a.entrada:
        ap.error("informe o arquivo de entrada ou --titulo")

    formato = a.formato
    if formato == 'auto':
        formato = 'planilha' if a.entrada.lower().endswith(('.csv', '.tsv')) else 'linhas'
    base, ext = os.path.splitext(a.entrada)
    saida = a.saida or f"{base}_triagem{ext or '.txt'}"

    if formato == 'planilha':
        cont = processar_planilha(a.entrada, saida, a.coluna_titulo)
    else:
        cont = processar_linhas(a.entrada, saida)
    resumo(cont)
    print(f"Saída gravada em: {saida}", file=sys.stderr)


if __name__ == '__main__':
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Journal quotidien.
Principe : sources réelles (PubMed, Wiktionnaire, presse) -> résumé par Gemini (offre gratuite)
-> vérification par le programme (page lisible, citation retrouvée, date non passée) -> page HTML.
  python3 journal.py         édition réelle (variable GEMINI_API_KEY requise)
  python3 journal.py --demo  aperçu avec données FICTIVES"""
import collections, datetime as dt, html, itertools, json, os, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

API = "https://generativelanguage.googleapis.com/v1beta"
KEY = os.environ.get("GEMINI_API_KEY", "")
NOW = dt.datetime.now(ZoneInfo("Europe/Paris"))
TODAY = NOW.date().isoformat()
JOURS = "lundi mardi mercredi jeudi vendredi samedi dimanche".split()
MOIS = "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split()
DATE_FR = f"{JOURS[NOW.weekday()]} {NOW.day}{'er' if NOW.day == 1 else ''} {MOIS[NOW.month - 1]} {NOW.year}"
NOTES = []  # incidents du jour, affichés en bas de page

def log(m): print(m, file=sys.stderr)

def get(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data, {"User-Agent": "Mozilla/5.0 (journal-perso)", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def clean(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(str(s)))
    s = s.replace("\u2019", "'").replace("\xa0", " ")
    return re.sub(r"\s+", " ", re.sub(r"[«»“”]", '"', s)).strip()

def norm(s): return re.sub(r"[^\w]+", " ", clean(s).lower()).strip()  # insensible à la ponctuation

# ---------- IA (Gemini, offre gratuite) ----------
_modeles = []

def modeles():
    """Liste les modèles Flash disponibles pour votre clé (évite de dépendre d'un nom qui disparaît)."""
    if not _modeles:
        j = json.loads(get(f"{API}/models?pageSize=200", headers={"x-goog-api-key": KEY}))
        n = [m["name"].split("/")[-1] for m in j.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        n = sorted((x for x in n if "flash" in x and not re.search(r"image|tts|live|audio|embed|robot|computer|native|exp", x)), reverse=True)
        _modeles.extend(sorted(n, key=lambda x: "lite" not in x))  # modèles « lite » d'abord
    return _modeles

def llm(prompt):
    time.sleep(6)  # reste sous la limite de requêtes par minute
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}).encode()
    for m in modeles():
        try:
            r = json.loads(get(f"{API}/models/{m}:generateContent", body,
                               {"Content-Type": "application/json", "x-goog-api-key": KEY}, 90))
            t = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
            try: return json.loads(t)
            except ValueError: return json.loads(re.search(r"\{.*\}", t, re.S).group(0))
        except Exception as ex:
            log(f"modèle {m} : {ex}")
            if "429" in str(ex): time.sleep(40)
    NOTES.append("Résumé par l'IA indisponible (quota ou panne temporaire).")
    return None

# ---------- Sources réelles ----------
def bing(q, n=5):
    # Actualités récentes (flux RSS public de Bing Actualités). Préfixe « de: » = recherche en allemand.
    mkt = "de-DE" if q.startswith("de:") else "fr-FR"
    q = q.removeprefix("de:").replace("{mois}", MOIS[NOW.month - 1]).replace("{annee}", str(NOW.year))
    u = "https://www.bing.com/news/search?" + urllib.parse.urlencode({"q": q, "format": "rss", "setmkt": mkt})
    res = []
    for it in ET.fromstring(get(u)).iter("item"):
        l = it.findtext("link", "")
        res.append({"url": urllib.parse.parse_qs(urllib.parse.urlparse(l).query).get("url", [l])[0]})
    return res[:n]

def mele(listes):  # intercale les résultats de plusieurs recherches (aucune ne monopolise la place)
    return [x for t in itertools.zip_longest(*listes) for x in t if x]

def recherche(qs, n=5):
    res = []
    for q in qs:
        try: res.append(bing(q, n))
        except Exception as ex: log(f"recherche « {q} » : {ex}")
    out = mele(res)
    if not out:
        NOTES.append("La recherche d'actualités n'a rien renvoyé (panne ou blocage possible) : les rubriques concernées peuvent être vides à tort.")
    return out

def page(url):
    h = get(url, timeout=25)
    return clean(re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", " ", h))[:5000]

def fr(d):
    try:
        d = dt.date.fromisoformat(str(d)[:10]); return f"{d.day} {MOIS[d.month - 1]} {d.year}"
    except ValueError: return str(d)

def pubmed(requete, jours):
    # Articles indexés PubMed (donc évalués par les pairs, hors prépublications) via Europe PMC.
    d1 = (NOW - dt.timedelta(days=jours)).date().isoformat()
    q = f"({requete}) AND SRC:MED AND HAS_ABSTRACT:y AND FIRST_PDATE:[{d1} TO {TODAY}]"
    u = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
        {"query": q, "format": "json", "resultType": "core", "pageSize": 15, "sort": "FIRST_PDATE_D desc"})
    res = []
    for r in json.loads(get(u)).get("resultList", {}).get("result", []):
        if not r.get("abstractText"): continue
        doi = r.get("doi")
        url = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{r.get('pmid')}/"
        vol = r.get("journalVolume") or ""
        iss = f"({r['issue']})" if r.get("issue") else ""
        pag = f":{r['pageInfo']}" if r.get("pageInfo") else ""
        titre = clean(r.get("title", ""))
        ref = (f"{str(r.get('authorString', '')).rstrip('.')}. {titre.rstrip('.')}. {r.get('journalTitle', '')}. {r.get('pubYear', '')}"
               + (f";{vol}{iss}{pag}" if vol else "") + (f". DOI : {doi}" if doi else f". PMID : {r.get('pmid')}"))
        meta = {"titre": titre, "publication": f"{fr(r.get('firstPublicationDate', ''))}, {r.get('journalTitle', '')}", "ref": ref}
        texte = clean(f"Titre : {titre}. Revue : {r.get('journalTitle')}. Date : {r.get('firstPublicationDate')}. Résumé : {r['abstractText']}")[:5000]
        res.append({"url": url, "texte": texte, "meta": meta})
    return res

def sur(f, *a):
    try: return f(*a)
    except Exception as ex:
        log(f"{f.__name__} : {ex}")
        NOTES.append("La base d'études (PubMed via Europe PMC) n'a pas répondu : les études du jour peuvent manquer.")
        return []

# ---------- Sélection + vérification ----------
REGLES = ("Règles absolues : utilise UNIQUEMENT le texte des sources ci-dessous ; n'invente aucune information ; "
          "écris « non précisé » si une donnée manque ; « source » = numéro de la source utilisée ; « preuve » = citation "
          "EXACTE (10 à 25 mots) copiée MOT POUR MOT dans le texte de la source, dans sa langue d'origine, SANS la traduire, "
          "et contenant la date quand l'item est un événement ; écarte les événements terminés ; rédige tout le reste en français.")
SCHEMA = ('Réponds uniquement en JSON : {"items":[{"source":1,"titre":"","resume":"2 phrases maximum","details":{},'
          '"ville":"","statut":"confirmé ou incertain","debut":"AAAA-MM-JJ ou vide","fin":"AAAA-MM-JJ ou vide","preuve":""}]}')
CONS_ORTHO = ("Rubrique « Recherche en orthophonie ». Parmi les sources (articles PubMed évalués par les pairs), choisis la "
              "plus utile à la pratique orthophonique (langage oral/écrit, troubles du langage, cognition mathématique, "
              "mémoire, fonctions exécutives, évaluation, intervention). « details » contient exactement ces clés : "
              "« Question de recherche », « Méthode » (type d'étude, population, effectif, mesures), « Résultats », "
              "« Limites et portée clinique ». N'affirme jamais qu'une intervention est efficace si le résumé ne le "
              "démontre pas ; précise que l'analyse repose sur le résumé seul.")
CONS_ECH = ("Rubrique « Chasse aux échantillons gratuits », ville : {ville}. Retiens uniquement les événements à venir ou en "
            "cours qui ont lieu à {ville} (pop-up, lancement, animation de marque, événement beauté ou parfumerie). "
            "« ville » = {ville}. « statut » = confirmé UNIQUEMENT si le texte dit explicitement que des produits ou "
            "échantillons sont offerts ou distribués ; sinon incertain. Une entrée gratuite n'est PAS un produit offert. "
            "« details » : « Marque », « Adresse », « Dates et horaires », « Offert », « Conditions d'accès », "
            "« Inscription », « Âge, quantité, achat ».")
CONS_OBS = ("Rubrique « Obsession du moment : {theme} ». Retiens les éléments les plus intéressants (presse spécialisée, étude "
            "scientifique, événement à Strasbourg ou Paris, lancement). « details » : « Nature de l'information » "
            "(connaissance établie, résultat préliminaire, hypothèse, discours commercial ou actualité) et « À retenir ». "
            "Ne présente jamais un discours marketing comme un fait scientifique.")
CONS_VIE = ("Rubrique « Art de vivre ». Territoires, par ordre de priorité : {territoires} Thèmes : gastronomie, restaurants "
            "remarquables, hôtels de charme ou de luxe, spas et bien-être, bars et salons de thé, expériences originales, "
            "nouveautés de l'hôtellerie. Sélection éditoriale : uniquement des expériences singulières, jamais de liste "
            "générique ; au plus un item hors Alsace. « details » : « Lieu », « Ce qui la distingue », « Prix » (seulement "
            "s'il figure dans le texte), « Infos pratiques ».")
CONS_SORTIE = ("Rubrique « Idée de sortie ». Choisis une sortie à faire entre le {debut} et le {fin} (événement, exposition, "
               "concert, marché, spectacle, visite, dégustation), de préférence culturelle, gourmande ou sensorielle. "
               "Territoires, par ordre de priorité : {territoires} « debut » et « fin » (AAAA-MM-JJ) sont OBLIGATOIRES : "
               "pour un événement d'un seul jour, debut = fin ; ignore les événements sans date précise. « details » : "
               "« Lieu et adresse », « Dates et horaires », « Prix », « Réservation », « Infos pratiques ».")
BILAN = {}  # explique pourquoi une rubrique est vide

def verif_sortie(it):
    d, f = str(it.get("debut", "")), str(it.get("fin", ""))
    if not (re.fullmatch(r"\d{4}-\d\d-\d\d", d) and re.fullmatch(r"\d{4}-\d\d-\d\d", f)): return "dates absentes"
    if f < TODAY or d > (NOW + dt.timedelta(days=7)).date().isoformat(): return "hors des 7 prochains jours"
    if not any(re.search(rf"(?<!\d){int(x[8:])}(?!\d)", str(it.get("preuve", ""))) for x in (d, f)): return "date absente de la citation"
    return ""

def quand(it):
    d, f = dt.date.fromisoformat(it["debut"]), dt.date.fromisoformat(it["fin"])
    j = lambda x: f"{JOURS[x.weekday()]} {x.day} {MOIS[x.month - 1]}"
    return f"Le {j(d)}" if d == f else (f"Jusqu'au {j(f)}" if d <= NOW.date() else f"Du {j(d)} au {j(f)}")

def rubrique(cle, cands, consigne, vus, nmax, verif=None):
    pages, vu, echecs = [], set(vus), 0
    for c in cands:
        if c["url"] in vu or len(pages) >= 12: continue
        vu.add(c["url"])
        try:
            if "texte" not in c: c["texte"] = page(c["url"])
            if len(c["texte"]) > 300: pages.append(c)
        except Exception as ex: echecs += 1; log(f"illisible {c['url']} : {ex}")
    BILAN[cle] = f"{len(cands)} pages trouvées, {len(pages)} lisibles"
    if not pages:
        if echecs: NOTES.append("Des pages trouvées n'ont pas pu être lues (accès refusé par les sites ?) : une rubrique peut être vide à tort.")
        return []
    src = "\n\n".join(f"### SOURCE {i + 1}\n{p['texte'][:3000]}" for i, p in enumerate(pages))
    rep = llm(f"Date du jour : {TODAY}. {consigne}\n{REGLES} Propose jusqu'à {nmax + 2} items, du plus au moins pertinent ; "
              f"liste vide si rien de pertinent.\n{SCHEMA}\n\n{src}")
    items = rep.get("items", []) if isinstance(rep, dict) else []
    items = items if isinstance(items, list) else []
    out, rej = [], collections.Counter()
    for it in items:
        if len(out) >= nmax: break
        try: p = pages[int(it["source"]) - 1]
        except (KeyError, TypeError, ValueError, IndexError): rej["source inconnue"] += 1; continue
        pr = norm(it.get("preuve", ""))
        if not it.get("titre") or len(pr) < 25 or pr not in norm(p["texte"][:3000]): rej["citation introuvable"] += 1; continue
        fin = str(it.get("fin", ""))
        if re.fullmatch(r"\d{4}-\d\d-\d\d", fin) and fin < TODAY: rej["événement terminé"] += 1; continue
        if verif and (msg := verif(it)): rej[msg] += 1; continue
        it["url"] = p["url"]
        if p.get("meta"):  # études : titre et références viennent de la base, pas de l'IA
            m = p["meta"]; d = it.get("details") if isinstance(it.get("details"), dict) else {}
            d = {k: v for k, v in d.items() if k not in ("Publication", "Référence", "Titre original")}
            it["titre"], it["badge"] = m["titre"], "Évalué par les pairs, d'après le résumé"
            it["details"] = {"Publication": m["publication"], "Référence": m["ref"], **d}
        out.append(it); vus.append(p["url"])
    BILAN[cle] += f", {len(items)} proposées par l'IA, {len(out)} retenue(s)" + (
        " ; rejets : " + ", ".join(f"{k} ×{v}" for k, v in rej.items()) if rej else "")
    log(f"[{cle}] {BILAN[cle]}")
    return out

def mot(vus):
    deja = ", ".join(w for w in vus if not w.startswith("http"))[-1500:]
    r = llm("Propose UN mot français élégant, précis ou nuancé, réellement employé (pas de néologisme), absent de cette "
            f'liste : {deja}. Réponds en JSON : {{"mot":""}}')
    w = str((r or {}).get("mot", "")).strip().lower() if isinstance(r, dict) else ""
    if not w or w in vus: return None
    j = json.loads(get("https://fr.wiktionary.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": w, "format": "json"})))
    ext = next(iter(j["query"]["pages"].values())).get("extract", "")
    if len(ext) < 100: return None
    r = llm(f"À partir UNIQUEMENT de l'article du Wiktionnaire ci-dessous sur « {w} », rédige la fiche. JSON : "
            '{"mot":"","nature":"","definition":"","registre":"","etymologie":"vide si absente de l\'article",'
            '"exemple":"une phrase naturelle et originale"}\n\n' + ext[:3500])
    if not isinstance(r, dict) or not r.get("definition"): return None
    r["url"] = "https://fr.wiktionary.org/wiki/" + urllib.parse.quote(w)
    vus += [w, r["url"]]
    return r

def construire(cfg, vus):
    k = NOW.toordinal()
    rot = lambda l, n=1: [l[(k + i) % len(l)] for i in range(n)]  # change de requêtes chaque jour
    ed = {}
    def essai(nom, f, defaut):
        try: return f()
        except Exception as ex:
            NOTES.append(f"Rubrique « {nom} » : source indisponible aujourd'hui."); log(f"{nom} : {ex!r}"); return defaut
    ed["mot"] = essai("Le mot du jour", lambda: mot(vus), None)
    ed["ortho"] = essai("Recherche en orthophonie", lambda: rubrique("ortho", mele(
        [sur(pubmed, q, 120) for q in rot(cfg["orthophonie"]["requetes"], 3)]), CONS_ORTHO, vus, 1), [])

    def echant():
        out = []
        for v, qs in cfg["echantillons"].items():
            for it in rubrique(f"echant:{v}", recherche(qs), CONS_ECH.format(ville=v), vus, 3):
                it["ville"] = v
                pr = re.sub(r"entr[ée]e (libre|gratuite)", "", norm(it["preuve"]))
                ok = it.get("statut") == "confirmé" and re.search(r"gratuit|offert|échantillon|cadeau|distribu|goodies", pr)
                it["badge"] = "Distribution gratuite confirmée" if ok else "Cadeau non garanti : à vérifier auprès de l'organisateur"
                if not re.fullmatch(r"\d{4}-\d\d-\d\d", str(it.get("fin", ""))): it["badge"] += " (dates à vérifier)"
                out.append(it)
        return out
    ed["echant"] = essai("La chasse aux échantillons gratuits", echant, [])

    def obs():
        o = cfg["obsession"]
        cands = sur(pubmed, rot(o["requetes_science"])[0], 150)[:4] + recherche(rot(o["requetes_presse"], 3))
        return rubrique("obsession", cands, CONS_OBS.format(theme=o["theme"]), vus, 2)
    ed["obsession"] = essai("Obsession du moment", obs, [])
    a = cfg["art_de_vivre"]
    ed["vie"] = essai("Art de vivre", lambda: rubrique("vie", recherche(rot(a["requetes"], 6)),
                                                       CONS_VIE.format(territoires=a["territoires"]), vus, 2), [])

    def sortie():
        s = cfg["sortie"]
        c = CONS_SORTIE.format(territoires=s["territoires"], debut=TODAY, fin=(NOW + dt.timedelta(days=7)).date().isoformat())
        out = rubrique("sortie", recherche(rot(s["requetes"], 6)), c, vus, s.get("nombre", 1), verif_sortie)
        for it in out: it["badge"] = quand(it)
        return out
    ed["sortie"] = essai("Idée de sortie", sortie, [])
    return ed

# ---------- Page ----------
CSS = """:root{--bg:#F1F0ED;--ink:#1D1A20;--mut:#6C6770;--rule:#D8D5CF;--acc:#5A3447}
@media(prefers-color-scheme:dark){:root{--bg:#141216;--ink:#EBE7E3;--mut:#9B959E;--rule:#2C292F;--acc:#D2A9BD}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:1.0625rem/1.65 "Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif}
main{max-width:40rem;margin:0 auto;padding:max(3rem,env(safe-area-inset-top)) 1.5rem 5rem}
header{padding-bottom:2rem;border-bottom:1px solid var(--rule)}
h1{font-weight:400;font-size:clamp(2rem,8vw,3.2rem);line-height:1.1;margin:0;letter-spacing:-.01em}
header p{margin:.7rem 0 0;color:var(--mut);font-style:italic;font-size:.95rem}
section{padding:2.6rem 0 .6rem;border-bottom:1px solid var(--rule)}
h2{font-weight:400;font-style:italic;font-size:1.5rem;margin:0 0 1.3rem;color:var(--acc)}
article{margin:0 0 2.2rem}h3{font-size:1.17rem;font-weight:600;line-height:1.3;margin:0 0 .4rem}
article>p{margin:.2rem 0}
a{color:inherit;text-decoration-color:var(--rule);text-underline-offset:.2em}
a:hover,a:focus-visible{color:var(--acc);text-decoration-color:currentColor}
dl{margin:1rem 0 .8rem;font-size:.95rem;display:grid;grid-template-columns:8rem 1fr;gap:.4rem 1rem}
dt{color:var(--mut)}dd{margin:0}
@media(max-width:32rem){dl{grid-template-columns:1fr;gap:0}dd{margin-bottom:.5rem}}
.badge{display:inline-block;margin:.1rem 0 .5rem;padding:.05rem .7rem;border:1px solid var(--acc);border-radius:99px;font-size:.82rem;color:var(--acc)}
.ville{font-style:italic;color:var(--mut);margin:1.5rem 0 .7rem}
.src,.vide,footer{color:var(--mut);font-size:.9rem}
footer{padding-top:2rem}footer p{margin:.4rem 0}
.demo{margin:0;padding:.7rem 1rem;text-align:center;font-size:.9rem;background:var(--acc);color:var(--bg)}"""

def page_html(ed, demo, cfg):
    e = lambda s: html.escape(str(s), quote=True)
    vide = lambda t: f'<p class="vide">{e(t)}</p>'
    diag = lambda k: f" ({BILAN[k]})" if k in BILAN else ""
    def carte(it):
        d = it.get("details") if isinstance(it.get("details"), dict) else {}
        dl = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in d.items() if str(v).strip())
        b = f'<p class="badge">{e(it["badge"])}</p>' if it.get("badge") else ""
        h = e(it["url"]); dom = e(urllib.parse.urlparse(it["url"]).netloc.removeprefix("www."))
        return (f'<article><h3><a href="{h}" rel="noopener">{e(it["titre"])}</a></h3>{b}<p>{e(it.get("resume", ""))}</p>'
                f'<dl>{dl}</dl><p class="src">Source : <a href="{h}" rel="noopener">{dom}</a></p></article>')
    m = ed.get("mot")
    mot_html = carte({"titre": m["mot"], "resume": m["definition"], "url": m["url"], "details": {
        "Nature": m.get("nature"), "Registre": m.get("registre"), "Étymologie": m.get("etymologie"), "Exemple": m.get("exemple")}}) \
        if m else vide("Pas de mot vérifié aujourd'hui.")
    ech = ""
    for v in cfg["echantillons"]:
        l = "".join(carte(i) for i in ed["echant"] if i.get("ville") == v)
        ech += f'<p class="ville">{e(v)}</p>' + (l or vide(f"Aucune opportunité confirmée aujourd'hui à {v}." + diag(f"echant:{v}")))
    cartes = lambda k, msg: "".join(map(carte, ed.get(k, []))) or vide(msg + diag(k))
    secs = [("Le mot du jour", mot_html),
            ("Recherche en orthophonie", cartes("ortho", "Aucune étude suffisamment pertinente et récente aujourd'hui.")),
            ("La chasse aux échantillons gratuits", ech),
            (f"Mon obsession du moment : {cfg['obsession']['theme'].lower()}", cartes("obsession", "Rien de vérifié et d'intéressant aujourd'hui.")),
            ("Art de vivre", cartes("vie", "Aucune découverte vérifiée aujourd'hui.")),
            ("Idée de sortie, d'ici sept jours", cartes("sortie", "Aucune sortie vérifiée pour les 7 prochains jours."))]
    corps = "".join(f"<section><h2>{e(t)}</h2>{c}</section>" for t, c in secs)
    notes = "".join(f"<p>{e(n)}</p>" for n in dict.fromkeys(NOTES))
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow"><title>Journal du {e(DATE_FR)}</title><style>{CSS}</style></head><body>'
            + ('<p class="demo">Aperçu : toutes les données ci-dessous sont fictives.</p>' if demo else "")
            + f'<main><header><h1>{e(DATE_FR.capitalize())}</h1><p>Édition préparée à {NOW:%H:%M}</p></header>{corps}'
            f'<footer>{notes}<p>Résumés rédigés par une IA à partir des pages citées. Le programme vérifie que chaque lien '
            f'répond et que la citation justificative figure dans la page. Vérifiez toujours auprès de la source ou de l\'organisateur.</p>'
            f'</footer></main></body></html>')

DEMO = {
    "mot": {"mot": "chatoiement", "nature": "nom masculin", "registre": "soutenu", "etymologie": "",
            "definition": "Reflet changeant d'une surface sous la lumière.",
            "exemple": "Le chatoiement du flacon sur l'étagère trahissait la fin de l'après-midi.",
            "url": "https://example.org/mot-fictif"},
    "ortho": [{"titre": "Exemple fictif : intervention langagière auprès d'enfants de 5 ans", "url": "https://example.org/etude-fictive",
               "resume": "Entrée fictive pour montrer la mise en page d'une étude.", "badge": "Évalué par les pairs, d'après le résumé",
               "details": {"Titre original": "(fictif)", "Publication": "(fictif)", "Question de recherche": "(fictif)",
                           "Méthode": "(fictif)", "Résultats": "(fictif)", "Limites et portée clinique": "(fictif)"}}],
    "echant": [{"ville": "Strasbourg", "titre": "Exemple fictif : pop-up d'une maison imaginaire", "url": "https://example.org/popup-fictif",
                "resume": "Événement fictif servant uniquement d'illustration.",
                "badge": "Cadeau non garanti : à vérifier auprès de l'organisateur",
                "details": {"Marque": "Maison Imaginaire", "Adresse": "(fictive)", "Dates et horaires": "(fictifs)", "Offert": "non précisé"}}],
    "obsession": [{"titre": "Exemple fictif : étude sur la mémoire olfactive", "url": "https://example.org/olfaction-fictive",
                   "resume": "Entrée fictive.", "details": {"Nature de l'information": "Résultat préliminaire", "À retenir": "(fictif)"}}],
    "vie": [{"titre": "Exemple fictif : table d'hôtes imaginaire en Forêt-Noire", "url": "https://example.org/table-fictive",
             "resume": "Entrée fictive.", "details": {"Lieu": "(fictif)", "Prix": "non précisé"}}],
    "sortie": [{"titre": "Exemple fictif : exposition imaginaire à Strasbourg", "url": "https://example.org/sortie-fictive",
               "resume": "Entrée fictive.", "badge": "Jusqu'au dimanche 12 octobre",
               "details": {"Lieu et adresse": "(fictif)", "Dates et horaires": "(fictifs)", "Prix": "non précisé", "Réservation": "non précisé"}}],
}

def main():
    demo = "--demo" in sys.argv
    cfg = json.load(open("config.json", encoding="utf-8"))
    vus = [] if demo or not os.path.exists("etat.json") else json.load(open("etat.json", encoding="utf-8"))["vus"]
    if demo: ed = DEMO
    elif not KEY: sys.exit("Clé GEMINI_API_KEY absente : voir l'étape « clé Gemini » des instructions.")
    else: ed = construire(cfg, vus)
    if not any(ed.values()):
        sys.exit("Aucune rubrique générée : l'édition précédente reste en ligne.")
    os.makedirs("site", exist_ok=True)
    open("site/index.html", "w", encoding="utf-8").write(page_html(ed, demo, cfg))
    open("site/robots.txt", "w").write("User-agent: *\nDisallow: /\n")
    if not demo:  # mémoire anti-doublons (liens et mots déjà publiés)
        json.dump({"vus": vus[-600:]}, open("etat.json", "w", encoding="utf-8"), ensure_ascii=False)

if __name__ == "__main__":
    main()

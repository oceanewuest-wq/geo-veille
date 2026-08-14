#!/usr/bin/env python3
"""
geo-veille — digest géopolitique quotidien ("Geopolitical news").

Pipeline : flux RSS → sélection des sujets par Gemini → page HTML (docs/index.html).
Onglet Médias FR en français, le reste en anglais. Double-registre par sujet :
« en clair » visible, version formelle repliable. Images tirées des flux RSS.

Usage :  python3 veille.py              # run complet (RSS + Gemini)
         python3 veille.py --html-only  # reconstruit la page depuis docs/digest.json
Clé    :  GEMINI_API_KEY dans l'environnement ou dans .env
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path

RACINE = Path(__file__).resolve().parent
SORTIE = RACINE / "docs" / "index.html"
CACHE = RACINE / "docs" / "digest.json"
# Cascade : si un modèle est à court de quota gratuit (20 req/jour chacun) ou
# indisponible, on passe au suivant.
GEMINI_MODELES = ["gemini-flash-latest", "gemini-3.5-flash", "gemini-flash-lite-latest"]
UA = "Mozilla/5.0 (compatible; geo-veille/1.0; digest personnel RSS)"

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"

L10N = {
    "fr": {"clair": "En clair", "formel": "Version formelle", "autres": "Autres",
           "langue": "français", "date_fmt": "%d/%m %H:%M"},
    "en": {"clair": "In plain terms", "formel": "Formal version", "autres": "Other",
           "langue": "anglais", "date_fmt": "%b %d, %H:%M"},
}


# ── Utilitaires ──────────────────────────────────────────────────────────────

def charger_env():
    """Charge un éventuel fichier .env (KEY=valeur) dans os.environ."""
    env = RACINE / ".env"
    if env.exists():
        for ligne in env.read_text().splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                k, v = ligne.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()
    except ssl.SSLCertVerificationError:
        # macOS : le python3 système n'a parfois pas les certificats racine.
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()


def nettoyer_html(texte):
    texte = re.sub(r"<[^>]+>", " ", texte or "")
    return re.sub(r"\s+", " ", unescape(texte)).strip()


def parser_date(brute):
    if not brute:
        return None
    try:
        return parsedate_to_datetime(brute)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(brute.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Récupération RSS / Atom ──────────────────────────────────────────────────

def extraire_image(item):
    """Cherche une image dans un item RSS (media:content, media:thumbnail, enclosure)."""
    for tag in (f"{MEDIA}content", f"{MEDIA}thumbnail"):
        for el in item.findall(tag):
            u = el.get("url", "")
            if u and "video" not in (el.get("medium") or "") and "video" not in (el.get("type") or ""):
                return u
    enc = item.find("enclosure")
    if enc is not None and "image" in (enc.get("type") or "") and enc.get("url"):
        return enc.get("url")
    return None


def parser_flux(xml_bytes, nom_source):
    """Retourne les items d'un flux RSS 2.0 ou Atom."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    items = []
    for item in root.iter("item"):  # RSS 2.0
        source_tag = item.find("source")
        items.append({
            "titre": nettoyer_html(item.findtext("title", "")),
            "url": (item.findtext("link") or "").strip(),
            "resume": nettoyer_html(item.findtext("description", ""))[:400],
            "date": parser_date(item.findtext("pubDate")),
            "image": extraire_image(item),
            "source": (source_tag.text.strip() if source_tag is not None and source_tag.text else nom_source),
        })
    if not items:  # Atom
        for entry in root.iter(f"{ATOM}entry"):
            lien = entry.find(f"{ATOM}link")
            items.append({
                "titre": nettoyer_html(entry.findtext(f"{ATOM}title", "")),
                "url": lien.get("href", "") if lien is not None else "",
                "resume": nettoyer_html(entry.findtext(f"{ATOM}summary", "") or entry.findtext(f"{ATOM}content", ""))[:400],
                "date": parser_date(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")),
                "image": None,
                "source": nom_source,
            })
    return [i for i in items if i["titre"] and i["url"]]


def recuperer_categorie(feeds, fenetre_heures):
    """Récupère tous les flux d'une catégorie, filtre sur la fenêtre temporelle, déduplique."""
    limite = datetime.now(timezone.utc) - timedelta(hours=fenetre_heures)
    articles, erreurs = [], []
    for feed in feeds:
        try:
            xml_bytes = http_get(feed["url"])
            items = parser_flux(xml_bytes, feed["nom"])
            for it in items:
                if it["date"] is None or it["date"] >= limite:
                    articles.append(it)
        except Exception as e:
            erreurs.append(f"{feed['nom']}: {type(e).__name__}")
    vus, uniques = set(), []
    for a in sorted(articles, key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        cle = a["titre"].lower()[:80]
        if cle not in vus:
            vus.add(cle)
            uniques.append(a)
    return uniques, erreurs


def feeds_google_news(requetes):
    return [
        {
            "nom": f"Google News — {r['nom']}",
            "url": "https://news.google.com/rss/search?"
                   + urllib.parse.urlencode({"q": r["q"], "hl": "fr", "gl": "FR", "ceid": "FR:fr"}),
        }
        for r in requetes
    ]


# ── Gemini : sélection + double-registre ─────────────────────────────────────

PROMPT = """Tu es analyste géopolitique. Voici des articles récents ({categorie}).

Zones suivies en priorité : {zones}.

Sélectionne les {nb} sujets les plus importants (priorité aux zones suivies pour la géopolitique, mais inclus tout événement majeur ailleurs). Regroupe les articles qui traitent du même sujet. Classe chaque sujet dans un thème, exactement l'un de : {themes}.

Répartis la sélection pour couvrir les différents thèmes dès que les articles fournis le permettent : les thèmes culture, gaming, sport ou société ne doivent pas être écartés au profit de la seule géopolitique.

Pour chaque sujet, rédige EN {langue} deux versions :
- "formel" : 3-5 phrases rigoureuses et factuelles — acteurs précis, dates, chiffres, enjeux. Registre soutenu, aucun sensationnalisme, distingue les faits établis des affirmations des parties.
- "en_clair" : 2-4 phrases simples et accessibles — explique le contexte comme à quelqu'un d'intelligent mais non spécialiste, définis le jargon, dis pourquoi ça compte. Pas de simplification abusive qui déformerait les faits.

Toutes les valeurs texte (titre, theme, zone, formel, en_clair) doivent être en {langue}.

Réponds UNIQUEMENT avec un tableau JSON valide :
[{{"titre": "titre court du sujet", "theme": "thème choisi", "zone": "zone ou région concernée", "formel": "...", "en_clair": "...", "urls": ["url1", "url2"]}}]

Les "urls" doivent être reprises TELLES QUELLES, à l'identique, depuis les articles fournis (2 max par sujet).

Articles :
{articles}"""


def appeler_gemini(prompt, cle, modele):
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{modele}:generateContent")
    corps = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 32768,
            "responseMimeType": "application/json",
        },
    }).encode()
    req = urllib.request.Request(url, data=corps, headers={
        "Content-Type": "application/json",
        "x-goog-api-key": cle,
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def digest_categorie(label, articles, config, cle, lang):
    """Retourne une liste de sujets double-registre, ou None si pas de clé / échec."""
    if not cle or not articles:
        return None
    envoi = articles[: config["max_articles_envoyes_au_llm"]]
    liste = "\n".join(
        f"- [{a['source']}] {a['titre']}"
        + (f" — {a['resume'][:200]}" if a["resume"] else "")
        + f"\n  URL: {a['url']}"
        for a in envoi
    )
    prompt = PROMPT.format(
        categorie=label,
        zones=", ".join(config["zones_suivies"]),
        themes=" ; ".join(f"« {t[lang]} »" for t in config["themes"]),
        langue=L10N[lang]["langue"].upper(),
        nb=config["nb_sujets_par_onglet"],
        articles=liste,
    )
    derniere_erreur = None
    for modele in GEMINI_MODELES:
        for tentative in range(2):
            try:
                brut = appeler_gemini(prompt, cle, modele)
                sujets = json.loads(brut)
                if isinstance(sujets, list) and sujets:
                    return sujets
                derniere_erreur = "réponse vide ou non-liste"
            except urllib.error.HTTPError as e:
                derniere_erreur = e
                if e.code in (429, 404):  # quota épuisé / modèle retiré → modèle suivant
                    break
            except Exception as e:
                derniere_erreur = e
            time.sleep(8)
    print(f"  ⚠ Gemini a échoué pour « {label} » (tous les modèles) : {derniere_erreur}",
          file=sys.stderr)
    return None


# ── Génération HTML ──────────────────────────────────────────────────────────

CSS = """
:root { --fond:#fdeef4; --carte:#ffffff; --texte:#2a1e25; --sourdine:#96798a;
        --rose:#e0447c; --rose-doux:#fbdce9; --vert:#2e7d4f; --vert-doux:#e3f2e7;
        --bordure:#f3d2e1; }
@media (prefers-color-scheme: dark) {
  :root { --fond:#221219; --carte:#2e1c26; --texte:#f7e9f0; --sourdine:#b596a5;
          --rose:#ff7eb0; --rose-doux:#432234; --vert:#7fd191; --vert-doux:#20362a;
          --bordure:#43293a; } }
* { box-sizing:border-box; margin:0; }
body { background:var(--fond); color:var(--texte);
       font:16px/1.6 'Nunito',system-ui,sans-serif;
       max-width:780px; margin:0 auto; padding:1.4rem 1rem 4rem; }
h1, h3 { font-family:'Fraunces',Georgia,serif; }
header h1 { font-size:1.9rem; color:var(--rose); letter-spacing:.01em; }
header p { color:var(--sourdine); font-size:.9rem; margin-top:.2rem; }
nav { display:flex; gap:.45rem; margin:1.3rem 0; flex-wrap:wrap; }
nav button { border:1px solid var(--bordure); background:var(--carte); color:var(--texte);
      padding:.5rem 1rem; border-radius:999px; font-size:.85rem; font-weight:700;
      font-family:'Nunito',system-ui,sans-serif; cursor:pointer; }
nav button.actif { background:var(--rose); border-color:var(--rose); color:#fff; }
section.onglet { display:none; } section.onglet.actif { display:block; }
details.theme-bloc { margin:.8rem 0; }
details.theme-bloc > summary { list-style:none; cursor:pointer; display:flex;
        align-items:center; gap:.55rem; background:var(--carte);
        border:1px solid var(--bordure); border-radius:14px; padding:.7rem 1rem;
        font-weight:800; font-size:.85rem; text-transform:uppercase;
        letter-spacing:.1em; color:var(--vert); }
details.theme-bloc > summary::-webkit-details-marker { display:none; }
details.theme-bloc > summary::after { content:"+"; margin-left:auto; color:var(--rose);
        font-size:1.15rem; font-weight:800; }
details.theme-bloc[open] > summary { margin-bottom:.8rem; }
details.theme-bloc[open] > summary::after { content:"–"; }
.compte { background:var(--rose-doux); color:var(--rose); border-radius:999px;
        padding:.05rem .6rem; font-size:.75rem; font-weight:800; }
article { background:var(--carte); border:1px solid var(--bordure); border-radius:18px;
          margin-bottom:1.1rem; overflow:hidden;
          box-shadow:0 2px 10px rgba(224,68,124,.06); }
.pic { width:100%; height:190px; object-fit:cover; display:block;
       background:linear-gradient(120deg,var(--rose-doux),var(--vert-doux)); }
.ph { display:flex; align-items:center; justify-content:center; font-size:3rem;
      background:linear-gradient(120deg,var(--rose-doux),var(--vert-doux)); }
.corps { padding:1rem 1.15rem 1.1rem; }
.zone { display:inline-block; background:var(--vert-doux); color:var(--vert);
        font-size:.72rem; font-weight:700; text-transform:uppercase;
        letter-spacing:.08em; padding:.15rem .6rem; border-radius:999px; }
article h3 { font-size:1.12rem; margin:.45rem 0 .2rem; line-height:1.35; }
.registre { border-radius:12px; padding:.65rem .85rem; margin-top:.6rem; }
.registre .etiquette { font-size:.7rem; font-weight:800; text-transform:uppercase;
        letter-spacing:.08em; display:block; margin-bottom:.25rem; }
.clair { background:var(--vert-doux); } .clair .etiquette { color:var(--vert); }
.formel { background:var(--rose-doux); }
details.formel summary { font-size:.7rem; font-weight:800; text-transform:uppercase;
        letter-spacing:.08em; color:var(--rose); cursor:pointer; }
details.formel[open] summary { margin-bottom:.3rem; }
.sources { margin-top:.65rem; font-size:.8rem; }
.sources a { color:var(--sourdine); margin-right:.9rem; text-decoration-color:var(--rose); }
.brut { padding-left:1.1rem; } .brut li { margin-bottom:.55rem; }
.brut a { color:var(--texte); text-decoration-color:var(--rose); }
.brut .meta { color:var(--sourdine); font-size:.8rem; }
.note { background:var(--carte); border:1px dashed var(--bordure); border-radius:14px;
        padding:.8rem 1rem; color:var(--sourdine); font-size:.85rem; margin-bottom:1rem; }
footer { color:var(--sourdine); font-size:.78rem; margin-top:2rem; }
footer b { color:var(--rose); }
"""

JS = """
document.querySelectorAll('nav button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.remove('actif'));
  document.querySelectorAll('section.onglet').forEach(x => x.classList.remove('actif'));
  b.classList.add('actif');
  document.getElementById(b.dataset.cible).classList.add('actif');
}));
"""

# Icônes (docs/icon-*.png générées une fois, non écrasées par le script)
TETES_ICONES = """<link rel="icon" href="icon-180.png">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#2e7d4f">"""


def meta_article(a, lang):
    """« Le Monde · Aug 13, 09:49 » — plateforme + date de publication."""
    d = a.get("date")
    quand = f" · {d.astimezone().strftime(L10N[lang]['date_fmt'])}" if d else ""
    return f"{a['source']}{quand}"


def html_sujet(s, infos_par_url, lang, emoji_par_theme):
    urls = [u for u in (s.get("urls") or []) if isinstance(u, str) and u.startswith("http")]
    liens = []
    for u in urls[:2]:
        a = infos_par_url.get(u)
        texte = meta_article(a, lang) if a else ("source" if lang == "en" else "source")
        liens.append(f'<a href="{escape(u, quote=True)}" target="_blank" rel="noopener">'
                     f'{escape(texte)} ↗</a>')

    image = next((infos_par_url[u]["image"] for u in urls
                  if u in infos_par_url and infos_par_url[u].get("image")), None)
    if image:
        pic = (f'<img class="pic" src="{escape(image, quote=True)}" alt="" loading="lazy" '
               f'referrerpolicy="no-referrer" onerror="this.remove()">')
    else:
        emoji = emoji_par_theme.get(str(s.get("theme", "")), "🌍")
        pic = f'<div class="pic ph">{emoji}</div>'

    t = L10N[lang]
    return f"""<article>
{pic}
<div class="corps">
<span class="zone">{escape(str(s.get("zone", "")))}</span>
<h3>{escape(str(s.get("titre", "")))}</h3>
<div class="registre clair"><span class="etiquette">{t["clair"]}</span>{escape(str(s.get("en_clair", "")))}</div>
<details class="registre formel"><summary>{t["formel"]}</summary><p>{escape(str(s.get("formel", "")))}</p></details>
<div class="sources">{"".join(liens)}</div>
</div>
</article>"""


def html_sujets_par_theme(sujets, articles, config, lang):
    """Regroupe les sujets d'un onglet par thème, dans l'ordre défini en config."""
    noms = [t[lang] for t in config["themes"]]
    emoji_par_theme = {t[lang]: t["emoji"] for t in config["themes"]}
    infos_par_url = {a["url"]: a for a in articles}
    ordre = {n: i for i, n in enumerate(noms)}
    groupes = {}
    for s in sujets:
        groupes.setdefault(str(s.get("theme") or L10N[lang]["autres"]), []).append(s)
    corps = []
    for rang, theme in enumerate(sorted(groupes, key=lambda t: ordre.get(t, len(ordre)))):
        emoji = emoji_par_theme.get(theme, "🌍")
        ouvert = " open" if rang == 0 else ""
        corps.append(
            f'<details class="theme-bloc"{ouvert}><summary>{emoji} {escape(theme)}'
            f'<span class="compte">{len(groupes[theme])}</span></summary>'
        )
        corps.extend(html_sujet(s, infos_par_url, lang, emoji_par_theme) for s in groupes[theme])
        corps.append("</details>")
    return "".join(corps)


def html_brut(articles, lang):
    lignes = "".join(
        f'<li><a href="{escape(a["url"], quote=True)}" target="_blank" rel="noopener">{escape(a["titre"])}</a>'
        f'<div class="meta">{escape(meta_article(a, lang))}</div></li>'
        for a in articles[:30]
    )
    return f'<ul class="brut">{lignes}</ul>'


def construire_page(onglets, config, mode_brut, erreurs):
    date_en = datetime.now().strftime("%B %d, %Y at %H:%M")
    nav, sections = [], []
    for i, (ident, label, lang, sujets, articles) in enumerate(onglets):
        actif = " actif" if i == 0 else ""
        nav.append(f'<button class="{actif.strip()}" data-cible="{ident}">{escape(label)}</button>')
        if sujets:
            corps = html_sujets_par_theme(sujets, articles, config, lang)
        elif articles:
            corps = html_brut(articles, lang)
        else:
            corps = '<p class="note">No articles found for this category.</p>'
        sections.append(f'<section class="onglet{actif}" id="{ident}">{corps}</section>')

    note = ""
    if mode_brut:
        note = ('<p class="note">Raw mode: GEMINI_API_KEY missing — headlines only. '
                'Add the key to .env (local) or the GitHub secrets to enable summaries.</p>')
    note_err = ""
    if erreurs:
        note_err = f'<p class="note">Feeds with errors: {escape(", ".join(erreurs))}</p>'

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>News — {datetime.now().strftime("%b %d, %Y")}</title>
{TETES_ICONES}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Nunito:wght@400;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<header><h1>News 🌸</h1><p>Generated on {date_en}</p></header>
{note}
<nav>{"".join(nav)}</nav>
{"".join(sections)}
{note_err}
<footer><b>geo-veille</b> — RSS + Gemini Flash · two registers: plain / formal ♡</footer>
<script>{JS}</script>
</body>
</html>"""
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(page, encoding="utf-8")


# ── Cache (pour retravailler le HTML sans rappeler Gemini) ───────────────────

def sauvegarder_cache(onglets, mode_brut, erreurs):
    data = {
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "mode_brut": mode_brut,
        "erreurs": erreurs,
        "onglets": [
            {"ident": ident, "label": label, "lang": lang, "sujets": sujets,
             "articles": [{**a, "date": a["date"].isoformat() if a["date"] else None}
                          for a in articles]}
            for ident, label, lang, sujets, articles in onglets
        ],
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def charger_cache():
    data = json.loads(CACHE.read_text())
    onglets = [
        (o["ident"], o["label"], o.get("lang", "en"), o["sujets"],
         [{**a, "date": datetime.fromisoformat(a["date"]) if a["date"] else None}
          for a in o["articles"]])
        for o in data["onglets"]
    ]
    return onglets, data["mode_brut"], data["erreurs"]


# ── Orchestration ────────────────────────────────────────────────────────────

def main():
    charger_env()
    config = json.loads((RACINE / "config.json").read_text())

    if "--html-only" in sys.argv:  # reconstruit la page depuis le dernier digest
        onglets, mode_brut, erreurs = charger_cache()
        construire_page(onglets, config, mode_brut, erreurs)
        print(f"✓ Page reconstruite depuis le cache : {SORTIE}")
        return

    cle = os.environ.get("GEMINI_API_KEY", "").strip()

    categories = [
        ("fr", config["flux"]["medias_fr"]["label"], "fr",
         config["flux"]["medias_fr"]["feeds"], config["fenetre_heures"]),
        ("en", config["flux"]["medias_en"]["label"], "en",
         config["flux"]["medias_en"]["feeds"], config["fenetre_heures"]),
        ("zones", "By region", "en",
         feeds_google_news(config["requetes_google_news"]), config["fenetre_heures"]),
        ("thinktanks", config["flux"]["think_tanks"]["label"], "en",
         config["flux"]["think_tanks"]["feeds"], config["fenetre_heures_think_tanks"]),
    ]

    onglets, toutes_erreurs = [], []
    for ident, label, lang, feeds, fenetre in categories:
        print(f"→ {label} : récupération de {len(feeds)} flux…")
        articles, erreurs = recuperer_categorie(feeds, fenetre)
        toutes_erreurs.extend(erreurs)
        print(f"  {len(articles)} articles dans la fenêtre de {fenetre}h")
        sujets = digest_categorie(label, articles, config, cle, lang)
        if sujets:
            print(f"  ✓ {len(sujets)} sujets résumés en double-registre ({lang})")
        onglets.append((ident, label, lang, sujets, articles))

    # Si Gemini a échoué pour un onglet, garde le digest précédent plutôt que rien
    if CACHE.exists() and any(o[3] is None for o in onglets):
        try:
            precedents = {o[0]: o for o in charger_cache()[0]}
        except Exception:
            precedents = {}
        repares = []
        for ident, label, lang, sujets, articles in onglets:
            anc = precedents.get(ident)
            if sujets is None and anc and anc[3]:
                repares.append((ident, label, lang, anc[3], anc[4]))
                print(f"  ↻ {label} : reprise du digest précédent (échec Gemini)")
            else:
                repares.append((ident, label, lang, sujets, articles))
        onglets = repares

    sauvegarder_cache(onglets, mode_brut=not cle, erreurs=toutes_erreurs)
    construire_page(onglets, config, mode_brut=not cle, erreurs=toutes_erreurs)
    print(f"\n✓ Page générée : {SORTIE}")
    if not cle:
        print("  (mode brut : pas de GEMINI_API_KEY — ajoute-la dans .env pour les résumés)")


if __name__ == "__main__":
    main()

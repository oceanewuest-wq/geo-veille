# geo-veille 🌍

Veille géopolitique quotidienne, automatisée et 100 % gratuite.
Chaque matin, un digest en **double-registre** (version formelle + version « en clair »)
sur les zones suivies, généré depuis des flux RSS et résumé par Gemini Flash (free tier).

## Comment ça marche

```
Flux RSS (Le Monde, BBC, Al Jazeera, Google News par zone, think tanks…)
   → veille.py (Python, zéro dépendance)
   → Gemini 2.5 Flash sélectionne les sujets et rédige les deux registres
   → docs/index.html (page à onglets : Médias FR / Médias EN / Par zone / Think tanks)
   → publiée sur GitHub Pages par GitHub Actions, tous les matins
```

## Tester en local

```bash
python3 veille.py
open docs/index.html
```

Sans clé Gemini, le script tourne en **mode brut** (liste de titres, pas de résumés).

Pour activer le double-registre :
1. Récupère une clé gratuite sur https://aistudio.google.com (« Get API key »)
2. Crée un fichier `.env` à la racine :
   ```
   GEMINI_API_KEY=ta_clé_ici
   ```
3. Relance `python3 veille.py`

## Déployer (une seule fois)

1. Crée un repo GitHub `geo-veille`, pousse ce dossier dessus
2. Dans le repo : **Settings → Secrets and variables → Actions → New repository secret**
   → nom `GEMINI_API_KEY`, valeur = ta clé
3. **Settings → Pages** → Source : *Deploy from a branch* → branche `main`, dossier `/docs`
4. C'est tout. Le workflow tourne chaque matin (~7 h Paris) et la page est sur
   `https://TON_USER.github.io/geo-veille/`
   (bouton **Actions → Digest quotidien → Run workflow** pour lancer à la main)

## Personnaliser

Tout se règle dans [config.json](config.json) :
- `zones_suivies` — les zones prioritaires données au LLM
- `requetes_google_news` — les requêtes de l'onglet « Par zone »
- `flux` — ajouter/retirer des flux RSS dans chaque onglet
- `nb_sujets_par_onglet`, `fenetre_heures` — volume et fraîcheur

L'heure du digest se règle dans [.github/workflows/digest.yml](.github/workflows/digest.yml)
(cron en UTC : `0 5 * * *` = 7 h à Paris l'été).

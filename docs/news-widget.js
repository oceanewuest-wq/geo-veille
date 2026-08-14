// News — widget iOS pour Scriptable
// Affiche les sujets du jour depuis https://oceanewuest-wq.github.io/geo-veille/
// Installation : placer ce fichier dans Scriptable, puis ajouter un widget
// « Scriptable » à l'écran d'accueil et choisir ce script.

const SOURCE = "https://oceanewuest-wq.github.io/geo-veille/widget.json";

const ROSE = new Color("#e0447c");
const VERT = new Color("#2e7d4f");
const TEXTE = new Color("#2a1e25");
const SOURDINE = new Color("#96798a");

// Nombre de sujets affichés selon la taille du widget
const COMBIEN = { small: 2, medium: 3, large: 7, extraLarge: 7 };

async function chargerDonnees() {
  const req = new Request(SOURCE + "?v=" + Date.now());
  req.timeoutInterval = 15;
  return await req.loadJSON();
}

function fondDegrade(widget) {
  const g = new LinearGradient();
  g.colors = [new Color("#fdeef4"), new Color("#e3f2e7")];
  g.locations = [0, 1];
  g.startPoint = new Point(0, 0);
  g.endPoint = new Point(1, 1);
  widget.backgroundGradient = g;
}

function enTete(widget, data, petit) {
  const ligne = widget.addStack();
  ligne.centerAlignContent();

  const titre = ligne.addText("News");
  titre.font = Font.boldSystemFont(petit ? 14 : 16);
  titre.textColor = ROSE;

  ligne.addSpacer();

  // Heure de génération du digest, en heure locale
  if (data.genere_le) {
    const d = new Date(data.genere_le);
    const f = new DateFormatter();
    f.dateFormat = "HH:mm";
    const heure = ligne.addText(f.string(d));
    heure.font = Font.mediumSystemFont(petit ? 10 : 11);
    heure.textColor = SOURDINE;
  }
  widget.addSpacer(petit ? 6 : 8);
}

function ajouterSujet(widget, sujet, petit) {
  const bloc = widget.addStack();
  bloc.layoutHorizontally();
  bloc.topAlignContent();

  const emoji = bloc.addText(sujet.emoji || "🌍");
  emoji.font = Font.systemFont(petit ? 11 : 13);
  bloc.addSpacer(5);

  const colonne = bloc.addStack();
  colonne.layoutVertically();

  const titre = colonne.addText(sujet.titre || "");
  titre.font = Font.semiboldSystemFont(petit ? 11 : 13);
  titre.textColor = TEXTE;
  titre.lineLimit = petit ? 2 : 2;
  titre.minimumScaleFactor = 0.85;

  // Sur les grands widgets, une ligne de contexte sous le titre
  if (!petit && sujet.zone) {
    const zone = colonne.addText(sujet.zone);
    zone.font = Font.mediumSystemFont(10);
    zone.textColor = VERT;
    zone.lineLimit = 1;
  }

  widget.addSpacer(petit ? 5 : 7);
}

function widgetErreur(message) {
  const w = new ListWidget();
  fondDegrade(w);
  w.setPadding(14, 14, 14, 14);
  const t = w.addText("News");
  t.font = Font.boldSystemFont(15);
  t.textColor = ROSE;
  w.addSpacer(6);
  const m = w.addText(message);
  m.font = Font.systemFont(11);
  m.textColor = SOURDINE;
  m.lineLimit = 3;
  return w;
}

async function construire() {
  const famille = config.widgetFamily || "medium";
  const petit = famille === "small";

  let data;
  try {
    data = await chargerDonnees();
  } catch (e) {
    return widgetErreur("Digest indisponible — vérifie ta connexion.");
  }

  const sujets = (data.sujets || []).slice(0, COMBIEN[famille] || 3);
  if (sujets.length === 0) return widgetErreur("Aucun sujet pour le moment.");

  const w = new ListWidget();
  fondDegrade(w);
  w.setPadding(petit ? 12 : 15, petit ? 12 : 15, petit ? 10 : 12, petit ? 12 : 15);
  w.url = data.url; // tap → ouvre la page complète

  enTete(w, data, petit);
  for (const s of sujets) ajouterSujet(w, s, petit);
  w.addSpacer();

  // Indice de rafraîchissement (iOS reste maître de la fréquence réelle)
  w.refreshAfterDate = new Date(Date.now() + 60 * 60 * 1000);
  return w;
}

const widget = await construire();

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // Aperçu quand on lance le script depuis l'app Scriptable
  await widget.presentMedium();
}
Script.complete();

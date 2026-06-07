# 🔄 PROMPT REFONTE MAJEURE - BOT DISCORD FT-CHAMPIONSHIP

**Destinataire :** Assistant IA (Claude, GPT, etc.)  
**Statut :** À implémenter  
**Priorité :** Haute  
**Date du prompt :** 07 Juin 2026  
**Projet :** bot-onpg-discord  
**Repository :** https://github.com/CG242/bot-onpg-discord

---

## 📋 CONTEXTE

Ce projet est un **bot Discord pour gérer un championnat FT (Fighting Tournament)**.

Le bot enregistre automatiquement les scores, calcule les classements, gère les rangs et les statistiques des joueurs.

**État actuel :** Fonctionnel mais avec limitations architecturales et systèmes de calcul basiques.

**Objectif :** Effectuer une mise à jour majeure pour améliorer :
- La gestion des saisons
- La détection de doublons
- Le système ELO compétitif
- La hiérarchie des rangs
- Le classement pertinent
- La documentation utilisateur

---

## 🎯 MODIFICATIONS DEMANDÉES

### 1️⃣ RÉINITIALISATION DE SAISON (Reset Championship)

#### Objectif
Créer une commande administrateur permettant de démarrer une nouvelle saison.

#### Contraintes inviolables
- ✓ NE PAS supprimer les messages Discord
- ✓ NE PAS supprimer les salons Discord
- ✓ NE PAS supprimer les utilisateurs Discord
- ✓ NE PAS supprimer la configuration du bot

#### Action lors du reset
```
Tous les matchs → ignorés ou archivés
Tous les calculs → réinitialisés
Toutes les stats → réinitialisées
Toutes les confrontations → réinitialisées
Tous les ELO → réinitialisés
```

#### Données conservées
```
✓ Les joueurs
✓ Les pseudos
✓ Les liens Discord ↔ joueur
✓ Les régions
✓ Les rangs attribués manuellement (admin)
```

#### Comportement après reset
```
• Bot ignore les anciens matchs
• Seuls les nouveaux matchs sont pris en compte
• Classement vide jusqu'à nouveaux matchs
```

#### Architecture proposée
**Gestion réelle de saisons :**

```
Saison 1 : 01/01/2026 → 31/03/2026 (COMPLÈTE)
Saison 2 : 01/04/2026 → 30/06/2026 (ACTIVE)
Saison 3 : 01/07/2026 → ? (FUTURE)
```

**Permet :**
- Consulter les anciennes saisons
- Comparer les performances entre saisons
- Archiver l'historique proprement

#### Commandes à créer

```
/nouvelle-saison (Admin)
  Paramètres :
    - nom (requis) : "Saison 2" ou "Saison d'été 2026"
    - date_debut (optionnel) : AAAA-MM-JJ
    - description (optionnel) : texte
  
  Comportement :
    • Crée une nouvelle saison
    • Archive l'ancienne (is_active=0)
    • Réinitialise les calculs
    • Conserve tous les joueurs
    • Affiche confirmation
```

---

### 2️⃣ SUPPRESSION INTELLIGENTE DES DOUBLONS DE JOUEURS

#### Problème actuel
```
David_MK
David MK
David
DavidMk
david mk
DAVID_MK

↓ Considérés comme 6 joueurs différents
↓ Les stats sont dispersées
```

#### Solution proposée

**Système intelligent de normalisation :**

```
Ignorer :
  • Les espaces
  • Les tirets
  • Les underscores
  • La casse (majuscules/minuscules)
  • Les caractères spéciaux courants

Exemple :
  David_MK    → davidmk (normalisé)
  David MK    → davidmk (normalisé)
  DAVID-MK    → davidmk (normalisé)
  david-Mk    → davidmk (normalisé)

Résultat : Tous pointent vers le MÊME joueur
```

**Fuzzy matching :**

```
Si un nouveau pseudo ne correspond pas exactement,
utiliser la similarité Levenshtein pour suggérer :

"Ce joueur ressemble fortement à 'David_MK'.
Voulez-vous les fusionner ?"

Menu interactif pour confirmer
```

#### Commandes à créer

```
/fusion-joueurs (Admin)
  Paramètres :
    - joueur_source (requis) : Pseudo à fusionner
    - joueur_cible (requis) : Pseudo destination
  
  Comportement :
    • Fusionne les deux profils
    • Transfère tous les matchs à la cible
    • Recalcule les stats
    • Supprime le doublon
    • Affiche résumé de la fusion

/deduplication-auto (Admin)
  Paramètres : Aucun
  
  Comportement :
    • Scan tous les joueurs
    • Détecte les probabilités de doublons (>95% similarité)
    • Affiche liste de suggestions
    • Propose fusion automatique
    • Log les actions
```

#### Implémentation technique

**Fonction de normalisation :**

```python
def normalize_name_strict(name: str) -> str:
    """
    Normalisation très stricte pour déduplication.
    
    David_MK → davidmk
    D'av-id MK → davidmk
    """
    # Minuscules
    normalized = name.lower()
    
    # Supprimer espaces, tirets, underscores
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("-", "")
    normalized = normalized.replace("_", "")
    
    # Supprimer caractères spéciaux (garder alphanum)
    normalized = "".join(c for c in normalized if c.isalnum())
    
    return normalized
```

**Détection de similarité :**

```python
from difflib import SequenceMatcher

def similarity_ratio(s1: str, s2: str) -> float:
    """Ratio de similarité entre 0 et 1"""
    return SequenceMatcher(None, s1, s2).ratio()

# Si ratio > 0.95 → suggestion de fusion
```

---

### 3️⃣ REFONTE COMPLÈTE DU SYSTÈME ELO

#### Problème actuel
```
Victoire = +25 + bonus
Défaite = -15 - bonus

Ce n'est pas réaliste. Les points gagnés ne dépendent
pas du niveau de l'adversaire.
```

#### Système proposé : ELO COMPÉTITIF

**Principe :**

```
Les points gagnés/perdus dépendent de :
  1. ELO de l'adversaire
  2. Écart de niveau
  3. Rang attribué
  4. Type de match (FT)
```

#### Formules mathématiques

**Probabilité de victoire attendue (Expectancy) :**

```
E_A = 1 / (1 + 10^((ELO_B - ELO_A) / 400))

où :
  E_A = probabilité de victoire attendue du joueur A
  ELO_A = ELO du joueur A
  ELO_B = ELO du joueur B
```

**Delta ELO après match :**

```
Δ_ELO = K × (Résultat - E_A)

où :
  K = facteur K (dépend du niveau)
  Résultat = 1 (victoire) ou 0 (défaite)
  E_A = probabilité attendue
```

#### Facteur K (variable selon le niveau)

```
Rang S+  : K = 16
Rang S   : K = 20
Rang A+  : K = 24
Rang A   : K = 28
Rang B+  : K = 32
Rang B   : K = 40
Rang C   : K = 48
Rang NR  : K = 50
```

Plus le joueur est faible, plus K est élevé (progression rapide).

#### Bonus par type FT

```
FT2  : ×0.5
FT3  : ×0.75
FT5  : ×1.0  (référence)
FT7  : ×1.25
FT10 : ×1.5
```

#### Exemples concrets

**Exemple 1 : S+ vs S+ (équivalent)**

```
ELO_A = 2400, ELO_B = 2400

E_A = 1 / (1 + 10^((2400-2400)/400)) = 0.5

Victoire :  16 × (1 - 0.5) = +8 points
Défaite  :  16 × (0 - 0.5) = -8 points

→ Gains/pertes faibles (équitable)
```

**Exemple 2 : S+ vs B (écart majeur)**

```
ELO_S+ = 2400, ELO_B = 1400

E_S+ = 1 / (1 + 10^((1400-2400)/400)) ≈ 0.999 (quasi-certain)

S+ victoire :  16 × (1 - 0.999) = +0.16 points (négligeable)
S+ défaite  :  16 × (0 - 0.999) = -15.98 points (grosse perte)

B victoire  :  40 × (1 - 0.001) = +40 points (énorme)
B défaite   :  40 × (0 - 0.001) = -0.04 points (négligeable)

→ Récompense les exploits, pénalise le farming
```

**Exemple 3 : Progression rapide (rang NR)**

```
ELO_NR = 1000, K = 50

Défaites contre S+ :  50 × (0 - 0.99) = -49 points
Victoire contre rival :  50 × (1 - 0.5) = +25 points

→ Les nouveaux joueurs progressent rapidement
```

#### Montées et descentes de rang

**Montée automatique :**

```
Quand ELO > seuil_supérieur → +1 rang
Seuil_supérieur = ELO_base_rang + 200

Exemple : S+ base = 2400
  Montée quand ELO > 2600
  (Mais S+ est max, donc pas de montée)
```

**Descente automatique :**

```
Quand ELO < seuil_inférieur → -1 rang
Seuil_inférieur = ELO_base_rang - 200

Exemple : S base = 2200
  Descente quand ELO < 2000
  (Descend en A+)
```

**Verrouillage admin :**

```
rank_manual = 1 → Le rang ne descend pas automatiquement
rank_manual = 0 → Le rang monte/descend librement
```

---

### 4️⃣ REFONTE DES RANGS

#### Hiérarchie proposée

```
NR (Non Classé) → ELO = 1000
C               → ELO = 1200
B               → ELO = 1400
B+              → ELO = 1600
A               → ELO = 1800
A+              → ELO = 2000
S               → ELO = 2200
S+              → ELO = 2400
```

#### Seuils de montée/descente

```
Chaque rang :
  Seuil_min (descente) = ELO_base - 200
  Seuil_max (montée) = ELO_base + 200

Exemple (rang B) :
  Descente si ELO < 1200 (passe à C)
  Montée si ELO > 1600 (passe à B+)
```

#### Initialisation d'un joueur

```
Nouveau joueur → ELO = 1000 (NR)
Admin attribue rang → ELO = ELO_base du rang
rank_manual = 1 (fixé jusqu'à changement admin)
```

#### Affichage du rang

```
/stats pseudo:David_MK

Affiche :
  Rang actuel : S+
  ELO : 2480
  Seuil descente : 2200
  Seuil montée : N/A (max)
  Statut : Verrouillé par admin
```

---

### 5️⃣ REFONTE DU CLASSEMENT

#### Problème actuel
```
Classement basé uniquement sur :
  1. Victoires (DESC)
  2. Défaites (ASC)
  3. Ratio points
  4. ELO

Pas assez représentatif de la force réelle.
```

#### Critères du nouveau classement

**Tri par importance :**

```
1. ELO (DESC)
   → Reflet direct du niveau compétitif

2. Taux de victoire (DESC)
   → Cohérence des performances
   → Minimum 5 matchs pour compter

3. Activité récente (DESC)
   → Bonus si match < 7 jours
   → Encourage participation

4. Nombre de matchs (DESC)
   → Tiebreaker : qui a joué plus

5. Pseudo (ASC)
   → Alphabétique pour déterminisme
```

#### Formule de score de classement

```
SCORE_CLASSEMENT = (
    ELO × 0.5 +
    (taux_victoire × 100) × 0.3 +
    (bonus_activité) × 0.15 +
    (min(matches, 50) / 50) × 0.05
)

Tri final par SCORE_CLASSEMENT DESC
```

#### Bonus activité

```
Match < 7 jours   : +50 points
Match < 14 jours  : +25 points
Match < 30 jours  : +10 points
Match > 30 jours  : 0 points (inactif)
```

#### Affichage du classement

```
**TOP 1 — David_MK**
Rang : S+
ELO : 2480
Taux de victoire : 85% (34/40 matchs)
Activité : 3 jours
Points : 2500 (score classement)
```

---

### 6️⃣ REFONTE COMPLÈTE DE /AIDE

#### Objectif
Créer un guide utilisateur clair et exhaustif.

#### Contenu proposé

```
L'utilisateur doit comprendre immédiatement :
  • Comment utiliser chaque commande
  • Qu'est-ce que chaque stat signifie
  • Comment progresser
  • Comment jouent les autres
```

#### Structure de /aide

```
PAGES (avec système de pagination) :

Page 1 : Vue d'ensemble
  - Qu'est-ce que le bot ?
  - Comment ça marche ?
  - Quoi de neuf ?

Page 2 : Les commandes de base
  /classement
  /stats
  /compare
  /set-region

Page 3 : Explications détaillées
  - ELO et rangs
  - Taux de victoire
  - Activité récente
  - Matchs récents

Page 4 : Commandes admin
  /nouvelle-saison
  /set-rang
  /region-definir
  /fusion-joueurs

Page 5 : FAQ
  "Pourquoi j'ai perdu des points ?"
  "Comment monter de rang ?"
  "Peut-on voir une ancienne saison ?"
```

#### Exemples concrets

```
COMMANDE : /stats pseudo:David_MK

Description :
  Affiche les statistiques COMPLÈTES d'un joueur.

Qu'on y voit :
  ✓ Rang actuel et ELO
  ✓ Victoires / Défaites
  ✓ Taux de victoire (%)
  ✓ Nombre total de matchs
  ✓ Derniers matchs (date, adversaire, score)
  ✓ Stats par type FT

Exemple réel :
  /stats pseudo:David_MK
  
  → Affiche :
    Rang : S+
    ELO : 2480
    Victoires : 34
    Défaites : 6
    Taux : 85%
    Matchs : 40
    Derniers matchs :
      [3j] David_MK 5-2 Toto (FT5) ✓ Victoire
      [5j] David_MK 3-5 Admin (FT5) ✗ Défaite
      ...
```

#### Implémentation

**Utiliser Discord Embeds avec pagination :**

```python
async def help_command(interaction):
    pages = [
        create_page_overview(),     # Page 1
        create_page_commands(),     # Page 2
        create_page_explanations(), # Page 3
        create_page_admin(),        # Page 4
        create_page_faq(),          # Page 5
    ]
    
    # Afficher page 1 + boutons Previous/Next
```

---

### 7️⃣ GESTION DES SAISONS

#### Structure de données

```
TABLE : seasons (MODIFIÉE)
  - id INT PK
  - name VARCHAR(100)
  - description VARCHAR(255)
  - start_date DATE
  - end_date DATE (nouveau)
  - is_active TINYINT(1)
  - champion_id INT FK (nouveau)
  - created_at TIMESTAMP
  - updated_at TIMESTAMP (nouveau)
```

#### Nouveaux champs dans matches

```
TABLE : matches (MODIFIÉE)
  - season_id INT FK (déjà présent)
  - ... (autres champs inchangés)

Permet filtrer par saison.
```

#### Commandes

```
/nouvelle-saison (Admin)
  - Crée une nouvelle saison
  - Archive l'ancienne
  - Initialise le comptage

/terminer-saison (Admin)
  - Marque une saison comme complète
  - Couronne un champion
  - Archivage définitif

/classement saison:2 (Tous)
  - Affiche classement d'une saison spécifique
  - Peut consulter l'historique

/stats saison:1 pseudo:David_MK (Tous)
  - Stats d'un joueur dans une saison donnée
  - Comparaison multi-saisons

/compare saison:3 joueur_a:David joueur_b:Admin (Tous)
  - Confrontations dans une saison spécifique
```

#### Affichage des saisons

```
/saisons (Tous)
  Affiche :
    Saison 1 : [COMPLÈTE] 01/01/2026 → 31/03/2026
      Champion : David_MK
      Joueurs : 42
      Matchs : 187

    Saison 2 : [ACTIVE] 01/04/2026 → ?
      Joueurs : 48
      Matchs : 52

    Saison 3 : [PLANIFIÉE] 01/07/2026 → ?
```

---

### 8️⃣ MIGRATION ET QUALITÉ DU CODE

#### Modifications de fichiers

**database.py :**
```
✓ Ajouter migrations pour new schema
✓ Créer fonctions de gestion saisons
✓ Implémenter nouveau système ELO
✓ Ajouter déduplication fuzzy matching
✓ Optimiser requêtes de classement
✓ Ajouter archivage de saison
```

**ranking.py :**
```
✓ Implémenter calcul ELO compétitif (formule attendue)
✓ Facteur K variable selon rang
✓ Bonus FT
✓ Montée/descente automatique
✓ Histogramme de progression
```

**commands.py :**
```
✓ /nouvelle-saison (admin)
✓ /terminer-saison (admin)
✓ /fusion-joueurs (admin)
✓ /deduplication-auto (admin)
✓ /saisons (tous)
✓ /classement saison:X (paramètre optionnel)
✓ /stats saison:X (paramètre optionnel)
✓ /compare saison:X (paramètre optionnel)
✓ Refonte /aide (pagination)
```

**stats.py :**
```
✓ Nouveau calcul de classement (score multi-critère)
✓ Formatage amélioré du classement
✓ Affichage détaillé de l'ELO
✓ Stats multi-saisons
✓ Historique de progression
```

**bot.py :**
```
✓ Gestion des saisons au démarrage
✓ Vérification saison active
✓ Migration des données si nécessaire
```

**player_resolver.py :**
```
✓ Fuzzy matching amélioré
✓ Normalisation stricte
✓ Détection de similarité
✓ Suggestions de fusion
```

**config.py :**
```
✓ Ajouter constantes de rangs
✓ Ajouter seuils ELO
✓ Ajouter facteurs K
```

#### Migrations SQL

```
ALTER TABLE seasons ADD COLUMN end_date DATE NULL;
ALTER TABLE seasons ADD COLUMN champion_id INT NULL;
ALTER TABLE seasons ADD COLUMN updated_at TIMESTAMP;

CREATE TABLE deduplicaton_history (
  id INT PK,
  source_player_id INT FK,
  target_player_id INT FK,
  performed_by INT FK,
  created_at TIMESTAMP
);

ALTER TABLE players ADD COLUMN merged_into_id INT NULL FK;
```

#### Standards de code

```
✓ Python 3.11+ compatible
✓ Type hints complètes
✓ Docstrings en français
✓ Logging exhaustif
✓ Gestion d'erreurs propre
✓ MySQL standard
✓ Discord.py 2.x compatible
```

#### Tests

```
✓ Tester fusion de joueurs
✓ Tester calcul ELO avec plusieurs scénarios
✓ Tester transition de saison
✓ Tester migration données
✓ Tester commandes admin
✓ Vérifier intégrité données
```

---

## 📊 DOCUMENTATION REQUISE

### Mise à jour de DOCUMENTATION_COMPLETE.txt

Ajouter sections :
- Saisons et transitions
- Système ELO compétitif (formules)
- Rangs et seuils
- Déduplication de joueurs
- Nouveau classement (scoring)
- Guide utilisateur détaillé

### Créer CHANGELOG.md

```
## Version 2.0 (Refonte majeure)

### Nouvelles fonctionnalités
- Système de gestion de saisons
- ELO compétitif adaptatif
- Déduplication intelligente
- Classement multi-critères
- Guide utilisateur amélioré

### Modifications
- Hiérarchie de rangs revue
- Système de bonus ELO par type FT
- Montée/descente automatique de rang
- Nouvelles commandes admin

### Migrations
- Schéma BD mise à jour
- Données existantes conservées
```

---

## 🎬 PLAN D'IMPLÉMENTATION

### Phase 1 : Préparation (1-2 jours)
```
□ Analyser l'architecture existante
□ Préparer les migrations SQL
□ Créer branches Git
□ Documenter l'approche
```

### Phase 2 : Fondations (2-3 jours)
```
□ Migrations de schéma
□ Nouveau système ELO (ranking.py)
□ Gestion de saisons (database.py)
□ Déduplication (player_resolver.py)
```

### Phase 3 : Commandes (2-3 jours)
```
□ /nouvelle-saison
□ /fusion-joueurs
□ /deduplication-auto
□ Variantes de /stats, /classement, /compare
□ /aide refonte
```

### Phase 4 : Tests & Docs (1-2 jours)
```
□ Tests fonctionnels
□ Documenter entièrement
□ Vérifier intégrité
□ Préparer la release
```

---

## ✅ CRITÈRES DE SUCCÈS

```
✓ Saisons gérées correctement
✓ Aucune donnée perdue lors du reset
✓ ELO compétitif fonctionnel
✓ Doublons détectés et fusionnables
✓ Rangs progressent automatiquement
✓ Classement reflète le vrai niveau
✓ /aide clair et complet
✓ Code propre et documenté
✓ Tous les tests passent
✓ Migration sans erreur
```

---

## 📞 NOTES IMPORTANTES

1. **Rétrocompatibilité :** Conserver les anciennes données, les migrer
2. **Progressivité :** Implémenter étape par étape, tester à chaque étape
3. **Logs :** Tous les changements doivent être loggés pour audit
4. **Sauvegardes :** Backup BD avant migration
5. **Communication :** Informer les utilisateurs de la transition

---

**FIN DU PROMPT**

---

Ce prompt peut être envoyé tel quel à Claude ou à un autre assistant de développement.
Il contient tous les détails nécessaires pour une implémentation complète et cohérente.


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

#### Commandes à créer
```
/nouvelle-saison (Admin)
  Paramètres :
    - nom (requis) : "Saison 2" ou "Saison d'été 2026"
    - date_debut (optionnel) : AAAA-MM-JJ
    - description (optionnel) : texte
```

### 2️⃣ SUPPRESSION INTELLIGENTE DES DOUBLONS DE JOUEURS

#### Système de normalisation
```
Ignorer :
  • Les espaces
  • Les tirets
  • Les underscores
  • La casse
  • Les caractères spéciaux courants

Exemple :
  David_MK = David MK = DAVID-MK = davidmk (tous identiques)
```

#### Commandes à créer
```
/fusion-joueurs (Admin)
  - Fusionne deux profils
  - Transfère tous les matchs
  - Supprime le doublon

/deduplication-auto (Admin)
  - Scan tous les joueurs
  - Détecte similarités >95%
  - Propose fusions
```

### 3️⃣ REFONTE COMPLÈTE DU SYSTÈME ELO

#### Système proposé : ELO COMPÉTITIF

**Formules :**

Probabilité de victoire attendue :
```
E_A = 1 / (1 + 10^((ELO_B - ELO_A) / 400))
```

Delta ELO :
```
Δ_ELO = K × (Résultat - E_A)
```

**Facteur K par rang :**
```
S+  : K = 16
S   : K = 20
A+  : K = 24
A   : K = 28
B+  : K = 32
B   : K = 40
C   : K = 48
NR  : K = 50
```

**Bonus par type FT :**
```
FT2  : ×0.5
FT3  : ×0.75
FT5  : ×1.0
FT7  : ×1.25
FT10 : ×1.5
```

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

#### Seuils montée/descente
```
Seuil_min (descente) = ELO_base - 200
Seuil_max (montée) = ELO_base + 200
```

### 5️⃣ REFONTE DU CLASSEMENT

#### Nouveaux critères de tri
```
1. ELO (DESC)
2. Taux de victoire (DESC)
3. Activité récente (DESC)
4. Nombre de matchs (DESC)
5. Pseudo (ASC)
```

#### Formule de score
```
SCORE = (ELO × 0.5) + (taux % × 0.3) + (bonus_activité × 0.15) + (matchs × 0.05)
```

### 6️⃣ REFONTE DE /AIDE

#### Pages proposées
```
Page 1 : Vue d'ensemble
Page 2 : Commandes de base (/classement, /stats, /compare)
Page 3 : Explications (ELO, taux victoire, activité)
Page 4 : Commandes admin
Page 5 : FAQ
```

### 7️⃣ GESTION DES SAISONS

#### Commandes
```
/nouvelle-saison (Admin)
/terminer-saison (Admin)
/saisons (Tous)
/classement saison:2 (Tous)
/stats saison:1 pseudo:X (Tous)
/compare saison:3 joueur_a:X joueur_b:Y (Tous)
```

### 8️⃣ MIGRATION ET QUALITÉ DU CODE

#### Fichiers à modifier
```
database.py       → Migrations, gestion saisons, ELO
ranking.py        → Système ELO compétitif
commands.py       → Nouvelles commandes
stats.py          → Nouveau classement
bot.py            → Gestion saisons
player_resolver.py→ Fuzzy matching
config.py         → Constantes
```

#### Migrations SQL
```
ALTER TABLE seasons ADD COLUMN end_date DATE NULL;
ALTER TABLE seasons ADD COLUMN champion_id INT NULL;
ALTER TABLE seasons ADD COLUMN updated_at TIMESTAMP;

CREATE TABLE deduplication_history (
  id INT PK,
  source_player_id INT FK,
  target_player_id INT FK,
  performed_by INT FK,
  created_at TIMESTAMP
);

ALTER TABLE players ADD COLUMN merged_into_id INT NULL FK;
```

---

## 📊 DOCUMENTATION REQUISE

Mettre à jour DOCUMENTATION_COMPLETE.txt et créer CHANGELOG.md

---

## 🎬 PLAN D'IMPLÉMENTATION

```
Phase 1 : Préparation (1-2 jours)
Phase 2 : Fondations (2-3 jours)
Phase 3 : Commandes (2-3 jours)
Phase 4 : Tests & Docs (1-2 jours)
```

---

**Ce prompt est prêt pour implémentation complète et cohérente.**

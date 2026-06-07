-- ============================================================================
-- MIGRATION SQL - Bot FT-Championship Refonte Majeure
-- Date: 07 Juin 2026
-- Version: 2.0
-- ============================================================================
-- 
-- Ce script applique toutes les migrations Phase 1-3 de la refonte majeure.
-- IMPORTANT: Effectuer un BACKUP complet avant d'exécuter ce script !
--
-- Exécution recommandée:
--   mysql -u root -p ft_championship < migration_v2.0.sql
--
-- ============================================================================

-- ============================================================================
-- ÉTAPE 1: MIGRATIONS COLONNES PLAYERS
-- ============================================================================

ALTER TABLE players ADD COLUMN IF NOT EXISTS region VARCHAR(2) NULL DEFAULT NULL;
ALTER TABLE players ADD COLUMN IF NOT EXISTS tier_rank VARCHAR(5) NOT NULL DEFAULT 'NR';
ALTER TABLE players ADD COLUMN IF NOT EXISTS elo INT NOT NULL DEFAULT 1000;
ALTER TABLE players ADD COLUMN IF NOT EXISTS rank_manual TINYINT(1) NOT NULL DEFAULT 0;

-- Contrainte unique sur normalized_name
ALTER TABLE players ADD UNIQUE KEY IF NOT EXISTS unique_normalized_name (normalized_name);

-- ============================================================================
-- ÉTAPE 2: MIGRATIONS COLONNES SEASONS (Phase 1 Tâche 2)
-- ============================================================================

ALTER TABLE seasons ADD COLUMN IF NOT EXISTS end_date DATE NULL DEFAULT NULL;
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS champion_id INT NULL DEFAULT NULL;
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;

-- Clé étrangère pour champion_id
ALTER TABLE seasons ADD CONSTRAINT IF NOT EXISTS fk_season_champion 
    FOREIGN KEY (champion_id) REFERENCES players(id) ON DELETE SET NULL;

-- ============================================================================
-- ÉTAPE 3: MIGRATIONS COLONNES MATCHES
-- ============================================================================

ALTER TABLE matches ADD COLUMN IF NOT EXISTS match_index INT NOT NULL DEFAULT 0;

-- Créer l'index unique (message_id, match_index) s'il n'existe pas
ALTER TABLE matches ADD UNIQUE KEY IF NOT EXISTS unique_message_match (message_id, match_index);

-- Supprimer l'ancien index simple message_id s'il existe
DROP INDEX IF EXISTS message_id ON matches;

-- ============================================================================
-- ÉTAPE 4: TABLE DE DÉDUPLICATION (Phase 1 Tâche 3)
-- ============================================================================

CREATE TABLE IF NOT EXISTS deduplication_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_player_id INT NOT NULL,
    target_player_id INT NOT NULL,
    source_player_name VARCHAR(255) NOT NULL,
    target_player_name VARCHAR(255) NOT NULL,
    merged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    merged_by VARCHAR(255) NULL,
    notes TEXT NULL,
    FOREIGN KEY (source_player_id) REFERENCES players(id) ON DELETE RESTRICT,
    FOREIGN KEY (target_player_id) REFERENCES players(id) ON DELETE RESTRICT,
    INDEX idx_merged_at (merged_at),
    INDEX idx_source_target (source_player_id, target_player_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- ============================================================================
-- ÉTAPE 5: TABLE DE LOG DE SAISONS (optionnel, pour audit)
-- ============================================================================

CREATE TABLE IF NOT EXISTS season_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    season_id INT NOT NULL,
    action VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details TEXT NULL,
    FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE,
    INDEX idx_season_date (season_id, created_at)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- ============================================================================
-- ÉTAPE 6: CORRECTION DONNÉES EXISTANTES
-- ============================================================================

-- Mettre à jour les tiers invalides en NR
UPDATE players SET tier_rank = 'NR' 
WHERE tier_rank NOT IN ('S+', 'S', 'A+', 'A', 'B+', 'B', 'C', 'NR');

-- Initialiser les ELO selon les tiers (si pas déjà fait)
UPDATE players SET elo = 2400 WHERE tier_rank = 'S+' AND elo < 2400;
UPDATE players SET elo = 2200 WHERE tier_rank = 'S' AND elo < 2200;
UPDATE players SET elo = 2000 WHERE tier_rank = 'A+' AND elo < 2000;
UPDATE players SET elo = 1800 WHERE tier_rank = 'A' AND elo < 1800;
UPDATE players SET elo = 1600 WHERE tier_rank = 'B+' AND elo < 1600;
UPDATE players SET elo = 1400 WHERE tier_rank = 'B' AND elo < 1400;
UPDATE players SET elo = 1200 WHERE tier_rank = 'C' AND elo < 1200;
UPDATE players SET elo = 1000 WHERE tier_rank = 'NR' AND elo < 1000;

-- ============================================================================
-- ÉTAPE 7: VÉRIFICATION INTÉGRITÉ
-- ============================================================================

-- Compter les joueurs par tier
SELECT 'Joueurs par tier:' as check_name;
SELECT tier_rank, COUNT(*) as count FROM players GROUP BY tier_rank;

-- Compter les saisons
SELECT 'Saisons:' as check_name;
SELECT id, name, start_date, end_date, is_active, champion_id FROM seasons;

-- Compter les matches
SELECT 'Total matches:' as check_name;
SELECT COUNT(*) as total_matches FROM matches;

-- ============================================================================
-- ÉTAPE 8: FINALISATION
-- ============================================================================

-- Mettre à jour la version en settings (optionnel)
INSERT INTO bot_settings (setting_key, setting_value) 
VALUES ('version', '2.0') 
ON DUPLICATE KEY UPDATE setting_value = '2.0';

-- Log de migration
INSERT INTO season_logs (season_id, action, details) 
SELECT id, 'MIGRATION_V2.0', 'Refonte majeure ELO, saisons, déduplication' 
FROM seasons WHERE is_active = 1 LIMIT 1;

-- ============================================================================
-- RÉSUMÉ DE LA MIGRATION
-- ============================================================================

/*
Migration complétée avec succès !

Changements:
✓ PLAYERS: Ajout region, tier_rank, elo, rank_manual
✓ SEASONS: Ajout end_date, champion_id, updated_at
✓ MATCHES: Ajout match_index, index unique (message_id, match_index)
✓ TABLE: deduplication_history créée
✓ TABLE: season_logs créée
✓ DONNÉES: Corrigées et validées

Pour annuler cette migration (si nécessaire):
  1. Restaurer depuis backup
  2. Ou supprimer les colonnes et tables ajoutées

Prochaines étapes:
1. Tester sur environnement staging
2. Vérifier les données après migration
3. Déployer le nouveau code bot
4. Mettre à jour la documentation
*/

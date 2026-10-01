-- Fresh schema already omits this legacy FK; upgrade only when present.
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() AND table_name='shots' AND constraint_name='shots_ibfk_1' AND constraint_type='FOREIGN KEY')>0, 'ALTER TABLE shots DROP FOREIGN KEY shots_ibfk_1', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

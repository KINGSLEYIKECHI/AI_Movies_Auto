-- Repeatable upgrade for databases predating the reference-job columns.
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='jobs' AND column_name='character_id')=0, 'ALTER TABLE jobs ADD COLUMN character_id VARCHAR(128) NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='jobs' AND column_name='location_id')=0, 'ALTER TABLE jobs ADD COLUMN location_id VARCHAR(128) NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='jobs' AND column_name='prop_id')=0, 'ALTER TABLE jobs ADD COLUMN prop_id VARCHAR(128) NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='jobs' AND column_name='prompt')=0, 'ALTER TABLE jobs ADD COLUMN prompt TEXT NULL', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
ALTER TABLE jobs MODIFY job_type ENUM('story','character_reference','location_reference','prop_reference','shot_image','shot_video','audio') NOT NULL;
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='jobs' AND column_name='character_id' AND referenced_table_name='characters')=0, 'ALTER TABLE jobs ADD CONSTRAINT fk_jobs_character_id FOREIGN KEY (character_id) REFERENCES characters(character_id) ON DELETE CASCADE', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='jobs' AND column_name='location_id' AND referenced_table_name='locations')=0, 'ALTER TABLE jobs ADD CONSTRAINT fk_jobs_location_id FOREIGN KEY (location_id) REFERENCES locations(location_id) ON DELETE CASCADE', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.key_column_usage WHERE table_schema=DATABASE() AND table_name='jobs' AND column_name='prop_id' AND referenced_table_name='props')=0, 'ALTER TABLE jobs ADD CONSTRAINT fk_jobs_prop_id FOREIGN KEY (prop_id) REFERENCES props(prop_id) ON DELETE CASCADE', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
SET @ddl = IF((SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='jobs' AND index_name='idx_jobs_type')=0, 'ALTER TABLE jobs ADD INDEX idx_jobs_type(job_type)', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

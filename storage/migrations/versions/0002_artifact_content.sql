-- Store new tool-output and transcript bodies in MySQL; retain legacy file metadata.
ALTER TABLE artifacts
  MODIFY COLUMN path TEXT NULL,
  MODIFY COLUMN path_key VARCHAR(64) NULL,
  ADD COLUMN content LONGTEXT NULL,
  ADD COLUMN line_count BIGINT NULL;

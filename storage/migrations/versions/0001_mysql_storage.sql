-- Frozen initial schema. Add a new revision for future schema changes.

CREATE TABLE projects (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	workdir TEXT NOT NULL, 
	path_key VARCHAR(64) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	archived BOOL NOT NULL, 
	CONSTRAINT pk_projects PRIMARY KEY (id), 
	CONSTRAINT uq_projects_path_key UNIQUE (path_key)
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE project_tasks (
	project_id VARCHAR(32) NOT NULL, 
	task_id VARCHAR(32) NOT NULL, 
	subject TEXT NOT NULL, 
	description LONGTEXT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	owner_session_id VARCHAR(64), 
	updated_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_project_tasks PRIMARY KEY (project_id, task_id), 
	CONSTRAINT fk_project_tasks_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE sessions (
	id VARCHAR(32) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	title_custom BOOL NOT NULL, 
	workdir TEXT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	latest_checkpoint_id VARCHAR(32), 
	current_run_id VARCHAR(32), 
	context_hash VARCHAR(64), 
	version INTEGER NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_sessions PRIMARY KEY (id), 
	CONSTRAINT fk_sessions_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE INDEX ix_sessions_project_updated ON sessions (project_id, updated_at, id);

CREATE TABLE agent_runs (
	id VARCHAR(32) NOT NULL, 
	session_id VARCHAR(32) NOT NULL, 
	request LONGTEXT NOT NULL, 
	model VARCHAR(255) NOT NULL, 
	workdir TEXT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	failure JSON, 
	attempt INTEGER NOT NULL, 
	started_at DATETIME(6) NOT NULL, 
	ended_at DATETIME(6), 
	CONSTRAINT pk_agent_runs PRIMARY KEY (id), 
	CONSTRAINT fk_agent_runs_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE INDEX ix_agent_runs_session_started ON agent_runs (session_id, started_at);

CREATE TABLE task_dependencies (
	project_id VARCHAR(32) NOT NULL, 
	task_id VARCHAR(32) NOT NULL, 
	depends_on VARCHAR(32) NOT NULL, 
	CONSTRAINT pk_task_dependencies PRIMARY KEY (project_id, task_id, depends_on), 
	CONSTRAINT fk_dependencies_task FOREIGN KEY(project_id, task_id) REFERENCES project_tasks (project_id, task_id) ON DELETE CASCADE, 
	CONSTRAINT fk_dependencies_parent FOREIGN KEY(project_id, depends_on) REFERENCES project_tasks (project_id, task_id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE artifacts (
	id VARCHAR(32) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	session_id VARCHAR(32), 
	run_id VARCHAR(32), 
	kind VARCHAR(32) NOT NULL, 
	path TEXT NOT NULL, 
	path_key VARCHAR(64) NOT NULL, 
	size_bytes BIGINT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_artifacts PRIMARY KEY (id), 
	CONSTRAINT uq_artifacts_project_id UNIQUE (project_id, path_key), 
	CONSTRAINT fk_artifacts_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_artifacts_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE SET NULL, 
	CONSTRAINT fk_artifacts_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE chat_messages (
	id VARCHAR(32) NOT NULL, 
	session_id VARCHAR(32) NOT NULL, 
	run_id VARCHAR(32), 
	message_key VARCHAR(255) NOT NULL, 
	seq BIGINT NOT NULL, 
	`role` VARCHAR(16) NOT NULL, 
	scope VARCHAR(20) NOT NULL, 
	body LONGTEXT NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_chat_messages PRIMARY KEY (id), 
	CONSTRAINT uq_chat_messages_session_id UNIQUE (session_id, message_key), 
	CONSTRAINT fk_chat_messages_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE, 
	CONSTRAINT fk_chat_messages_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE INDEX ix_chat_messages_session_seq ON chat_messages (session_id, seq);

CREATE TABLE context_checkpoints (
	id VARCHAR(32) NOT NULL, 
	session_id VARCHAR(32) NOT NULL, 
	run_id VARCHAR(32), 
	version INTEGER NOT NULL, 
	messages_json JSON NOT NULL, 
	schema_version INTEGER NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_context_checkpoints PRIMARY KEY (id), 
	CONSTRAINT uq_context_checkpoints_session_id UNIQUE (session_id, version), 
	CONSTRAINT fk_context_checkpoints_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE, 
	CONSTRAINT fk_context_checkpoints_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE memories (
	id VARCHAR(32) NOT NULL, 
	project_id VARCHAR(32) NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	name TEXT NOT NULL, 
	type VARCHAR(24) NOT NULL, 
	description LONGTEXT NOT NULL, 
	body LONGTEXT NOT NULL, 
	document LONGTEXT NOT NULL, 
	source_session_id VARCHAR(32), 
	source_run_id VARCHAR(32), 
	version INTEGER NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_memories PRIMARY KEY (id), 
	CONSTRAINT uq_memories_project_id UNIQUE (project_id, filename), 
	CONSTRAINT fk_memories_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	CONSTRAINT fk_memories_source_session_id_sessions FOREIGN KEY(source_session_id) REFERENCES sessions (id) ON DELETE SET NULL, 
	CONSTRAINT fk_memories_source_run_id_agent_runs FOREIGN KEY(source_run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE run_usage (
	run_id VARCHAR(32) NOT NULL, 
	purpose VARCHAR(20) NOT NULL, 
	calls BIGINT NOT NULL, 
	input_tokens BIGINT NOT NULL, 
	output_tokens BIGINT NOT NULL, 
	cache_read_tokens BIGINT NOT NULL, 
	cache_creation_tokens BIGINT NOT NULL, 
	unknown_calls BIGINT NOT NULL, 
	pending_calls BIGINT NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_run_usage PRIMARY KEY (run_id, purpose), 
	CONSTRAINT fk_run_usage_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE session_events (
	id VARCHAR(32) NOT NULL, 
	session_id VARCHAR(32) NOT NULL, 
	run_id VARCHAR(32), 
	seq BIGINT NOT NULL, 
	event_type VARCHAR(40) NOT NULL, 
	payload JSON NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	CONSTRAINT pk_session_events PRIMARY KEY (id), 
	CONSTRAINT uq_session_events_session_id UNIQUE (session_id, seq), 
	CONSTRAINT fk_session_events_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE, 
	CONSTRAINT fk_session_events_run_id_agent_runs FOREIGN KEY(run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
)ENGINE=InnoDB CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci;

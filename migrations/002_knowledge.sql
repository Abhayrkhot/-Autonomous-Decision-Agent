CREATE TABLE knowledge_chunks (
    owner_id text NOT NULL,
    source_id text NOT NULL,
    chunk_number integer NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    text text NOT NULL,
    embedding double precision[] NOT NULL,
    PRIMARY KEY (owner_id, source_id, chunk_number)
);
